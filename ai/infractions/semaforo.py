"""
RTM — SEMÁFORO STRONG MODULE (SVL-SEM-1)

Determinista, sin OpenAI.
- Exige fase roja + línea de detención + secuencia completa.
- Pide expediente íntegro y motivación reforzada.
- Solicita ARCHIVO.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List


def is_semaforo_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = (body or "").lower()
    hecho = str(core.get("hecho_imputado") or "").lower()

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "semaforo":
        return True

    # señales robustas (OCR sucio incluido)
    signals = [
        "semáforo", "semaforo", "fase roja", "luz roja",
        "cruce en rojo", "cruce con fase roja",
        "t/s roja", "ts roja",
        "señal luminosa roja", "senal luminosa roja",
        "línea de detención", "linea de detencion",
    ]
    blob = b + "\n" + hecho
    if any(s in blob for s in signals):
        return True

    # artículo típico
    precepts = " ".join([str(x) for x in (core.get("preceptos_detectados") or [])]).lower()
    if "146" in precepts or re.search(r"\bart\.?\s*146\b", blob):
        return True

    return False


def build_semaforo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
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
        "ALEGACIÓN PRIMERA — PRUEBA OBJETIVA DEL HECHO: FASE ROJA Y LÍNEA DE DETENCIÓN\n\n"
        "Para sancionar por no respetar la luz roja no intermitente de un semáforo debe acreditarse de forma objetiva y verificable:\n"
        "1) Que existía fase roja activa en el instante exacto del supuesto cruce/rebase.\n"
        "2) La posición del vehículo respecto a la línea de detención.\n"
        "3) La identificación inequívoca del vehículo y la correspondencia entre registro/captura y el vehículo denunciado.\n\n"
        "No consta aportada prueba completa que permita dicha verificación, por lo que no puede tenerse por probado el hecho infractor.\n\n"
        "ALEGACIÓN SEGUNDA — SECUENCIA/IMÁGENES COMPLETAS, SIN RECORTES, Y MOTIVACIÓN REFORZADA\n\n"
        "Se solicita la aportación de la secuencia/fotogramas completos (sin recortes) y, en su caso, acreditación del sistema de captación "
        "y su sincronización. Igualmente, se interesa denuncia/acta completa con motivación individualizada (ubicación exacta, instante del cruce, "
        "línea de detención y circunstancias), evitando fórmulas estereotipadas.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del hecho.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if not any(k in b for k in ["fase roja", "luz roja"]):
        missing.append("fase_roja")
    if not any(k in b for k in ["línea de detención", "linea de detencion"]):
        missing.append("linea_detencion")
    if not any(k in b for k in ["secuencia", "fotograma", "captura", "imagen", "vídeo", "video"]):
        missing.append("secuencia_o_soportes")
    if "archivo" not in b:
        missing.append("archivo")
    return list(dict.fromkeys(missing))