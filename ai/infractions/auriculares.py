
"""
RTM — AURICULARES / CASCOS (ART. 18.2 RGC)
VERSIÓN DEMOLEDORA 9.5/10 — ENFOQUE OPERATIVO (MAXIMIZA ARCHIVO REAL)

Objetivo:
- Forzar acreditación del elemento típico: USO de auriculares CONECTADOS a aparato receptor/reproductor.
- Diferenciar uso efectivo vs mera presencia de objeto en el oído.
- Exigir constatación concreta: conexión, reproducción sonora, distancia/ángulo/tiempo de observación.
- Solicitar expediente íntegro y soporte objetivo.

Determinista. Sin OpenAI.
"""

from __future__ import annotations
from typing import Any, Dict, List

# ---------------------------------------------------------
# Context detection
# ---------------------------------------------------------

def is_auriculares_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in ("auriculares", "cascos", "auricular", "art_18_2", "18.2"):
        return True

    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    blob = (body or "") + "\n" + hecho + "\n" + raw
    b = blob.lower()

    signals = [
        "auricular",
        "auriculares",
        "cascos conectados",
        "cascos o auriculares",
        "aparatos receptores",
        "reproductores de sonido",
        "porta auricular",
        "en oído",
        "oido izquierdo",
        "oido derecho"
    ]

    return any(s in b for s in signals)


# ---------------------------------------------------------
# Template demoledor
# ---------------------------------------------------------

def build_auriculares_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCIR UTILIZANDO CASCOS O AURICULARES."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"

        "II. ALEGACIONES\n\n"

        "ALEGACIÓN PRIMERA — ELEMENTO TÍPICO: USO EFECTIVO DE AURICULARES CONECTADOS\n\n"
        "El art. 18.2 del Reglamento General de Circulación sanciona el uso de auriculares conectados a aparatos receptores "
        "o reproductores de sonido durante la conducción. Por tanto, el elemento típico exige acreditar no solo la presencia "
        "de un objeto en el oído, sino el uso efectivo de auriculares conectados a un dispositivo de reproducción sonora.\n\n"

        "No consta acreditado en el expediente:\n"
        "1) Que el objeto observado estuviera efectivamente conectado a un aparato reproductor o receptor.\n"
        "2) Que se estuviera reproduciendo sonido en el momento de la observación.\n"
        "3) Que el agente pudiera verificar materialmente la conexión del dispositivo.\n\n"

        "La mera presencia de un objeto en el oído no permite afirmar la utilización de auriculares conectados si no se "
        "acredita el elemento funcional del tipo sancionador.\n\n"

        "ALEGACIÓN SEGUNDA — CONSTATACIÓN VISUAL Y CONDICIONES DE OBSERVACIÓN\n\n"
        "La imputación se basa exclusivamente en una apreciación visual. No consta en el expediente:\n"
        "• Distancia exacta de observación.\n"
        "• Ángulo o posición respecto del vehículo.\n"
        "• Tiempo durante el cual se realizó la observación.\n"
        "• Circunstancias de tráfico o visibilidad.\n\n"

        "Sin estos elementos no puede considerarse acreditado con suficiente certeza el uso efectivo del dispositivo.\n\n"

        "ALEGACIÓN TERCERA — POSIBLES OBJETOS NO PROHIBIDOS\n\n"
        "La presencia de un objeto en el oído puede corresponder a múltiples elementos no prohibidos por la normativa, "
        "como protectores auditivos, tapones de oído o dispositivos no conectados.\n\n"

        "En ausencia de comprobación directa de conexión o reproducción sonora, la afirmación de uso de auriculares "
        "constituye una inferencia no suficientemente acreditada.\n\n"

        "ALEGACIÓN CUARTA — EXPEDIENTE ÍNTEGRO Y PRUEBA OBJETIVA\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (fotografías, vídeo, anotaciones) que permita "
        "verificar la supuesta utilización del dispositivo.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con prueba objetiva verificable.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


# ---------------------------------------------------------
# Strict check
# ---------------------------------------------------------

def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "conect" not in b:
        missing.append("conexion_dispositivo")

    if "observ" not in b:
        missing.append("condiciones_observacion")

    if "archivo" not in b:
        missing.append("archivo")

    out = []
    seen = set()

    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out
