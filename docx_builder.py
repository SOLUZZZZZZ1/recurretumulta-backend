import io
from docx import Document
from docx.shared import Pt

def build_docx(title: str, body: str) -> bytes:
    doc = Document()
    p = doc.add_paragraph()
    run = p.add_run(title)
    run.bold = True
    run.font.size = Pt(14)
    doc.add_paragraph("")
    for line in body.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
