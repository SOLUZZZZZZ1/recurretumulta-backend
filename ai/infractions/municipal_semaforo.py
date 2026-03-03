"""RTM — MUNICIPAL — SEMÁFORO (MUN-SEM-1) — DEMOLEDOR OPERATIVO"""
from __future__ import annotations
from typing import Any, Dict

def build_municipal_semaforo_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "NO RESPETAR LA LUZ ROJA (SEMÁFORO)."
    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""
    asunto = "ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del Ayuntamiento competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FASE ROJA ACTIVA Y REBASE EFECTIVO\n\n"
        "Debe acreditarse fase roja activa en el instante exacto y rebase de línea de detención.\n\n"
        "ALEGACIÓN SEGUNDA — SECUENCIA ÍNTEGRA Y SINCRONIZACIÓN\n\n"
        "Se solicita secuencia íntegra sin recortes (imágenes/vídeo), sincronización horaria y la identificación del cruce, carril, semáforo y línea aplicable.\n"
        "Asimismo, acreditación de mantenimiento y correcto funcionamiento del sistema municipal en la fecha del hecho.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita expediente íntegro y motivación individualizada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa (secuencia, sincronización, identificación del cruce/semáforo).\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}
