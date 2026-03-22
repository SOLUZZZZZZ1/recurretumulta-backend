import io
import re
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

def _add_runs_with_bold(paragraph, text):
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        else:
            paragraph.add_run(part)

def build_docx(title: str, body: str) -> bytes:
    doc = Document()

    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    for line in (body or "").splitlines():
        p = doc.add_paragraph()
        txt_upper = line.strip().upper()

        if "ESCRITO DE ALEGACIONES" in txt_upper:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(line)
            run.bold = True

        elif txt_upper in ["ANTECEDENTES","ALEGACIONES","FUNDAMENTOS DE DERECHO","SUPLICA","S U P L I C A","OTROSÍ DIGO","OTROSI DIGO"]:
            run = p.add_run(line)
            run.bold = True

        else:
            _add_runs_with_bold(p, line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
