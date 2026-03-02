
"""
RTM — CONDICIONES DEL VEHÍCULO (SVL-CV-2) — DEMOLEDOR (Modo B por defecto)
(Archivo completo listo para sustituir el módulo de condiciones.)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import re

def _get_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(round(v))
        s = str(v).strip()
        if not s:
            return None
        s = s.replace("€", "").replace(".", "").replace(",", "").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None

def _is_grave(core: Dict[str, Any]) -> bool:
    core = core or {}
    fine = _get_int(core.get("sancion_importe_eur") or core.get("importe") or core.get("importe_total_multa"))
    pts = _get_int(core.get("puntos_detraccion") or core.get("puntos") or 0) or 0
    if fine is not None and fine >= 1000:
        return True
    if pts and pts > 0:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")

def _contains_any(text: str, needles: List[str]) -> bool:
    t = (text or "").lower()
    return any(n.lower() in t for n in needles)

def _clean_hecho(hecho: str) -> str:
    h = (hecho or "").strip()
    return re.sub(r"\s+", " ", h)

def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = _clean_hecho(core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO.")
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None and str(art).strip().isdigit() else None
    except Exception:
        art_i = None
    modo_c = _is_grave(core)

    blob = " ".join([
        hecho,
        str(core.get("raw_text_blob") or ""),
        str(core.get("raw_text_pdf") or ""),
        str(core.get("raw_text_vision") or ""),
    ]).lower()

    is_art15 = (art_i == 15)
    has_reflect = _contains_any(blob, ["reflect", "reflej", "como un espejo", "pulid", "deslumbr"])
    has_destellos = _contains_any(blob, ["destellos", "intermit", "emite luz", "luz roja", "senalizacion optica", "señalización óptica", "alumbrado"])

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    if is_art15:
        titulo = "ALEGACIÓN PRIMERA — ALUMBRADO/SEÑALIZACIÓN ÓPTICA (ART. 15): TIPICIDAD TÉCNICA Y PRUEBA OBJETIVA"
        enfoque = (
            "En infracciones relativas a dispositivos de alumbrado o señalización óptica, la imputación no puede basarse en "
            "fórmulas genéricas o apreciaciones subjetivas. Debe identificarse el dispositivo concreto, la norma técnica aplicable "
            "y aportarse soporte objetivo verificable.\n\n"
            "No consta acreditado en el expediente:\n"
            "1) Qué dispositivo exacto se considera irregular (ubicación y función).\n"
            "2) Qué precepto/anexo concreto se considera vulnerado y por qué.\n"
            "3) En qué consiste el incumplimiento (color/modo de emisión/intensidad).\n"
            "4) Medio de constatación y condiciones de observación.\n"
            "5) Soporte objetivo (fotos/vídeo) para contradicción efectiva.\n\n"
            "Sin identificación técnica y soporte verificable, procede el ARCHIVO por insuficiencia probatoria.\n"
        )
    else:
        titulo = "ALEGACIÓN PRIMERA — CONDICIONES REGLAMENTARIAS (ART. 12): PRUEBA TÉCNICA OBJETIVA Y NORMA CONCRETA"
        enfoque = (
            "La Administración debe acreditar defecto concreto + norma técnica concreta + soporte verificable.\n\n"
            "No consta acreditado:\n"
            "1) Defecto concreto imputado.\n"
            "2) Norma técnica/anexo concreto vulnerado.\n"
            "3) Medio de constatación y descripción circunstanciada.\n"
            "4) Soporte objetivo (fotos/medición/informe).\n\n"
            "En ausencia de acreditación suficiente, procede el ARCHIVO.\n"
        )

    extra = []
    if has_reflect:
        extra.append(
            "BLOQUE ESPECÍFICO — SUPERFICIE REFLECTANTE/DESLUMBRAMIENTO: BASE TÉCNICA Y SOPORTE\n\n"
            "Debe constar norma/anexo aplicable, método de verificación y soporte objetivo (fotos/vídeo/informe). "
            "La mera afirmación 'como un espejo' no suple prueba verificable.\n"
        )
    if has_destellos:
        extra.append(
            "BLOQUE ESPECÍFICO — LUZ ROJA/DESTELLOS: HOMOLOGACIÓN, FUNCIÓN Y PROHIBICIÓN CONCRETA\n\n"
            "Debe acreditarse función del dispositivo, autorización/homologación, precepto concreto infringido y soporte "
            "objetivo que muestre el modo de emisión.\n"
        )

    cierre_b = (
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA Y CARGA PROBATORIA\n\n"
        "La carga de la prueba corresponde a la Administración. La resolución debe motivar individualizadamente el encaje típico "
        "y aportar soporte verificable. De lo contrario, procede el archivo por insuficiencia probatoria.\n"
    )
    cierre_c = ""
    if modo_c:
        cierre_c = (
            "\nALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): LEGALIDAD, TIPICIDAD Y PRESUNCIÓN DE INOCENCIA\n\n"
            "En ausencia de identificación técnica y prueba suficiente, se vulneran garantías nucleares (art. 25 CE — legalidad/tipicidad; "
            "art. 24 CE — presunción de inocencia) y el deber de motivación, procediendo el archivo y, en su caso, la anulación por falta de motivación.\n"
        )

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        f"{titulo}\n\n"
        f"{enfoque}\n"
        + ("\n".join(extra) + "\n" if extra else "")
        + f"{cierre_b}\n"
        + (cierre_c if cierre_c else "")
        + "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica objetiva.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soportes (denuncia completa, fotos/vídeo/informe) con identificación del precepto/anexo concreto aplicado.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}
