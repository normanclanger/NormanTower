
import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3
import psycopg


logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_NAME = "norman-tower-tickets"
URL_EXPIRY_SECONDS = 900

DEFAULT_LIMIT = 4
MAX_LIMIT = 20

UK_TIMEZONE = ZoneInfo("Europe/London")

s3 = boto3.client("s3")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-store",
        },
        "body": "" if body is None else json.dumps(body),
    }


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be in YYYY-MM-DD format")

    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("date is not a valid calendar date")


def parse_integer(value, name, default, minimum, maximum=None):
    if value is None:
        return default

    if not isinstance(value, str) or not re.fullmatch(r"\d+", value):
        raise ValueError(f"{name} must be a whole number")

    number = int(value)

    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")

    if maximum is not None and number > maximum:
        raise ValueError(f"{name} cannot be greater than {maximum}")

    return number


def image_url(key):
    if not key:
        return None

    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=URL_EXPIRY_SECONDS,
    )


def lambda_handler(event, context):
    method = (
        (event.get("requestContext") or {}).get("http", {}).get("method")
        or event.get("httpMethod")
    )

    if method == "OPTIONS":
        return response(204, None)

    if method and method != "GET":
        return response(405, {"error": "Method not allowed"})

    try:
        params = event.get("queryStringParameters") or {}

        # Default to today's date in UK local time.
        requested_date = params.get("date")

        if requested_date is None:
            selected_date = datetime.now(UK_TIMEZONE).date()
        else:
            selected_date = parse_date(requested_date)

        limit = parse_integer(
            params.get("limit"),
            "limit",
            DEFAULT_LIMIT,
            1,
            MAX_LIMIT,
        )

        offset = parse_integer(
            params.get("offset"),
            "offset",
            0,
            0,
        )

        # Convert the selected UK calendar day into UTC boundaries.
        # This also handles 23/25-hour days during clock changes.
        start_local = datetime.combine(
            selected_date,
            time.min,
            tzinfo=UK_TIMEZONE,
        )

        end_local = datetime.combine(
            selected_date + timedelta(days=1),
            time.min,
            tzinfo=UK_TIMEZONE,
        )

        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        sql = """
            SELECT
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
            FROM public.people_count_results
            WHERE captured_at >= %s
              AND captured_at < %s
            ORDER BY captured_at DESC
            LIMIT %s
            OFFSET %s
        """

        with psycopg.connect(
            os.environ["DATABASE_URL"],
            connect_timeout=10,
        ) as conn:
            with conn.cursor() as cur:

                # Count all original images captured on the selected day.
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM public.people_count_results
                    WHERE captured_at >= %s
                      AND captured_at < %s
                    """,
                    (start_utc, end_utc),
                )

                daily_image_count = cur.fetchone()[0]

                # Fetch one extra row to determine whether another batch exists.
                cur.execute(
                    sql,
                    (start_utc, end_utc, limit + 1, offset),
                )

                rows = cur.fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]

        results = []

        for row in rows:
            (
                captured_at,
                previous_count,
                previous_seconds,
                previous_error,
                close_pairs_count,
                close_pairs_seconds,
                close_pairs_error,
                original_image_key,
                previous_image_key,
                close_pairs_image_key,
            ) = row

            results.append({
                "captured_at": captured_at.isoformat(),

                "previous_count": previous_count,
                "previous_seconds": (
                    float(previous_seconds)
                    if previous_seconds is not None else None
                ),
                "previous_error": previous_error,

                "close_pairs_count": close_pairs_count,
                "close_pairs_seconds": (
                    float(close_pairs_seconds)
                    if close_pairs_seconds is not None else None
                ),
                "close_pairs_error": close_pairs_error,

                "images": {
                    "original": image_url(original_image_key),
                    "previous": image_url(previous_image_key),
                    "close_pairs": image_url(close_pairs_image_key),
                },
            })

        return response(
            200,
            {
                "date": selected_date.isoformat(),
                "daily_image_count": daily_image_count,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "results": results,
            },
        )

    except ValueError as exc:
        return response(400, {"error": str(exc)})

    except Exception:
        logger.exception("Unable to retrieve people-count results")
        return response(
            500,
            {"error": "Unable to retrieve results"},
        )
