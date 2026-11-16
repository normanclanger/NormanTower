
from io import BytesIO
from pathlib import Path
import math

from reportlab.pdfgen import canvas
from reportlab.lib.colors import black, red
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics

from pypdf import PdfReader, PdfWriter, Transformation


TEMPLATE = Path("12bellticket.pdf")
OUTPUT = Path("sample_tickets.pdf")

# Sample ticket data
TICKETS = [
    {
        "surname": "Hart",
        "first_name": "Tim",
        "ticket_id": "NT-1234567890",
        "product": "Admission",
    },
    {
        "surname": "Smith",
        "first_name": "Jane",
        "ticket_id": "NT-1234567891",
        "product": "Bacon Roll",
    },
    {
        "surname": "Jones",
        "first_name": "Peter",
        "ticket_id": "NT-1234567892",
        "product": "Sausage Bap",
    },
    {
        "surname": "Brown",
        "first_name": "Elizabeth",
        "ticket_id": "NT-1234567893",
        "product": "Pilgrims' Kitchen Breakfast",
    },
    
    
    {
        "surname": "Brown",
        "first_name": "Elizabeth",
        "ticket_id": "NT-1234567893",
        "product": "Pilgrims' Kitchen Breakfast",
    },    
    
]

IS_TEST = True

# Text positioning: coordinates measured from
# the bottom-left corner of the ticket.

NAME_X = 35
NAME_Y = 75

TICKET_ID_X = 35
TICKET_ID_Y = 50

PRODUCT_X = 250
PRODUCT_Y = 65
PRODUCT_MAX_WIDTH = 125
PRODUCT_FONT_SIZE = 16
PRODUCT_MIN_FONT_SIZE = 9

# A4 landscape layout: top-left, top-right,
# bottom-left, bottom-right.
TICKETS_PER_PAGE = 4


def make_ticket_page(ticket, is_test):

    # Clone the template into its own writer.
    # This is important for compatibility with pypdf 7.
    ticket_writer = PdfWriter(clone_from=str(TEMPLATE))
    page = ticket_writer.pages[0]

    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    overlay_buffer = BytesIO()

    pdf = canvas.Canvas(
        overlay_buffer,
        pagesize=(width, height)
    )

    # -----------------------------------------
    # Left box: ticket holder
    # -----------------------------------------

    pdf.setFillColor(black)
    pdf.setFont("Helvetica-Bold", 16)

    full_name = f"{ticket['surname']}, {ticket['first_name']}"

    pdf.drawString(NAME_X, NAME_Y, full_name)

    pdf.setFont("Helvetica", 11)

    pdf.drawString(
        TICKET_ID_X,
        TICKET_ID_Y,
        f"Ticket ID: {ticket['ticket_id']}"
    )

    # -----------------------------------------
    # Right box: product
    # -----------------------------------------

    product = ticket["product"]
    font_size = PRODUCT_FONT_SIZE

    while (
        font_size > PRODUCT_MIN_FONT_SIZE
        and pdfmetrics.stringWidth(
            product, "Helvetica-Bold", font_size
        ) > PRODUCT_MAX_WIDTH
    ):
        font_size -= 0.5

    pdf.setFont("Helvetica-Bold", font_size)

    pdf.drawCentredString(
        PRODUCT_X + PRODUCT_MAX_WIDTH / 2,
        PRODUCT_Y,
        product
    )

    # -----------------------------------------
    # Test watermark
    # -----------------------------------------

    if is_test:
        pdf.saveState()

        pdf.translate(width / 2, height / 2)
        pdf.rotate(35)

        pdf.setFillColor(red)
        pdf.setFillAlpha(0.25)
        pdf.setFont("Helvetica-Bold", 48)

        pdf.drawCentredString(0, 0, "TEST")

        pdf.restoreState()

    pdf.save()

    overlay_buffer.seek(0)
    overlay = PdfReader(overlay_buffer)

    page.merge_page(overlay.pages[0])

    return page, width, height


def generate_tickets():

    if not TICKETS:
        print("No tickets to generate.")
        return

    a4_width, a4_height = landscape(A4)

    writer = PdfWriter()

    # Determine the ticket dimensions.
    _, ticket_width, ticket_height = make_ticket_page(
        TICKETS[0], IS_TEST
    )

    # Four positions on each A4 landscape page.
    positions = [
        (0, a4_height - ticket_height),
        (a4_width - ticket_width, a4_height - ticket_height),
        (0, 0),
        (a4_width - ticket_width, 0),
    ]

    page_count = math.ceil(len(TICKETS) / TICKETS_PER_PAGE)

    for page_number in range(page_count):

        sheet = writer.add_blank_page(
            width=a4_width,
            height=a4_height
        )

        first_ticket = page_number * TICKETS_PER_PAGE
        page_tickets = TICKETS[
            first_ticket:first_ticket + TICKETS_PER_PAGE
        ]

        for position, ticket in enumerate(page_tickets):

            ticket_page, _, _ = make_ticket_page(
                ticket, IS_TEST
            )

            x, y = positions[position]

            sheet.merge_transformed_page(
                ticket_page,
                Transformation().translate(tx=x, ty=y)
            )

    with OUTPUT.open("wb") as output_file:
        writer.write(output_file)

    print(f"Created {OUTPUT}")
    print(f"Tickets: {len(TICKETS)}")
    print(f"A4 pages: {page_count}")
    print("Page size: A4 landscape")


if __name__ == "__main__":
    generate_tickets()
