"""
RTM — Generic infraction fallback module (Render-safe).

Goal:
  - Provide deterministic, safe fallback text when a specific infraction module
    is not available or fails.
  - Never raise at import time.
  - No external dependencies besides stdlib typing.

This module is designed for "Commit 1" (no engine changes yet).
"""

from typing import Any, Dict, List


def strict_validate(body: str, **kwargs: Any) -> List[str]:
    """Generic minimal validation (always passes unless body is empty)."""
    missing: List[str] = []
    if not body or not str(body).strip():
        missing.append("body_empty")
    return missing


def build_generic_body(core: Dict[str, Any], has_image_evidence: bool = False) -> Dict[str, str]:
    """Build a deterministic generic 'alegaciones' body.
    This is a safe fallback. It does not invent facts.
    """
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "HECHO NO DETERMINADO (falta concreción en la documentación aportada)."

    if has_image_evidence:
        evid = "Consta la posible existencia de material gráfico asociado; se solicita su aportación íntegra y sin recortes."
    else:
        evid = "No se aporta prueba objetiva (fotografía/secuencia) ni expediente íntegro; se solicita su aportación."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n"
        "ALEGACIÓN PRIMERA — INSUFICIENCIA PROBATORIA Y FALTA DE CONCRECIÓN\n\n"
        "En Derecho sancionador, la carga de la prueba corresponde a la Administración. "
        "No basta una afirmación genérica del hecho: debe aportarse soporte probatorio suficiente y motivación individualizada "
        "que permita el ejercicio del derecho de defensa.\n\n"
        f"{evid}\n\n"
        "ALEGACIÓN SEGUNDA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se interesa copia íntegra del expediente administrativo (denuncia/boletín, prueba aportada, propuesta y resolución si existieran), "
        "así como identificación expresa del precepto aplicado (artículo/apartado) y motivación completa de la subsunción.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del hecho.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}
