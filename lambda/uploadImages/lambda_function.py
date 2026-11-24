
import base64
import binascii
import hmac
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
import psycopg


logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_NAME = "norman-tower-tickets"
S3_PREFIX = "people-counts/"
RETENTION_DAYS = 7
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024

s3 = boto3.client("s3")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def parse_request(event):
    body = event.get("body")

    if not body:
        raise ValueError("Request body is missing")

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if len(body.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Request is too large")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Request body must be valid JSON")

    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    return data


def validate_captured_at(value):
    if not isinstance(value, str):
        raise ValueError("captured_at must be an ISO timestamp")

    try:
        captured_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("captured_at is not a valid ISO timestamp")

    if captured_at.tzinfo is None:
        raise ValueError("captured_at must include a timezone")

    return captured_at.astimezone(timezone.utc)


def validate_filename(value):
    if not isinstance(value, str):
        raise ValueError("original_filename is required")

    # Use only the filename stem when constructing S3 keys.
    stem = os.path.splitext(os.path.basename(value))[0]

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stem):
        raise ValueError("original_filename contains invalid characters")

    return stem


def decode_image(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"Image '{name}' is missing")

    try:
        image_data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"Image '{name}' is not valid base64")

    if len(image_data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image '{name}' is too large")

    if not image_data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"Image '{name}' is not a JPEG")

    return image_data


def validate_count(value, name):
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")

    return value


def validate_seconds(value, name):
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null")

    if value < 0:
        raise ValueError(f"{name} cannot be negative")

    return value


def upload_images(stem, images):
    suffixes = {
        "original": "original",
        "previous": "previous",
        "close_pairs": "close_pairs",
    }

    keys = {}

    for image_name, suffix in suffixes.items():
        key = f"{S3_PREFIX}{stem}_{suffix}.jpg"

        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=images[image_name],
            ContentType="image/jpeg",
        )

        keys[image_name] = key

        logger.info("Uploaded s3://%s/%s", BUCKET_NAME, key)

    return keys


def save_result(data, captured_at, keys):
    sql = """
        INSERT INTO public.people_count_results (
            original_filename,
            captured_at,
            previous_count,
            previous_seconds,
            previous_error,
            close_pairs_count,
            close_pairs_seconds,
            close_pairs_error,
            original_image_key,
            previous_image_key,
            close_pairs_image_key
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (captured_at) DO UPDATE SET
            original_filename = EXCLUDED.original_filename,
            previous_count = EXCLUDED.previous_count,
            previous_seconds = EXCLUDED.previous_seconds,
            previous_error = EXCLUDED.previous_error,
            close_pairs_count = EXCLUDED.close_pairs_count,
            close_pairs_seconds = EXCLUDED.close_pairs_seconds,
            close_pairs_error = EXCLUDED.close_pairs_error,
            original_image_key = EXCLUDED.original_image_key,
            previous_image_key = EXCLUDED.previous_image_key,
            close_pairs_image_key = EXCLUDED.close_pairs_image_key
    """

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    data["original_filename"],
                    captured_at,
                    data["previous_count"],
                    data["previous_seconds"],
                    data["previous_error"],
                    data["close_pairs_count"],
                    data["close_pairs_seconds"],
                    data["close_pairs_error"],
                    keys["original"],
                    keys["previous"],
                    keys["close_pairs"],
                ),
            )

        # Commit the new result before attempting retention cleanup.
        conn.commit()


def cleanup_old_results():
    """Delete expired images first, then their database records."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_image_key,
                       previous_image_key, close_pairs_image_key
                FROM public.people_count_results
                WHERE captured_at < %s
                """,
                (cutoff,),
            )
            rows = cur.fetchall()

        if not rows:
            return 0

        objects = []

        for row in rows:
            for key in row[1:]:
                if key:
                    objects.append({"Key": key})

        # S3 DeleteObjects accepts at most 1000 keys per request.
        for start in range(0, len(objects), 1000):
            batch = objects[start:start + 1000]

            result = s3.delete_objects(
                Bucket=BUCKET_NAME,
                Delete={"Objects": batch, "Quiet": True},
            )

            if result.get("Errors"):
                raise RuntimeError(
                    f"S3 retention deletion failed: {result['Errors']}"
                )

        ids = [row[0] for row in rows]

        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM public.people_count_results
                WHERE id = ANY(%s)
                """,
                (ids,),
            )

        logger.info("Removed %s expired results", len(rows))

    return len(rows)


def lambda_handler(event, context):
    expected_token = os.environ.get("UPLOAD_TOKEN")

    if not expected_token:
        logger.error("UPLOAD_TOKEN environment variable is not configured")
        return response(500, {"error": "Server configuration error"})

    headers = {
        key.lower(): value
        for key, value in (event.get("headers") or {}).items()
    }

    supplied_token = headers.get("x-upload-token", "")

    if not hmac.compare_digest(supplied_token, expected_token):
        return response(401, {"error": "Unauthorised"})

    try:
        data = parse_request(event)

        required = ("original_filename", "captured_at", "images")
        for field in required:
            if field not in data:
                raise ValueError(f"Missing field: {field}")

        captured_at = validate_captured_at(data["captured_at"])
        stem = validate_filename(data["original_filename"])

        data["previous_count"] = validate_count(
            data.get("previous_count"), "previous_count"
        )
        data["close_pairs_count"] = validate_count(
            data.get("close_pairs_count"), "close_pairs_count"
        )
        data["previous_seconds"] = validate_seconds(
            data.get("previous_seconds"), "previous_seconds"
        )
        data["close_pairs_seconds"] = validate_seconds(
            data.get("close_pairs_seconds"), "close_pairs_seconds"
        )

        for field in ("previous_error", "close_pairs_error"):
            if data.get(field) is not None and not isinstance(data[field], str):
                raise ValueError(f"{field} must be a string or null")

        if not isinstance(data["images"], dict):
            raise ValueError("images must be a JSON object")

        images = {
            name: decode_image(data["images"].get(name), name)
            for name in ("original", "previous", "close_pairs")
        }

        keys = upload_images(stem, images)
        save_result(data, captured_at, keys)

        # Retention failures should not invalidate a successful upload.
        try:
            deleted = cleanup_old_results()
        except Exception:
            logger.exception("Retention cleanup failed")
            deleted = None

        return response(
            200,
            {
                "success": True,
                "original_filename": data["original_filename"],
                "captured_at": captured_at.isoformat(),
                "images": keys,
                "expired_results_deleted": deleted,
            },
        )

    except ValueError as exc:
        logger.warning("Invalid upload request: %s", exc)
        return response(400, {"error": str(exc)})

    except Exception:
        logger.exception("Image upload failed")
        return response(500, {"error": "Image upload failed"})
