import os
import psycopg
import json
import base64
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets
from sumup_client import (
    create_checkout,
    get_checkout,
    get_webhook_url,
    SumUpError
)
import boto3
from botocore.exceptions import ClientError

s3_client = boto3.client("s3")
TICKETS_BUCKET = os.environ.get("TICKETS_BUCKET")


TICKET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"

TICKET_GENERATOR_FUNCTION = os.environ.get(
    "TICKET_GENERATOR_FUNCTION"
)

lambda_client = boto3.client("lambda")

# Load the registration configuration.

CONFIG_PATH = Path(__file__).parent / "config" / "registration.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as file:
    CONFIG = __import__("yaml").safe_load(file)


def get_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not configured")

    return psycopg.connect(database_url)
    
    
    

def save_registration(registration):

    total_pence = registration["total_pence"]
    confirmation_token = secrets.token_urlsafe(32)

    payment_status = (
        "NOT_REQUIRED" if total_pence == 0 else "PENDING"
    )

    item_ticket_status = (
        "ISSUED" if total_pence == 0 else "PENDING_PAYMENT"
    )

    created_tickets = []

    with get_connection() as conn:

        with conn.cursor() as cur:

            # -----------------------------------------
            # 1. Create the registration
            # -----------------------------------------

            cur.execute(
                """
                INSERT INTO registrations (
                    event_name,
                    first_name,
                    surname,
                    email,
                    participant_type,
                    home_tower,
                    total_pence,
                    payment_status,
                    confirmation_token,
                    is_test
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    registration["event"],
                    registration["customer"]["first_name"],
                    registration["customer"]["surname"],
                    registration["customer"]["email"],
                    registration["customer"]["participant_type"],
                    registration["customer"]["home_tower"],
                    total_pence,
                    payment_status,
                    confirmation_token,
                    TEST_MODE
                )
            )

            registration_id = cur.fetchone()[0]

            # -----------------------------------------
            # 2. Create the admission ticket
            # -----------------------------------------

            ticket_reference = generate_ticket_reference()

            cur.execute(
                """
                INSERT INTO tickets (
                    registration_id,
                    ticket_reference,
                    ticket_type,
                    ticket_status,
                    issued_at
                )
                VALUES (
                    %s, %s, 'ADMISSION', %s,
                    CASE WHEN %s THEN NOW() ELSE NULL END
                )
                RETURNING ticket_id
                """,
                (
                    registration_id,
                    ticket_reference,
                    item_ticket_status,
                    item_ticket_status == "ISSUED"
                )
            )

            ticket_id = cur.fetchone()[0]

            created_tickets.append({
                "ticket_id": str(ticket_id),
                "ticket_reference": ticket_reference,
                "ticket_type": "ADMISSION"
            })

            # -----------------------------------------
            # 3. Create registration items and vouchers
            # -----------------------------------------

            for item in registration["items"]:

                cur.execute(
                    """
                    INSERT INTO registration_items (
                        registration_id,
                        item_id,
                        item_name,
                        quantity,
                        unit_price_pence,
                        line_total_pence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        registration_id,
                        item["item_id"],
                        item["name"],
                        item["quantity"],
                        item["unit_price_pence"],
                        item["line_total_pence"]
                    )
                )

                registration_item_id = cur.fetchone()[0]

                # One voucher for each individual quantity

                for _ in range(item["quantity"]):

                    ticket_reference = generate_ticket_reference()

                    cur.execute(
                        """
                        INSERT INTO tickets (
                            registration_id,
                            registration_item_id,
                            ticket_reference,
                            ticket_type,
                            ticket_status,
                            issued_at
                        )
                        VALUES (
                            %s, %s, %s, 'ITEM', %s,
                            CASE WHEN %s THEN NOW() ELSE NULL END
                        )
                        RETURNING ticket_id
                        """,
                        (
                            registration_id,
                            registration_item_id,
                            ticket_reference,
                            item_ticket_status,
                            item_ticket_status == "ISSUED"
                        )
                    )

                    ticket_id = cur.fetchone()[0]

                    created_tickets.append({
                        "ticket_id": str(ticket_id),
                        "ticket_reference": ticket_reference,
                        "ticket_type": "ITEM",
                        "item_id": item["item_id"]
                    })

    # The connection context commits automatically if successful.
    # If anything fails, it rolls back the transaction.

    return {
        "registration_id": registration_id,
        "confirmation_token": confirmation_token,
        "payment_status": payment_status,
        "total_pence": total_pence,
        "tickets": created_tickets
    }

    
