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
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=10,
    )

    center_title_style = ParagraphStyle(
        "CenterTitle",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    center_org_style = ParagraphStyle(
        "CenterOrg",
        parent=normal_style,
        fontName="Helvetica",
        fontSize=11,
        alignment=TA_CENTER,
        spaceAfter=10,
    )

    bold_style = ParagraphStyle(
        "Bold",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=11,
        spaceBefore=10,
        spaceAfter=6,
    )

    story = []

    for raw_line in (body or "").split("\n"):
        line = raw_line.rstrip()
        txt = line.strip()
        txt_upper = txt.upper()

        if not txt:
            story.append(Spacer(1, 8))
            continue

        if txt_upper.startswith("REFERENCIA: EXPTE.") or txt_upper.startswith("EXPEDIENTE"):
            story.append(Paragraph(_escape(txt), normal_style))
            story.append(Spacer(1, 6))
            continue

        if "ESCRITO DE ALEGACIONES" in txt_upper:
            story.append(Paragraph(_escape(txt), center_title_style))
            continue

        if txt_upper.startswith("A LA "):
            story.append(Paragraph(_escape(txt), center_org_style))
            continue

        if txt_upper in [
            "ANTECEDENTES",
            "ALEGACIONES",
            "FUNDAMENTOS DE DERECHO",
            "SUPLICA",
            "S U P L I C A",
            "OTROSÍ DIGO",
            "OTROSI DIGO",
            "EXPONE",
        ] or txt_upper.startswith("I. ") or txt_upper.startswith("II. ") or txt_upper.startswith("III. ") or txt_upper.startswith("IV. "):
            story.append(Spacer(1, 4))
            story.append(Paragraph(_escape(txt), bold_style))
            continue

        if txt_upper.startswith("ALEGACIÓN "):
            story.append(Paragraph(_escape(txt), bold_style))
            continue

        story.append(Paragraph(_escape(txt), normal_style))

    doc.build(story)
    return buffer.getvalue()
