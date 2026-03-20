# DOCX BUILDER FINAL

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

def build_docx(title, body):
    doc = Document()

    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(11)

    lines = body.split("\n")

    for line in lines:
        txt = line.strip()

        p = doc.add_paragraph(txt)

        if "EXPEDIENTE" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        elif "ESCRITO DE ALEGACIONES" in txt or "A LA" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        elif "ALEGACIÓN" in txt or "FUNDAMENTOS" in txt or "SUPLICO" in txt:
            for run in p.runs:
                run.bold = True

    doc.save("output.docx")
