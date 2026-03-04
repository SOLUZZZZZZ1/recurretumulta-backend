"""RTM — CONDICIONES DEL VEHÍCULO (ART. 12/15 + RGV 2822/98) — DEMOLEDOR ESPECÍFICO

Compatibilidad: build_condiciones_vehiculo_strong_template(core)
NO introduce bloques de atención/ciclistas.
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any]) -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("hecho_imputado", "raw_text_blob", "raw_text_pdf", "raw_text_vision"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return "\n".join(parts).lower()


def _has_any(b: str, needles: List[str]) -> bool:
    return any(n in b for n in needles)


def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    b = _blob(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    # Submotivos
    is_alumbrado = _has_any(b, ["alumbrado", "señalización óptica", "senalizacion optica", "luz roja", "destello", "destellos", "anexo ii", "anexo i"]) or _has_any(b, ["emite luz", "intermit", "destell"])
    is_reflect = _has_any(b, ["reflect", "reflej", "pulid", "como un espejo", "deslumbr"])
    is_neumaticos = _has_any(b, ["neumático", "neumatico", "banda de rodadura", "dibujo", "liso", "desgast"])
    is_itv = _has_any(b, ["itv", "inspección técnica", "inspeccion tecnica", "caducad"])
    is_reformas = _has_any(b, ["reforma", "homolog", "proyecto", "certificado", "modificación", "modificacion"])

    parts: List[str] = []
    parts.append(
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA OBJETIVA Y NORMA CONCRETA\n\n"
        "Debe acreditarse el defecto concreto imputado, el precepto/anexo/apartado técnico concreto vulnerado, el método de constatación y soporte objetivo verificable (fotos/vídeo/medición/informe).\n"
        "La referencia genérica a 'condiciones reglamentarias' o a un anexo sin precisar el apartado concreto impide subsunción típica y defensa efectiva.\n\n"
    )

    if is_alumbrado:
        parts.append(
            "BLOQUE ESPECÍFICO — ALUMBRADO/SEÑALIZACIÓN ÓPTICA: DESTELLOS, FUNCIÓN Y HOMOLOGACIÓN\n\n"
            "Si se afirma una luz trasera roja que emite destellos, debe acreditarse: (i) qué dispositivo exacto es (función y ubicación), (ii) qué apartado concreto del Anexo aplicable se considera infringido y por qué, (iii) si está homologado/autorizado y cómo se verificó, y (iv) soporte objetivo que muestre el modo de emisión (destello/intermitencia) sin recortes.\n\n"
            "No basta una apreciación visual genérica sobre 'destellos' si no se identifica el requisito técnico concreto incumplido.\n\n"
        )

    if is_reflect:
        parts.append(
            "BLOQUE ESPECÍFICO — SUPERFICIE REFLECTANTE/DESLUMBRAMIENTO\n\n"
            "Debe constar norma/anexo aplicable, método de verificación y soporte objetivo. La afirmación 'refleja como un espejo' requiere acreditación verificable (condiciones de luz/ángulo y registro gráfico).\n\n"
        )

    if is_neumaticos:
        parts.append(
            "BLOQUE ESPECÍFICO — NEUMÁTICOS: MEDICIÓN OBJETIVA\n\n"
            "Debe indicarse rueda/posición, defecto concreto y medición objetiva. Sin medición y soporte verificable, la imputación es genérica.\n\n"
        )

    if is_itv:
        parts.append(
            "BLOQUE ESPECÍFICO — ITV: ACREDITACIÓN DOCUMENTAL\n\n"
            "Debe acreditarse documentalmente el estado administrativo del vehículo en la fecha del hecho (consulta registral/estado ITV). Sin constancia documental, no puede tenerse por probado el incumplimiento.\n\n"
        )

    if is_reformas:
        parts.append(
            "BLOQUE ESPECÍFICO — REFORMAS/HOMOLOGACIÓN\n\n"
            "Debe identificarse la reforma concreta y el requisito técnico presuntamente incumplido, con comprobación documental (homologación/proyecto/certificado). La mera apreciación visual no basta.\n\n"
        )

    parts.append(
        "ALEGACIÓN FINAL — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita expediente íntegro con identificación del precepto/anexo concreto aplicado y soporte técnico completo para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica objetiva.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico verificable.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}
