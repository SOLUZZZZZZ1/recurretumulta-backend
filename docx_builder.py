import io
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

def build_docx(title: str, body: str) -> bytes:
    doc = Document()

    # Configuración base
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    for line in (body or "").splitlines():
        p = doc.add_paragraph()

        run = p.add_run(line)

        txt = line.strip().upper()

        # Interlineado ligero (Word lo gestiona automático)
        p.paragraph_format.space_after = Pt(8)

        # TÍTULO
        if "ESCRITO DE ALEGACIONES" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.bold = True

        # ÓRGANO
        elif "A LA JEFATURA PROVINCIAL DE TRÁFICO" in txt or "A LA JEFATURA PROVINCIAL DE TRAFICO" in txt:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # SECCIONES IMPORTANTES
        elif txt in [
            "ANTECEDENTES",
            "ALEGACIONES",
            "FUNDAMENTOS DE DERECHO",
            "SUPLICA",
            "S U P L I C A",
            "OTROSÍ DIGO",
            "OTROSI DIGO"
        ]:
            run.bold = True
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(6)

        # RESTO DEL TEXTO
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