def save_sumup_checkout_id(registration_id, checkout_id):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE registrations
                SET sumup_checkout_id = %s
                WHERE id = %s
                """,
                (checkout_id, registration_id)
            )

            if cur.rowcount != 1:
                raise ValueError(
                    "Unable to associate SumUp checkout with registration"
                )


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
    
    



def generate_ticket_reference():

    code = "".join(
        secrets.choice(TICKET_ALPHABET)
        for _ in range(8)
    )

    return f"NT-{code}"


def validate_request(data):
    """Validate a registration submission and calculate its total."""

    if not isinstance(data, dict):
        raise ValueError("The request must contain a JSON object.")

    customer = data.get("customer")

    if not isinstance(customer, dict):
        raise ValueError("Customer details are missing.")

    first_name = customer.get("first_name")
    surname = customer.get("surname")
    email = customer.get("email")
    participant_type = customer.get("participant_type")
    home_tower = customer.get("home_tower", "")

    if not isinstance(first_name, str) or not first_name.strip():
        raise ValueError("Please provide your first name.")

    if not isinstance(surname, str) or not surname.strip():
        raise ValueError("Please provide your surname.")

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
            "first_name": first_name.strip(),
            "surname": surname.strip(),
            "email": email,
            "participant_type": participant_type,
            "home_tower": home_tower.strip()
        },
        "items": validated_items,
        "total_pence": total_pence,
        "total": f"{Decimal(total_pence) / 100:.2f}"
    }


def handle_test_checkout():

    try:
        result = create_checkout(
            amount=1.00,
            description="AWS Lambda sandbox test",
            return_url=get_webhook_url()
        )

        return response(200, {
            "status": "success",
            "checkout_id": result.get("id"),
            "checkout_reference": result.get("checkout_reference"),
            "checkout_url": result.get("hosted_checkout_url"),
            "checkout_status": result.get("status")
        })

    except SumUpError as error:

        print(f"SumUp checkout error: {error}")

        return response(500, {
            "status": "error",
            "error": "Unable to create SumUp checkout."
        })


def handle_sumup_webhook(event):

    # Check the secret token in the URL.
    path = event.get("rawPath", "")
    token = path.rsplit("/", 1)[-1]

    expected_token = os.getenv("SUMUP_WEBHOOK_TOKEN")

    if not expected_token or not secrets.compare_digest(
        token, expected_token
    ):
        print("Rejected webhook: invalid token")
        return response(404, {
            "status": "error",
            "error": "Not found"
        })

    # Read the notification body.
    body = event.get("body")

    if not body:
        print("Invalid webhook payload: empty body")
        return response(400, {
            "status": "error",
            "error": "Invalid webhook payload"
        })

    try:
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        data = json.loads(body)

    except (ValueError, UnicodeDecodeError):
        print("Invalid webhook payload: unable to decode JSON")
        return response(400, {
            "status": "error",
            "error": "Invalid webhook payload"
        })

    if not isinstance(data, dict):
        print("Invalid webhook payload")
        return response(400, {
            "status": "error",
            "error": "Invalid webhook payload"
        })

    event_type = data.get("event_type")
    checkout_id = data.get("id")

    print("\nWebhook received")
    print("----------------")
    print(f"Event type:  {event_type}")
    print(f"Checkout ID: {checkout_id}")

    if event_type != "CHECKOUT_STATUS_CHANGED":
        print("Ignoring unknown event type")
        return response(200, {"status": "ignored"})

    if not checkout_id:
        print("Missing checkout ID")
        return response(400, {
            "status": "error",
            "error": "Missing checkout ID"
        })


    try:
        checkout = get_checkout(checkout_id)

        status = checkout.get("status")
        checkout_reference = checkout.get("checkout_reference")
        amount = checkout.get("amount")
        currency = checkout.get("currency")

        print("\nVerified checkout with SumUp")
        print("----------------------------")
        print(f"Status:      {status}")
        print(f"Reference:   {checkout_reference}")
        print(f"Amount:      {amount}")
        print(f"Currency:    {currency}")
        print(f"Transaction: {checkout.get('transaction_code')}")

        # Only process successful payments.
        if status != "PAID":
            print(f"\nPayment not yet confirmed: {status}")
            return response(200, {
                "status": "not_paid",
                "checkout_id": checkout_id,
                "checkout_status": status
            })

        print("\n*** PAYMENT CONFIRMED ***")

        # Find the registration and lock it against concurrent webhook calls.
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    SELECT id, total_pence, payment_status, confirmation_token
                    FROM registrations
                    WHERE sumup_checkout_id = %s
                    FOR UPDATE
                """, (checkout_id,))

                registration = cur.fetchone()

                if not registration:
                    print("ERROR: No registration found for checkout")
                    return response(200, {
                        "status": "registration_not_found"
                    })

                registration_id, total_pence, payment_status, confirmation_token = registration

                # Check the checkout reference.
                expected_reference = f"registration-{confirmation_token}"

                if checkout_reference != expected_reference:
                    print("ERROR: Checkout reference mismatch")
                    print(f"Expected: {expected_reference}")
                    print(f"Received: {checkout_reference}")
                    return response(200, {
                        "status": "reference_mismatch"
                    })

                # Check currency and amount.
                if currency != "GBP":
                    print(f"ERROR: Unexpected currency: {currency}")
                    return response(200, {
                        "status": "currency_mismatch"
                    })

                try:
                    paid_amount = Decimal(str(amount))
                    expected_amount = (
                        Decimal(total_pence) / Decimal("100")
                    )
                except (InvalidOperation, TypeError, ValueError):
                    print("ERROR: Invalid checkout amount")
                    return response(200, {
                        "status": "invalid_amount"
                    })

                if paid_amount != expected_amount:
                    print(
                        f"ERROR: Amount mismatch. "
                        f"Expected £{expected_amount}, received £{paid_amount}"
                    )
                    return response(200, {
                        "status": "amount_mismatch"
                    })

                # Make repeated webhook notifications harmless.
                if payment_status == "PAID":
                    print(
                        f"Registration {registration_id} "
                        "has already been marked PAID"
                    )
                    return response(200, {
                        "status": "already_processed",
                        "registration_id": registration_id
                    })

                if payment_status != "PENDING":
                    print(
                        f"ERROR: Unexpected registration payment status: "
                        f"{payment_status}"
                    )
                    return response(200, {
                        "status": "unexpected_payment_status"
                    })

                # Mark the registration as paid.
                cur.execute("""
                    UPDATE registrations
                    SET payment_status = 'PAID'
                    WHERE id = %s
                """, (registration_id,))

                # Issue the tickets that were waiting for payment.
                cur.execute("""
                    UPDATE tickets
                    SET ticket_status = 'ISSUED',
                        issued_at = NOW()
                    WHERE registration_id = %s
                      AND ticket_status = 'PENDING_PAYMENT'
                """, (registration_id,))

                tickets_issued = cur.rowcount

                print("\nDatabase updated successfully")
                print("-----------------------------")
                print(f"Registration: {registration_id}")
                print("Payment status: PAID")
                print(f"Tickets issued: {tickets_issued}")

        # The transaction has committed successfully at this point.
        return response(200, {
            "status": "processed",
            "registration_id": registration_id,
            "tickets_issued": tickets_issued
        })

    except SumUpError as error:
        print(f"Unable to verify checkout: {error}")

        return response(500, {
            "status": "error",
            "error": "Unable to verify checkout"
        })

