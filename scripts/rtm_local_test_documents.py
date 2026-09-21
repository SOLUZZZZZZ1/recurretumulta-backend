"""Clearly fictitious PDF inputs for the local Banks intake, with no real ID."""
from io import BytesIO
from pathlib import Path
import textwrap

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from rtm_core.local_operator_auth import assert_local_operator_auth_ready

DOCUMENTS = {
    "01_identidad_prueba_frontal.pdf": (
        "Identificacion ficticia - frontal",
        ["Nombre: PRUEBA LOCAL BANCOS", "Identificador de prueba: RTMTEST001",
         "Email: prueba.bancos@example.com", "Este archivo no es un DNI, NIE ni pasaporte.",
         "Sirve exclusivamente para comprobar la subida de archivos en el PC local."],
    ),
    "02_identidad_prueba_reverso.pdf": (
        "Identificacion ficticia - reverso",
        ["Nombre: PRUEBA LOCAL BANCOS", "Identificador de prueba: RTMTEST001",
         "Domicilio ficticio: Calle de Prueba 1, 08240 Manresa, Barcelona.",
         "Sin fotografia, firma, codigo oficial ni informacion de una persona real."],
    ),
    "03_comision_bancaria_prueba.pdf": (
        "Comision bancaria ficticia",
        ["Entidad ficticia: BANCO DE PRUEBA RTM", "Cliente ficticio: PRUEBA LOCAL BANCOS",
         "Importe discutido: 30,00 EUR", "Concepto: comision de mantenimiento de prueba.",
         "Se solicita su revision y devolucion dentro de este escenario ficticio.",
         "No corresponde a una cuenta, operacion, deuda o reclamacion real."],
    ),
    "04_candidato_autorizacion_prueba.pdf": (
        "Candidato documental de prueba",
        ["Cliente ficticio: PRUEBA LOCAL BANCOS", "Identificador de prueba: RTMTEST001",
         "Documento para probar exclusivamente la recepcion de un candidato pendiente de revision.",
         "No contiene firma real y no acredita representacion, consentimiento ni poderes.",
         "Su subida nunca debe marcar el expediente como autorizado o verificado."],
    ),
}


def pdf_bytes(title: str, lines: list[str]) -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    canvas.setTitle(title)
    canvas.setAuthor("RTM - pruebas locales")
    canvas.setFillColor(HexColor("#123C69"))
    canvas.rect(0, height - 108, width, 108, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("Helvetica-Bold", 17)
    canvas.drawString(42, height - 47, "RTM | DOCUMENTO FICTICIO")
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(42, height - 77, "PRUEBA LOCAL SIN VALIDEZ")
    canvas.setFillColor(HexColor("#123C69"))
    canvas.setFont("Helvetica-Bold", 17)
    canvas.drawString(42, height - 153, title)
    canvas.setFont("Helvetica", 11)
    canvas.setFillColor(HexColor("#263238"))
    y = height - 194
    for line in lines:
        for part in textwrap.wrap(line, width=78):
            canvas.drawString(42, y, part)
            y -= 17
        y -= 12
    canvas.setFillColor(HexColor("#FFF3CD"))
    canvas.rect(42, 96, width - 84, 56, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#754C00"))
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(56, 129, "SOLO PARA PRUEBAS EN EL PC LOCAL")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(56, 112, "Sin validez identificativa, contractual, bancaria ni administrativa.")
    canvas.setFillColor(HexColor("#607D8B"))
    canvas.setFont("Helvetica", 9)
    canvas.drawString(42, 52, "RTM / pruebas de desarrollo / datos sinteticos")
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def create_local_test_documents(directory: Path) -> list[Path]:
    assert_local_operator_auth_ready()
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError("La carpeta de documentos de prueba no esta preparada.")
    results = []
    for filename, (title, lines) in DOCUMENTS.items():
        path = directory / filename
        data = pdf_bytes(title, lines)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != data:
                raise RuntimeError("Un documento de prueba existente ha cambiado; no se sustituira.")
        else:
            with path.open("xb") as stream:
                stream.write(data)
        results.append(path)
    return results
