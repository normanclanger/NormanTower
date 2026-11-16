
import json
import os

import psycopg

from generate_ticket import generate_tickets


def get_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured"
        )

    return psycopg.connect(database_url)


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
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Retrieve the registration and its tickets.
                cur.execute(
                    """
                    SELECT
                        r.id,
                        r.first_name,
                        r.surname,
                        r.email,
                        r.is_test,
                        r.payment_status,
                        r.fulfilment_status,
                        t.id,
                        t.ticket_reference,
                        t.ticket_type,
                        t.ticket_status,
                        ri.item_name
                    FROM registrations r
                    JOIN tickets t
                        ON t.registration_id = r.id
                    LEFT JOIN registration_items ri
                        ON ri.id = t.registration_item_id
                       AND ri.registration_id = t.registration_id
                    WHERE r.confirmation_token = %s
                    ORDER BY
                        CASE
                            WHEN t.ticket_type = 'ADMISSION' THEN 0
                            ELSE 1
                        END,
                        t.id
                    """,
                    (confirmation_token,)
                )

                rows = cur.fetchall()

        if not rows:
            raise ValueError(
                "Registration or tickets not found."
            )

        registration_id = rows[0][0]
        first_name = rows[0][1]
        surname = rows[0][2]
        email = rows[0][3]
        is_test = rows[0][4]
        payment_status = rows[0][5]
        fulfilment_status = rows[0][6]

        # Check that the registration is eligible for fulfilment.
        if payment_status not in ("NOT_REQUIRED", "PAID"):
            raise ValueError(
                f"Registration payment status is {payment_status}."
            )

        if fulfilment_status not in (
            "PROCESSING",
            "GENERATING_TICKETS"
        ):
            raise ValueError(
                f"Unexpected fulfilment status: {fulfilment_status}."
            )

        tickets = []

        for row in rows:
            (
                _registration_id,
                _first_name,
                _surname,
                _email,
                _is_test,
                _payment_status,
                _fulfilment_status,
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
                        f"Item name missing for ticket {ticket_reference}."
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

        # Generate the PDF using the existing ticket generator.
        pdf_bytes = generate_tickets(
            tickets,
            is_test=is_test
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "registration_id": registration_id,
                "email": email,
                "ticket_count": len(tickets),
                "pdf_size": len(pdf_bytes)
            })
        }

    except Exception as exc:
        print(f"Ticket generation failed: {exc}")
        raise
