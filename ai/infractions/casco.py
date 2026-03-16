"""
RTM — CASCO DE PROTECCIÓN
Determinista, sin IA.
Salida: {"asunto","cuerpo"}

Objetivo:
- Separar con claridad CASCO de AURICULARES.
- Activarse solo cuando la infracción sea realmente:
    * no llevar casco
    * casco no homologado
    * casco mal abrochado / incorrectamente utilizado
- Evitar falsos positivos cuando aparezcan:
    * bluetooth en casco
    * intercomunicador
    * auriculares / cascos conectados
"""

from __future__ import annotations
from typing import Any, Dict, List


def _safe_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _blob(core: Dict[str, Any], body: str = "") -> str:
    parts: List[str] = []
    for k in (
        "raw_text_pdf",
        "raw_text_vision",
        "raw_text_blob",
        "vision_raw_text",
        "hecho_denunciado_literal",
        "hecho_denunciado_resumido",
        "hecho_imputado",
        "subtipo_infraccion",
    ):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if body:
        parts.append(body)

    blob = " ".join(parts).lower()
    blob = (
        blob.replace("protección", "proteccion")
            .replace("utilización", "utilizacion")
            .replace("oído", "oido")
    )
    return blob


def is_casco_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)

    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    if tipo in ("casco", "no_casco", "casco_proteccion"):
        return True

    # Bloqueadores fuertes: si el núcleo es audio/auriculares, NO es casco.
    auriculares_blockers = [
        "auricular",
        "auriculares",
        "cascos conectados",
        "cascos o auriculares",
        "reproductores de sonido",
        "aparatos receptores",
        "bluetooth en casco",
        "bluetooth instalado en casco",
        "intercomunicador",
        "dispositivo de audio",
        "reproductor de musica",
        "reproductor de música",
        "porta auricular",
        "oido izquierdo",
        "oido derecho",
        "conectado a dispositivo",
        "conectados a aparatos",
    ]
    if any(tok in b for tok in auriculares_blockers):
        return False

    strong_signals = [
        "sin casco",
        "no llevar casco",
        "no lleve casco",
        "no utiliza casco",
        "no utilizar casco",
        "casco de proteccion",
        "casco homologado",
        "casco no homologado",
        "casco mal abrochado",
        "casco incorrectamente abrochado",
        "casco desabrochado",
        "sin llevar abrochado el casco",
        "sin usar casco",
        "no hacer uso del casco",
    ]
    if any(tok in b for tok in strong_signals):
        return True

    # Combinación suficiente: aparece casco y además una señal de incumplimiento.
    casco_present = "casco" in b
    incumplimiento = any(tok in b for tok in [
        "sin",
        "no llevar",
        "no utilizar",
        "no usa",
        "desabrochado",
        "mal abrochado",
        "no homologado",
        "incorrectamente",
    ])
    return casco_present and incumplimiento


def build_casco_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = (
        core.get("hecho_imputado")
        or core.get("hecho_denunciado_resumido")
        or "NO UTILIZAR CASCO DE PROTECCIÓN EN LAS CONDICIONES EXIGIDAS."
    )

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

        "ALEGACIÓN PRIMERA — ELEMENTO TÍPICO Y CONCRECIÓN DEL INCUMPLIMIENTO\n\n"
        "La infracción relativa al casco de protección exige concretar de forma precisa cuál fue exactamente el incumplimiento observado: "
        "si se afirma ausencia total de casco, casco no homologado, casco desabrochado o uso incorrecto del mismo.\n\n"

        "No basta una referencia genérica al casco si no se describe con claridad el hecho concreto y verificable que integraría el tipo sancionador.\n\n"

        "ALEGACIÓN SEGUNDA — CONDICIONES DE OBSERVACIÓN Y FIABILIDAD DE LA PERCEPCIÓN\n\n"
        "Si la imputación se basa en observación visual del agente, la Administración debe concretar:\n"
        "1) Distancia exacta de observación.\n"
        "2) Ángulo y posición respecto del vehículo.\n"
        "3) Tiempo de observación.\n"
        "4) Circunstancias de visibilidad y tráfico.\n\n"

        "Sin estos datos no puede valorarse con suficiente certeza la realidad del incumplimiento denunciado.\n\n"

        "ALEGACIÓN TERCERA — NECESIDAD DE PRUEBA OBJETIVA O EXPEDIENTE ÍNTEGRO\n\n"
        "Se solicita la aportación íntegra del expediente administrativo y de cualquier soporte objetivo disponible "
        "(fotografía, vídeo, anotaciones o informe ampliatorio), a fin de verificar con precisión la conducta denunciada y permitir contradicción efectiva.\n\n"

        "ALEGACIÓN CUARTA — TIPICIDAD Y MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "La potestad sancionadora exige motivación suficiente y subsunción clara del hecho en el precepto aplicado. "
        "La mera fórmula estereotipada o genérica no satisface las exigencias del principio de tipicidad ni permite el adecuado ejercicio del derecho de defensa.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de motivación suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con prueba objetiva bastante para contradicción efectiva.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "tipic" not in b and "incumplimiento" not in b:
        missing.append("tipicidad_incumplimiento")

    if "observ" not in b and "distancia" not in b:
        missing.append("condiciones_observacion")

    if "expediente" not in b and "archivo" not in b:
        missing.append("expediente_archivo")

    out: List[str] = []
    seen = set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out