# Return the fulfilment status

def handle_fulfilment_status(event):

    params = event.get("queryStringParameters") or {}
    token = params.get("token")

    if not token:
        return response(400, {
            "status": "error",
            "error": "Confirmation token is required."
        })

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT fulfilment_status
                FROM registrations
                WHERE confirmation_token = %s
                """,
                (token,)
            )

            row = cur.fetchone()

    if not row:
        return response(404, {
            "status": "error",
            "error": "Registration not found."
        })

    return response(200, {
        "status": row[0]
    })



def handle_start_fulfilment(event):

    # Read the confirmation token from the request body.
    body = event.get("body")

    if not body:
        return response(400, {
            "status": "error",
            "error": "Request body is required."
        })

    try:
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        data = json.loads(body)

    except (ValueError, UnicodeDecodeError):
        return response(400, {
            "status": "error",
            "error": "Invalid request body."
        })

    if not isinstance(data, dict):
        return response(400, {
            "status": "error",
            "error": "Invalid request body."
        })

    token = data.get("token")

    if not token:
        return response(400, {
            "status": "error",
            "error": "Confirmation token is required."
        })

    # Claim fulfilment in a database transaction.
    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id, payment_status, fulfilment_status
                FROM registrations
                WHERE confirmation_token = %s
                FOR UPDATE
                """,
                (token,)
            )

            row = cur.fetchone()

            if not row:
                return response(404, {
                    "status": "error",
                    "error": "Registration not found."
                })

            registration_id, payment_status, fulfilment_status = row

            # Payment must be complete before fulfilment starts.
            if payment_status not in ("NOT_REQUIRED", "PAID"):
                return response(409, {
                    "status": "error",
                    "error": "Payment has not been confirmed.",
                    "payment_status": payment_status
                })

            # Do not start fulfilment twice.
            if fulfilment_status != "NOT_STARTED":
                return response(200, {
                    "status": fulfilment_status,
                    "message": "Fulfilment has already been started."
                })

            # Claim the registration for fulfilment.
            cur.execute(
                """
                UPDATE registrations
                SET fulfilment_status = 'PROCESSING'
                WHERE id = %s
                """,
                (registration_id,)
            )

    # The database transaction has committed here.

    # Invoke the ticket-generation Lambda asynchronously.
    try:

        if not TICKET_GENERATOR_FUNCTION:
            raise RuntimeError(
                "TICKET_GENERATOR_FUNCTION is not configured."
            )

        payload = {
           "confirmation_token": token
        }

        lambda_client.invoke(
            FunctionName=TICKET_GENERATOR_FUNCTION,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8")
        )

    except Exception as error:

        print(
            f"Unable to invoke ticket generator for "
            f"registration {registration_id}: {error}"
        )

        with get_connection() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    UPDATE registrations
                    SET fulfilment_status = 'FAILED'
                    WHERE id = %s
                      AND fulfilment_status = 'PROCESSING'
                    """,
                    (registration_id,)
                )

        return response(500, {
            "status": "error",
            "error": "Unable to start ticket generation."
        })

    return response(202, {
        "status": "PROCESSING",
        "registration_id": registration_id,
        "message": "Ticket fulfilment has started."
    })



def handle_ticket_download(event):

    params = event.get("queryStringParameters") or {}
    token = params.get("token")

    if not token:
        return response(400, {
            "status": "error",
            "error": "Confirmation token is required."
        })

    if not TICKETS_BUCKET:
        print("ERROR: TICKETS_BUCKET is not configured")
        return response(500, {
            "status": "error",
            "error": "Ticket download is temporarily unavailable."
        })

    # Look up the registration and check that ticket fulfilment is complete.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fulfilment_status
                FROM registrations
                WHERE confirmation_token = %s
                """,
                (token,)
            )
            row = cur.fetchone()

    if not row:
        return response(404, {
            "status": "error",
            "error": "Registration not found."
        })

    if row[0] != "COMPLETE":
        return response(409, {
            "status": "error",
            "error": "Your tickets are not yet ready for download."
        })

    # Retrieve the PDF from the private S3 bucket.
    s3_key = f"tickets/{token}.pdf"

    try:
        result = s3_client.get_object(
            Bucket=TICKETS_BUCKET,
            Key=s3_key
        )

        pdf_bytes = result["Body"].read()

    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")

        print(f"S3 download error: {error_code}: {error}")

        if error_code in ("NoSuchKey", "404"):
            return response(404, {
                "status": "error",
                "error": "Ticket PDF could not be found."
            })

        return response(500, {
            "status": "error",
            "error": "Ticket download is temporarily unavailable."
        })

    # Return the PDF as a downloadable file.
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="Norman-Tower-Tickets.pdf"',
            "Cache-Control": "private, no-store"
        },
        "isBase64Encoded": True,
        "body": base64.b64encode(pdf_bytes).decode("utf-8")
    }



