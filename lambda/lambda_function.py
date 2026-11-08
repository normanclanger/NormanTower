import os
import psycopg
import json
import base64
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


# Load the registration configuration.

CONFIG_PATH = Path(__file__).parent / "config" / "registration.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as file:
    CONFIG = __import__("yaml").safe_load(file)


def get_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not configured")

    return psycopg.connect(database_url)

def response(status_code, data):
    """Create an API Gateway HTTP response."""

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(data)
    }


def get_catalog():
    """Build a lookup of all available items and their prices."""

    catalog = {}

    for category in CONFIG["categories"]:
        for item in category["items"]:

            item_id = item["id"]

            if item_id in catalog:
                raise ValueError(
                    f"Duplicate item ID in configuration: {item_id}"
                )

            price = Decimal(str(item["price"]))

            # Prices must be non-negative and have no more than
            # two decimal places.

            if price < 0 or price != price.quantize(Decimal("0.01")):
                raise ValueError(
                    f"Invalid price for item: {item_id}"
                )

            catalog[item_id] = {
                "name": item["name"],
                "price_pence": int(price * 100)
            }

    return catalog


def validate_request(data):
    """Validate a registration submission and calculate its total."""

    if not isinstance(data, dict):
        raise ValueError("The request must contain a JSON object.")

    customer = data.get("customer")

    if not isinstance(customer, dict):
        raise ValueError("Customer details are missing.")

    name = customer.get("name")
    email = customer.get("email")
    participant_type = customer.get("participant_type")
    home_tower = customer.get("home_tower", "")

    if not isinstance(name, str) or not name.strip():
        raise ValueError("Please provide your name.")

    if not isinstance(email, str) or not email.strip():
        raise ValueError("Please provide your email address.")

    email = email.strip()

    if (
        "@" not in email
        or email.startswith("@")
        or email.endswith("@")
        or " " in email
    ):
        raise ValueError("Please provide a valid email address.")

    if participant_type not in ("competitor", "visitor"):
        raise ValueError("Invalid participant type.")

    if not isinstance(home_tower, str):
        raise ValueError("Invalid home tower.")

    items = data.get("items")

    if not isinstance(items, list):
        raise ValueError("The items list is missing or invalid.")

    catalog = get_catalog()

    # Check the advance-purchase deadline.
    # Registration itself remains open after this deadline.

    cutoff_text = CONFIG["event"].get("advance_purchase_cutoff")

    if cutoff_text:
        cutoff = datetime.fromisoformat(cutoff_text)

        if cutoff.tzinfo is None:
            raise ValueError(
                "The advance-purchase deadline must include a timezone."
            )

        if datetime.now(timezone.utc) > cutoff:
            if items:
                raise ValueError(
                    "Advance purchases have now closed. "
                    "You can still register without purchasing items."
                )

    total_pence = 0
    validated_items = []
    seen_item_ids = set()

    for submitted_item in items:

        if not isinstance(submitted_item, dict):
            raise ValueError("Invalid item in submission.")

        item_id = submitted_item.get("item_id")
        quantity = submitted_item.get("quantity")

        if not isinstance(item_id, str) or item_id not in catalog:
            raise ValueError("An unrecognised item was submitted.")

        if item_id in seen_item_ids:
            raise ValueError("An item was submitted more than once.")

        seen_item_ids.add(item_id)

        # bool is a subclass of int in Python, so check it explicitly.

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ValueError(
                f"Invalid quantity for {catalog[item_id]['name']}."
            )

        price_pence = catalog[item_id]["price_pence"]
        line_total_pence = quantity * price_pence

        total_pence += line_total_pence

        validated_items.append({
            "item_id": item_id,
            "name": catalog[item_id]["name"],
            "quantity": quantity,
            "unit_price_pence": price_pence,
            "line_total_pence": line_total_pence
        })

    return {
        "event": CONFIG["event"]["name"],
        "customer": {
            "name": name.strip(),
            "email": email,
            "participant_type": participant_type,
            "home_tower": home_tower.strip()
        },
        "items": validated_items,
        "total_pence": total_pence,
        "total": f"{Decimal(total_pence) / 100:.2f}"
    }





def lambda_handler2(event, context):

    try:

        # API Gateway sends the submitted JSON in event["body"].

        body = event.get("body")

        if not body:
            return response(400, {
                "status": "error",
                "error": "No registration data was received."
            })

        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        data = json.loads(body)

        registration = validate_request(data)

        return response(200, {
            "status": "validated",
            "message": "Registration details validated successfully.",
            "registration": registration
        })

    except (ValueError, json.JSONDecodeError, InvalidOperation) as error:

        return response(400, {
            "status": "error",
            "error": str(error)
        })

    except Exception:

        # Log unexpected errors in CloudWatch, without exposing
        # internal details to the person submitting the form.

        print("Unexpected error processing registration:")
        import traceback
        traceback.print_exc()

        return response(500, {
            "status": "error",
            "error": "An unexpected error occurred. Please try again."
        })



def lambda_handler(event, context):

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]

        return response(200, {
            "status": "connected",
            "database_version": version
        })

    except Exception as e:
        print(f"Database connection error: {e}")
        return response(500, {
            "error": "Database connection failed"
        })