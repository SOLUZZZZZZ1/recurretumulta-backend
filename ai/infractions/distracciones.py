"""
RTM — TRÁFICO — DISTRACCIONES (SVL-DIS-1)
- Art. 18.2: auriculares/cascos conectados a aparatos de sonido.

Determinista (sin OpenAI). Salida {"asunto","cuerpo"}.
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str = "") -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if isinstance(body, str) and body.strip():
        parts.append(body)
    return " ".join(parts).lower()


def is_auriculares_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)

    # Artículo 18 explícito ayuda
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None else None
    except Exception:
        art_i = None

    aur_signals = [
        "auricular", "auriculares",
        "cascos", "casco",
        "receptores", "reproductores",
        "aparatos receptores", "aparatos reproductores",
        "sonido",
        "conectados", "conectado",
        "oído", "oido",
        "porta auricular", "lleva auricular",
    ]

    movil_signals = ["teléfono", "telefono", "móvil", "movil", "uso manual", "utilizando manualmente", "pantalla", "whatsapp", "llamada"]
    if any(s in b for s in movil_signals):
        return False

    if art_i == 18 and any(s in b for s in aur_signals):
        return True
    if any(s in b for s in ["auriculares conectados", "porta auricular", "lleva auricular", "cascos conectados"]):
        return True

    return False


def build_auriculares_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCIR UTILIZANDO CASCOS O AURICULARES CONECTADOS A APARATOS RECEPTORES O REPRODUCTORES DE SONIDO (ART. 18.2)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TIPICIDAD Y DESCRIPCIÓN CONCRETA (ART. 18.2)\n\n"
        "La imputación por uso de cascos/auriculares exige descripción circunstanciada y verificable. No basta fórmula genérica. Debe precisarse:\n"
        "1) Si eran auriculares/cascos conectados a un dispositivo de sonido y cómo se constató.\n"
        "2) Si el uso era efectivo durante la conducción (no mera tenencia), y en qué momento exacto.\n"
        "3) Posición/distancia de observación, visibilidad y circunstancias del tráfico.\n"
        "4) Si era uno o dos auriculares y su ubicación (oído izq./dcho.), con soporte objetivo si existiera.\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y EXPEDIENTE ÍNTEGRO\n\n"
        "Se solicita denuncia íntegra y, en su caso, soporte probatorio (fotografías/vídeo/informe) que permita contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de descripción concreta.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}
