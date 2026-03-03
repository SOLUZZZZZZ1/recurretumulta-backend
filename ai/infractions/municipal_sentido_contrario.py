"""RTM — MUNICIPAL — SENTIDO CONTRARIO (MUN-SEN-1) — DEMOLEDOR OPERATIVO"""
from __future__ import annotations
from typing import Any, Dict

def build_municipal_sentido_contrario_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "CIRCULACIÓN EN SENTIDO CONTRARIO."
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
        "ALEGACIÓN PRIMERA — TRAMO EXACTO Y SEÑALIZACIÓN\n\n"
        "Debe precisarse tramo exacto, configuración de la vía y señalización vertical/horizontal aplicable, con visibilidad real (obras/desvíos/obstáculos).\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA OBJETIVA\n\n"
        "Se solicita soporte objetivo (fotos/vídeo/croquis). Sin soporte verificable, la imputación queda en afirmación genérica.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}
