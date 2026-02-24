"""
RTM — SEMÁFORO STRONG MODULE (SVL-SEM-3)

Nivel quirúrgico máximo.

Exige:
- Fase roja activa exacta
- Rebase de línea de detención
- Secuencia completa sin recortes
- Sincronización y certificación del sistema
- Diferenciación fase roja vs ámbar
- Motivación individualizada
- Archivo
"""

from __future__ import annotations
from typing import Any, Dict


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

        "ALEGACIÓN PRIMERA — ACREDITACIÓN DE FASE ROJA ACTIVA Y REBASE EFECTIVO\n\n"
        "Para sancionar por no respetar la luz roja no intermitente de un semáforo "
        "debe acreditarse de forma objetiva, técnica y verificable:\n"
        "1) Que existía fase roja activa en el instante exacto del supuesto rebase.\n"
        "2) Que el vehículo rebasó completamente la línea de detención con la fase roja ya activa.\n"
        "3) Que no se trataba de fase ámbar o transición del ciclo semafórico.\n"
        "4) Identificación inequívoca del vehículo y correspondencia temporal exacta.\n\n"

        "No consta acreditación suficiente de dichos extremos, "
        "por lo que no puede tenerse por probado el hecho infractor.\n\n"

        "ALEGACIÓN SEGUNDA — SECUENCIA ÍNTEGRA, SIN RECORTES Y SINCRONIZACIÓN DEL SISTEMA\n\n"
        "En caso de captación automática, debe aportarse:\n"
        "1) Secuencia completa de imágenes o vídeo sin recortes.\n"
        "2) Certificación del sistema de captación y su homologación.\n"
        "3) Sincronización horaria del dispositivo con el ciclo semafórico.\n"
        "4) Identificación del cruce y configuración del ciclo en el instante exacto.\n\n"

        "En caso de observación por agente, debe detallarse posición, visibilidad y circunstancias "
        "que permitan verificar que el rebase se produjo con fase roja activa.\n\n"

        "ALEGACIÓN TERCERA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "La resolución debe contener motivación individualizada y no fórmulas estereotipadas, "
        "especificando instante exacto, ciclo del semáforo y rebase de la línea de detención.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba técnica completa.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}