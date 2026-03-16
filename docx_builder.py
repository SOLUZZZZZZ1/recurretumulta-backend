import io
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

def build_docx(title: str, body: str) -> bytes:
    doc = Document()
    p = doc.add_paragraph()
    run = p.add_run(title)
    run.bold = True
    run.font.size = Pt(14)
    doc.add_paragraph("")
    for line in body.splitlines():
        p = doc.add_paragraph(line)
        txt = line.strip().upper()
        if "ESCRITO DE ALEGACIONES" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if "A LA JEFATURA PROVINCIAL DE TRÁFICO DE" in txt or "A LA JEFATURA PROVINCIAL DE TRAFICO DE" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
