"""Reproducible synthetic notice. Does not import the app or contact providers."""
import hashlib
import json
from pathlib import Path
import shutil
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def build():
    root = Path(__file__).resolve().parents[1] / "rtm_core" / "fixtures"
    destination = root / "staging_client_v1"
    destination.mkdir(exist_ok=True)
    manifest = {}
    for kind, name in (("identity_front", "IDENTIDAD_FICTICIA_FRONT.pdf"), ("identity_back", "IDENTIDAD_FICTICIA_BACK.pdf")):
        shutil.copyfile(root / "staging_radar_v1" / name, destination / name)
        manifest[kind] = {"filename": name}
    name = "NOTIFICACION_FICTICIA_RTM_20261106.pdf"
    styles = getSampleStyleSheet()
    styles["Normal"].leading = 16
    sections = [
        ("ENSAYO RTM - SIN VALIDEZ", "Title"),
        ("Todos los datos, hechos, organismo y plazos de este documento son ficticios. No es una notificación administrativa real ni acredita una deuda.", "Normal"),
        ("Notificación ficticia para el recorrido de cliente", "Heading2"),
        ("Referencia: RTM-PRUEBA-2026-1106<br/>Organismo del supuesto: Dirección General de Tráfico - Unidad FICTICIA RTM.<br/>Interesado y titular en el supuesto: PERSONA DE PRUEBA RTM, RTMTEST001.<br/>Domicilio ficticio: CALLE FICTICIA RTM, 1, 00000 CIUDAD FICTICIA (PROVINCIA FICTICIA).", "Normal"),
        ("Hechos del supuesto", "Heading2"),
        ("El 2 de noviembre de 2026 a las 10:15 se atribuye al vehículo FICTICIO-RTM una parada en la carretera FICTICIA RTM, kilómetro 1. La persona del supuesto manifiesta que la parada se debió a una avería. El documento no identifica al conductor y no aporta fotografías ni informe técnico. Esos extremos deben permanecer pendientes de comprobación.", "Normal"),
        ("Importe y recepción del supuesto", "Heading2"),
        ("Importe ficticio de sanción: 200 EUR. Este importe no es el precio del servicio RTM.<br/>Fecha de recepción ficticia: 6 de noviembre de 2026.<br/>A efectos exclusivos de esta prueba, el documento indica un plazo de 20 días naturales desde el día siguiente a la recepción, con vencimiento el 26 de noviembre de 2026. Esta regla pertenece al supuesto de prueba; no se presenta como criterio jurídico aplicable a un caso real.", "Normal"),
        ("Documentación para estudiar", "Heading2"),
        ("El interesado pide revisar el supuesto y valorar alegaciones relacionadas con la avería. La identidad se aporta en dos documentos ficticios separados. La autorización se genera para el expediente nuevo y requiere su propio proceso de revisión. Este documento no acredita autorización, pago, revisión, aceptación del presupuesto ni presentación de recurso.", "Normal"),
    ]
    story = []
    for text, style in sections:
        story.extend([Paragraph(text, styles[style]), Spacer(1, 10)])
    def footer(canvas, doc):
        canvas.setFillColor(colors.darkred)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(45, 28, "DATOS FICTICIOS - SOLO STAGING - SIN VALIDEZ ADMINISTRATIVA")
    SimpleDocTemplate(str(destination / name), pagesize=A4, leftMargin=45, rightMargin=45,
                      topMargin=35, bottomMargin=45, title="Notificación ficticia RTM", author="RTM TEST",
                      invariant=1).build(story, onFirstPage=footer, onLaterPages=footer)
    manifest["original"] = {"filename": name}
    for item in manifest.values():
        content = (destination / item["filename"]).read_bytes()
        item.update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"created": True, "fixtures": list(manifest)}))


if __name__ == "__main__":
    build()
