import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def build_pdf(title: str, body: str) -> bytes:
    """
    Genera un PDF profesional a partir de título y cuerpo.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x_margin = 25 * mm
    y_margin = 25 * mm
    y = height - y_margin

    # Título
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x_margin, y, title)
    y -= 20

    # Cuerpo
    c.setFont("Helvetica", 10)
    for line in body.splitlines():
        if y < y_margin:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - y_margin
        c.drawString(x_margin, y, line)
        y -= 14

    c.showPage()
    c.save()

    return buffer.getvalue()
