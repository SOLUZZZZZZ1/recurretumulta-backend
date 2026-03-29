from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from datetime import datetime

def generate_authorization_pdf(path, data):
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(path)
    content = []

    content.append(Paragraph("AUTORIZACIÓN DE REPRESENTACIÓN", styles["Title"]))
    content.append(Spacer(1, 20))

    content.append(Paragraph(f"Nombre: {data.get('name','')}", styles["Normal"]))
    content.append(Paragraph(f"DNI: {data.get('dni','')}", styles["Normal"]))
    content.append(Paragraph(f"Expediente: {data.get('case_id','')}", styles["Normal"]))
    content.append(Spacer(1, 20))

    content.append(Paragraph(
        "Autoriza a LA TALAMANQUINA S.L. (RecurreTuMulta) "
        "a actuar en su nombre para la tramitación administrativa del expediente.",
        styles["Normal"]
    ))

    content.append(Spacer(1, 20))

    content.append(Paragraph(f"Fecha: {datetime.utcnow().isoformat()}", styles["Normal"]))
    content.append(Paragraph(f"IP: {data.get('ip','')}", styles["Normal"]))

    doc.build(content)