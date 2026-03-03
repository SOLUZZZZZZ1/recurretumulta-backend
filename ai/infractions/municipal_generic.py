"""RTM — MUNICIPAL — GENÉRICO (MUN-GEN-1)"""
from __future__ import annotations
from typing import Any, Dict

def build_municipal_generic_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "INFRACCIÓN MUNICIPAL."
    asunto = "ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del Ayuntamiento competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — INSUFICIENCIA PROBATORIA Y MOTIVACIÓN\n\n"
        "Se solicita prueba suficiente y motivación individualizada, con identificación del lugar y soporte verificable.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}
