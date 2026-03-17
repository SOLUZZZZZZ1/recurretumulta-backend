import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT, TA_CENTER

def _escape(text):
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )

def build_pdf(title: str, body: str) -> bytes:
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
    )

    styles = getSampleStyleSheet()

    normal_style = ParagraphStyle(
        "NormalLeft",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )

    center_style = ParagraphStyle(
        "Center",
        parent=normal_style,
        alignment=TA_CENTER,
    )

    bold_style = ParagraphStyle(
        "Bold",
        parent=normal_style,
        fontName="Helvetica-Bold",
    )

    story = []

    for line in (body or "").split("\n"):
        txt = line.strip().upper()

        if not line.strip():
            story.append(Spacer(1, 8))
            continue

        if "ESCRITO DE ALEGACIONES" in txt or "A LA JEFATURA PROVINCIAL DE TRÁFICO" in txt or "A LA JEFATURA PROVINCIAL DE TRAFICO" in txt:
            story.append(Paragraph(_escape(line), center_style))

        elif txt in ["ANTECEDENTES", "ALEGACIONES", "FUNDAMENTOS DE DERECHO", "SUPLICA", "S U P L I C A", "OTROSÍ DIGO", "OTROSI DIGO"]:
            story.append(Paragraph(_escape(line), bold_style))

        else:
            story.append(Paragraph(_escape(line), normal_style))

    doc.build(story)
    return buffer.getvalue()
