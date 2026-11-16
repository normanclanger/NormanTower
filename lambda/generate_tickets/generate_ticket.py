
from io import BytesIO
from pathlib import Path
import math

import qrcode

from reportlab.pdfgen import canvas
from reportlab.lib.colors import black, red
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader

from pypdf import PdfReader, PdfWriter, Transformation


TEMPLATE = Path(__file__).parent / "12bellticket.pdf"

NAME_X = 35
NAME_Y = 80

TICKET_ID_X = 35
TICKET_ID_Y = 50

PRODUCT_X = 250
PRODUCT_Y = 80
PRODUCT_MAX_WIDTH = 125
PRODUCT_FONT_SIZE = 16
PRODUCT_MIN_FONT_SIZE = 9

TICKETS_PER_PAGE = 4

# QR code position and size, measured from bottom-left.
QR_X = 360
QR_Y = 25
QR_SIZE = 45


def make_qr_image(ticket_reference):

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1
    )

    qr.add_data(ticket_reference)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    ).convert("RGB")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return ImageReader(buffer), buffer


def make_ticket_page(ticket, is_test):

    ticket_writer = PdfWriter(clone_from=str(TEMPLATE))
    page = ticket_writer.pages[0]

    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    overlay_buffer = BytesIO()

    pdf = canvas.Canvas(
        overlay_buffer,
        pagesize=(width, height)
    )

    # Ticket holder
    pdf.setFillColor(black)
    pdf.setFont("Helvetica-Bold", 16)

    full_name = f"{ticket['surname']}, {ticket['first_name']}"
    pdf.drawString(NAME_X, NAME_Y, full_name)

    pdf.setFont("Helvetica", 11)

    pdf.drawString(
        TICKET_ID_X,
        TICKET_ID_Y,
        f"Ticket ID: {ticket['ticket_reference']}"
    )

    # Product
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

    # QR code: contains only the ticket reference.
    qr_image, qr_buffer = make_qr_image(
        ticket["ticket_reference"]
    )

    pdf.drawImage(
        qr_image,
        QR_X,
        QR_Y,
        width=QR_SIZE,
        height=QR_SIZE
    )

    # Test watermark
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


def generate_tickets(tickets, is_test=False):

    if not tickets:
        raise ValueError("No tickets supplied.")

    a4_width, a4_height = landscape(A4)

    writer = PdfWriter()

    # Determine ticket dimensions.
    _, ticket_width, ticket_height = make_ticket_page(
        tickets[0], is_test
    )

    positions = [
        (0, a4_height - ticket_height),
        (a4_width - ticket_width, a4_height - ticket_height),
        (0, 0),
        (a4_width - ticket_width, 0),
    ]

    page_count = math.ceil(
        len(tickets) / TICKETS_PER_PAGE
    )

    for page_number in range(page_count):

        sheet = writer.add_blank_page(
            width=a4_width,
            height=a4_height
        )

        first_ticket = page_number * TICKETS_PER_PAGE

        page_tickets = tickets[
            first_ticket:first_ticket + TICKETS_PER_PAGE
        ]

        for position, ticket in enumerate(page_tickets):

            ticket_page, _, _ = make_ticket_page(
                ticket, is_test
            )

            x, y = positions[position]

            sheet.merge_transformed_page(
                ticket_page,
                Transformation().translate(tx=x, ty=y)
            )

    output = BytesIO()
    writer.write(output)
    output.seek(0)

    print(f"Tickets: {len(tickets)}")
    print(f"A4 pages: {page_count}")

    return output.getvalue()


# Local test only
if __name__ == "__main__":

    sample_tickets = [
        {
            "surname": "Hart",
            "first_name": "Tim",
            "ticket_reference": "NT-12345678",
            "product": "Admission",
        },
        {
            "surname": "Smith",
            "first_name": "Jane",
            "ticket_reference": "NT-12345679",
            "product": "Bacon Roll",
        },
        {
            "surname": "Jones",
            "first_name": "Peter",
            "ticket_reference": "NT-12345680",
            "product": "Sausage Bap",
        },
        {
            "surname": "Brown",
            "first_name": "Elizabeth",
            "ticket_reference": "NT-12345681",
            "product": "Pilgrims' Kitchen Breakfast",
        },
    ]

    pdf_bytes = generate_tickets(sample_tickets, is_test=True)

    Path("sample_tickets.pdf").write_bytes(pdf_bytes)

    print("Created sample_tickets.pdf")
