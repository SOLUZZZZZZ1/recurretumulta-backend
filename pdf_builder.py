import io
import html
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT, TA_CENTER

SECTION_TITLES = {
    "ANTECEDENTES",
    "ALEGACIONES",
    "FUNDAMENTOS DE DERECHO",
    "SUPLICA",
    "S U P L I C A",
    "OTROSÍ DIGO",
    "OTROSI DIGO",
}

def _markdown_bold_to_reportlab(text: str) -> str:
    parts = re.split(r'(\*\*.*?\*\*)', text or "")
    out = []
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            inner = html.escape(part[2:-2])
            out.append(f"<b>{inner}</b>")
        else:
            out.append(html.escape(part))
    return "".join(out).replace("\n", "<br/>")

def _is_section_heading(line: str) -> bool:
    txt = (line or "").strip().upper()
    if txt in SECTION_TITLES:
        return True
    if txt.startswith("I. ") or txt.startswith("II. ") or txt.startswith("III. "):
        return True
    if txt.startswith("ALEGACIÓN "):
        return True
    return False

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

    normal = ParagraphStyle(
        "normal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    center = ParagraphStyle(
        "center",
        parent=normal,
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    strong = ParagraphStyle(
        "strong",
        parent=normal,
        fontName="Helvetica-Bold",
        spaceBefore=6,
        spaceAfter=6,
    )

    story = []

    for raw_line in (body or "").splitlines():
        line = raw_line.rstrip()
        txt = line.strip().upper()

        if not line.strip():
            story.append(Spacer(1, 8))
            continue

        formatted = _markdown_bold_to_reportlab(line)

        if "ESCRITO DE ALEGACIONES" in txt:
            story.append(Paragraph(html.escape(line), center))
        elif txt.startswith("A LA "):
            story.append(Paragraph(formatted, center))
        elif _is_section_heading(line):
            story.append(Paragraph(html.escape(line.replace("**", "")), strong))
        else:
            story.append(Paragraph(formatted, normal))

    doc.build(story)
    return buffer.getvalue()
