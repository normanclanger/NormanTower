
import json
import os

import boto3
import psycopg

from generate_ticket import generate_tickets


s3_client = boto3.client("s3")

TICKETS_BUCKET = os.environ.get("TICKETS_BUCKET")


def get_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured"
        )

    return psycopg.connect(database_url)


def mark_failed(confirmation_token):
    """Mark an active fulfilment attempt as failed."""

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE registrations
                    SET
                        fulfilment_status = 'FAILED',
                        updated_at = NOW()
                    WHERE confirmation_token = %s
                      AND fulfilment_status IN (
                          'PROCESSING',
                          'GENERATING_TICKETS',
                          'FAILED'
                      )
                    """,
                    (confirmation_token,)
                )

        print("Fulfilment status updated to FAILED")

    except Exception as exc:
        print(f"Could not update failure status: {exc}")


def lambda_handler(event, context):

    confirmation_token = event.get("confirmation_token")

    if not confirmation_token:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "status": "error",
                "message": "Confirmation token is required."
            })
        }

    try:
        if not TICKETS_BUCKET:
            raise RuntimeError(
                "TICKETS_BUCKET environment variable is not configured"
            )

        # Retrieve and lock the registration.
        with get_connection() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        id,
                        first_name,
                        surname,
                        email,
                        is_test,
                        payment_status,
                        fulfilment_status
                    FROM registrations
                    WHERE confirmation_token = %s
                    FOR UPDATE
                    """,
                    (confirmation_token,)
                )

                registration = cur.fetchone()

                if not registration:
                    raise ValueError("Registration not found.")

                (
                    registration_id,
                    first_name,
                    surname,
                    email,
                    is_test,
                    payment_status,
                    fulfilment_status
                ) = registration

                if payment_status not in ("NOT_REQUIRED", "PAID"):
                    raise ValueError(
                        f"Registration payment status is "
                        f"{payment_status}."
                    )

                # Permit a retry after a previous failure.
                if fulfilment_status not in (
                    "PROCESSING",
                    "GENERATING_TICKETS",
                    "FAILED"
                ):
                    raise ValueError(
                        f"Unexpected fulfilment status: "
                        f"{fulfilment_status}."
                    )

                # Record that ticket generation has started.
                cur.execute(
                    """
                    UPDATE registrations
                    SET
                        fulfilment_status = 'GENERATING_TICKETS',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (registration_id,)
                )

                # Retrieve the existing tickets.
                cur.execute(
                    """
                    SELECT
                        t.id,
                        t.ticket_reference,
                        t.ticket_type,
                        t.ticket_status,
                        ri.item_name
                    FROM tickets t
                    LEFT JOIN registration_items ri
                        ON ri.id = t.registration_item_id
                       AND ri.registration_id = t.registration_id
                    WHERE t.registration_id = %s
                    ORDER BY
                        CASE
                            WHEN t.ticket_type = 'ADMISSION' THEN 0
                            ELSE 1
                        END,
                        t.id
                    """,
                    (registration_id,)
                )

                rows = cur.fetchall()

        if not rows:
            raise ValueError("No tickets found for registration.")

        tickets = []

        for row in rows:
            (
                ticket_db_id,
                ticket_reference,
                ticket_type,
                ticket_status,
                item_name
            ) = row

            if not ticket_reference:
                raise ValueError(
                    f"Ticket {ticket_db_id} has no ticket reference."
                )

            if ticket_status != "ISSUED":
                raise ValueError(
                    f"Ticket {ticket_reference} is not issued."
                )

            if ticket_type == "ADMISSION":
                product = "Admission"

            elif ticket_type == "ITEM":
                if not item_name:
                    raise ValueError(
                        f"Item name missing for ticket "
                        f"{ticket_reference}."
                    )

                product = item_name

            else:
                raise ValueError(
                    f"Unknown ticket type: {ticket_type}."
                )

            tickets.append({
                "first_name": first_name,
                "surname": surname,
                "ticket_reference": ticket_reference,
                "product": product
            })

        print(
            f"Generating {len(tickets)} tickets "
            f"for registration {registration_id}"
        )

        # Generate the PDF.
        pdf_bytes = generate_tickets(
            tickets,
            is_test=is_test
        )

        if not pdf_bytes:
            raise RuntimeError("Ticket generator returned an empty PDF.")

        # Upload the PDF to private S3 storage.
        s3_key = f"tickets/{confirmation_token}.pdf"

        s3_client.put_object(
            Bucket=TICKETS_BUCKET,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf"
        )

        print(
            f"Uploaded ticket PDF to "
            f"s3://{TICKETS_BUCKET}/{s3_key}"
        )

        # Leave status as GENERATING_TICKETS for now.
        # It will move to SENDING_EMAIL when email sending is implemented.

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "registration_id": registration_id,
                "email": email,
                "ticket_count": len(tickets),
                "pdf_size": len(pdf_bytes),
                "pdf_uploaded": True,
                "fulfilment_status": "GENERATING_TICKETS"
            })
        }

    except Exception as exc:
        print(f"Ticket generation failed: {exc}")

        mark_failed(confirmation_token)

        raise