def lambda_handler(event, context):

    try:
    
        path = event.get("rawPath", "")

        method = event.get("requestContext", {}).get(
            "http", {}
        ).get("method")
            
        # SumUp webhook route.

        if (
            event.get("rawPath", "").startswith("/sumup/webhook/")
            and event.get("requestContext", {})
                .get("http", {})
                .get("method") == "POST"
        ):
            return handle_sumup_webhook(event)
            
        # Start ticket fulfilment
        if (
            path == "/api/registration/fulfil"
            and method == "POST"
        ):
            return handle_start_fulfilment(event)

        # Check ticket fulfilment status
        if (
            path == "/api/registration/fulfilment-status"
            and method == "GET"
        ):
            return handle_fulfilment_status(event)
        
        # Download registration tickets
        if (
            path == "/api/tickets/download"
            and method == "GET"
        ):
            return handle_ticket_download(event)

        # Registration submission below
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

        # Save the registration and create its tickets.

        saved = save_registration(registration)

        # -----------------------------------------
        # Free registration - no payment required
        # -----------------------------------------

        if saved["total_pence"] == 0:

            return response(200, {
                "status": "registered",
                "message": "Registration saved successfully.",
                "confirmation_token": saved["confirmation_token"],
                "registration": registration,
                "database": saved
            })

        # -----------------------------------------
        # Paid registration - create SumUp checkout
        # -----------------------------------------

        print(f"Creating SumUp checkout for registration "
         f"{saved['registration_id']}")

        amount = saved["total_pence"] / 100
        
        print(f"Checkout amount: {amount}")

        checkout = create_checkout(
            amount=amount,
            description=f"{registration['event']} registration",
            checkout_reference=f"registration-{saved['confirmation_token']}",
            return_url=get_webhook_url(),
            redirect_url="https://www.normantower.uk/payment-success/"
            f"?token={saved['confirmation_token']}"
        )

        print("SumUp checkout response:")
        print(json.dumps(checkout, indent=2))
        
        checkout_id = checkout.get("id")
        checkout_url = checkout.get("hosted_checkout_url")
        
        print(f"Checkout ID: {checkout_id}")
        print(f"Checkout URL returned: {bool(checkout_url)}")

        if not checkout_id or not checkout_url:
            raise RuntimeError(
                "SumUp did not return a checkout ID and URL"
            )

        # Associate the checkout with the registration.


        print("Saving SumUp checkout ID")
        
        save_sumup_checkout_id(
            saved["registration_id"],
            checkout_id
        )
       
        print("SumUp checkout ID saved successfully")
        
        return response(200, {
            "status": "awaiting_payment",
            "message": "Registration saved. Payment is required.",
            "registration": registration,
            "database": saved,
            "payment": {
                "checkout_id": checkout_id,
                "checkout_url": checkout_url,
                "amount": amount,
                "currency": "GBP"
            }
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





