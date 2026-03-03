"""RTM — SEMÁFORO (SVL-SEM-4) — DEMOLEDOR 9.5/10 (Enfoque operativo)

Modo B por defecto (maximiza archivo real).
Modo C solo graves (puntos/sanción alta).
Compatibilidad: build_semaforo_strong_template(core)
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
    if pts and pts > 0:
        return True
    if fine is not None and fine >= 500:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def build_semaforo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    modo_c = _is_grave(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO RESPETAR LA LUZ ROJA (SEMÁFORO)."

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
        "ALEGACIÓN PRIMERA — ELEMENTO OBJETIVO: FASE ROJA ACTIVA Y REBASE EFECTIVO\n\n"
        "Para sancionar por no respetar la luz roja no intermitente debe acreditarse de forma objetiva y verificable:\n"
        "1) Que existía FASE ROJA ACTIVA en el instante exacto del supuesto rebase.\n"
        "2) Que el vehículo rebasó efectivamente la LÍNEA DE DETENCIÓN con la fase roja ya activa.\n"
        "3) Que no se trataba de fase ámbar o transición del ciclo semafórico.\n"
        "4) Identificación inequívoca del vehículo y correspondencia temporal exacta del registro.\n\n"
        "No consta acreditación suficiente de dichos extremos con soporte verificable, por lo que no puede tenerse por probado el hecho infractor.\n\n"
        "ALEGACIÓN SEGUNDA — SECUENCIA ÍNTEGRA, SIN RECORTES, Y SINCRONIZACIÓN HORARIA\n\n"
        "En captación automática, no basta un fotograma aislado o recortado. Se requiere secuencia completa (mínimo dos/tres imágenes o vídeo) "
        "que permita verificar fase roja efectiva, posición del vehículo respecto de la línea de detención y cronometría.\n\n"
        "Debe aportarse también documentación técnica del sistema (homologación/certificación del dispositivo y del conjunto semáforo-captación), "
        "y acreditación de sincronización horaria y correcto funcionamiento en la fecha del hecho.\n\n"
        "En observación por agente, debe detallarse posición, distancia, ángulo, visibilidad y circunstancias que permitan verificar que el rebase se produjo con fase roja activa (no ámbar).\n\n"
        "ALEGACIÓN TERCERA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "La resolución debe contener motivación individualizada, evitando fórmulas estereotipadas, identificando instante exacto, ciclo del semáforo, rebase de la línea de detención y soporte probatorio aportado.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): EXIGENCIA REFORZADA DE PRUEBA\n\n"
            "Cuando la sanción incorpora pérdida de puntos o especial gravedad, la exigencia de prueba verificable y motivación es máxima. "
            "En ausencia de secuencia íntegra, sincronización y acreditación técnica del sistema, procede el archivo y, en su caso, la anulación por falta de motivación suficiente.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa (secuencia íntegra sin recortes, homologación/certificación, sincronización horaria y motivación detallada).\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "fase roja" not in b and "roja activa" not in b:
        missing.append("fase_roja")
    if "secuencia" not in b:
        missing.append("secuencia")
    if "sincron" not in b:
        missing.append("sincronizacion")
    if "línea de detención" not in b and "linea de detencion" not in b:
        missing.append("linea_detencion")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
