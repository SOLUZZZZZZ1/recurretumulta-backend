import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT


def _escape_and_format(text: str) -> str:
    """Convierte texto plano a markup básico para Paragraph.
    - Escapa &, <, >
    - Convierte saltos de línea en <br/>
    """
    if text is None:
        return ""
    s = str(text)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("\n", "<br/>\n")
    return s


def build_pdf(title: str, body: str) -> bytes:
    """
    Genera un PDF profesional (con ajuste automático de línea y paginado)
    a partir de título y cuerpo.
    """
    buffer = io.BytesIO()

    margin = 25 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=str(title or "").strip()[:180],
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "RTMTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceAfter=10,
        alignment=TA_LEFT,
    )

    body_style = ParagraphStyle(
        "RTMBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=6,
        alignment=TA_LEFT,
    )

    story = []

    if title and str(title).strip():
        story.append(Paragraph(_escape_and_format(str(title).strip()), title_style))
        story.append(Spacer(1, 6))

    raw = str(body or "").strip()
    if raw:
        paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
        for p in paragraphs:
            story.append(Paragraph(_escape_and_format(p), body_style))

    if not story:
        story.append(Paragraph(" ", body_style))

    doc.build(story)
    return buffer.getvalue()
