import io
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

def build_docx(title: str, body: str) -> bytes:
    doc = Document()

    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    for line in (body or "").splitlines():
        p = doc.add_paragraph(line)

        txt = line.strip().upper()

        if "ESCRITO DE ALEGACIONES" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True

        elif "A LA JEFATURA PROVINCIAL DE TRÁFICO" in txt or "A LA JEFATURA PROVINCIAL DE TRAFICO" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        elif txt in ["ANTECEDENTES", "ALEGACIONES", "FUNDAMENTOS DE DERECHO", "SUPLICA", "S U P L I C A", "OTROSÍ DIGO", "OTROSI DIGO"]:
            for run in p.runs:
                run.bold = True

        else:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
