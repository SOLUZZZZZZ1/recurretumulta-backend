# PDF BUILDER FINAL

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def build_pdf(title, body):
    doc = SimpleDocTemplate("output.pdf", pagesize=A4)
    styles = getSampleStyleSheet()

    normal = ParagraphStyle("normal", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=10)
    bold = ParagraphStyle("bold", fontName="Helvetica-Bold", fontSize=11, spaceBefore=10, spaceAfter=6)
    center = ParagraphStyle("center", fontName="Helvetica-Bold", fontSize=12, alignment=TA_CENTER, spaceAfter=12)

    story = []

    lines = body.split("\n")

    for line in lines:
        txt = line.strip()

        if not txt:
            story.append(Spacer(1, 8))
            continue

        if "EXPEDIENTE" in txt:
            story.append(Paragraph(txt, normal))
            story.append(Spacer(1, 8))
            continue

        if "ESCRITO DE ALEGACIONES" in txt or "A LA" in txt:
            story.append(Paragraph(txt, center))
            continue

        if "ALEGACIÓN" in txt or "FUNDAMENTOS DE DERECHO" in txt or "SUPLICO" in txt:
            story.append(Paragraph(txt, bold))
            continue

        story.append(Paragraph(txt, normal))

    doc.build(story)
