import io
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT, TA_CENTER

def _format_bold(text):
    return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

def _escape(text):
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
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

    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=14)
    center = ParagraphStyle("center", parent=normal, alignment=TA_CENTER)
    bold = ParagraphStyle("bold", parent=normal, fontName="Helvetica-Bold")

    story = []

    for line in (body or "").split("\n"):
        if not line.strip():
            story.append(Spacer(1,10))
            continue

        txt = _escape(_format_bold(line))

        if "ESCRITO DE ALEGACIONES" in line.upper():
            story.append(Paragraph(txt, center))
        elif line.strip().upper() in ["ANTECEDENTES","ALEGACIONES","FUNDAMENTOS DE DERECHO","SUPLICA","S U P L I C A","OTROSÍ DIGO","OTROSI DIGO"]:
            story.append(Paragraph(txt, bold))
        else:
            story.append(Paragraph(txt, normal))

    doc.build(story)
    return buffer.getvalue()
