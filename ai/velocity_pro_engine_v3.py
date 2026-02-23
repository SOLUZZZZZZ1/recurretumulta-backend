# -*- coding: utf-8 -*-
"""Velocity PRO Engine v3 (prudente pero quirúrgico)

- Construye un veredicto determinista para sanciones de velocidad.
- No inventa hechos.
- Clasifica en: correcto / error_tramo / incongruente / unknown
- Produce bloques de texto prudentes para insertar en el escrito (postprocesado o prompt condicionado).

Dependencias: ninguna externa (solo stdlib).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

STANDARD_SPEED_FINES = {100, 300, 400, 500, 600}
STANDARD_SPEED_POINTS = {0, 2, 4, 6}

def _try_int(x: Any) -> Optional[int]:
    try:
        if x is None or isinstance(x, bool):
            return None
        if isinstance(x, int):
            return int(x)
        s = str(x).strip()
        s = s.replace(".", "").replace(",", ".")
        s = re.sub(r"[^0-9\-]", "", s)
        if s in ("", "-"):
            return None
        return int(s)
    except Exception:
        return None

def _extract_imposed_from_extraction(extraction_core: Dict[str, Any]) -> Dict[str, Optional[int]]:
    fine = _try_int((extraction_core or {}).get("sancion_importe_eur"))
    pts = _try_int((extraction_core or {}).get("puntos_detraccion"))
    src = "extraction_core" if (fine is not None or pts is not None) else None
    return {"fine": fine, "points": pts, "source": src}

def _extract_imposed_from_docs(docs: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    blob = " ".join([(d.get("text_excerpt") or "") for d in (docs or [])]).lower()
    fine = None
    points = None

    euros = re.findall(r"\b(\d{2,4})\s*€\b|\b(\d{2,4})€\b", blob)
    euro_vals = []
    for a, b in euros:
        v = a or b
        if v and v.isdigit():
            euro_vals.append(int(v))
    euro_uniq = sorted(set([v for v in euro_vals if 10 <= v <= 5000]))
    if len(euro_uniq) == 1:
        fine = euro_uniq[0]

    pts_hits = re.findall(r"\b(\d)\s*puntos\b|\bpuntos\s*(?:a\s*detraer)?\s*[:\-]?\s*(\d)\b", blob)
    pts_vals = []
    for a, b in pts_hits:
        v = a or b
        if v and v.isdigit():
            pts_vals.append(int(v))
    pts_uniq = sorted(set([v for v in pts_vals if 0 <= v <= 6]))
    if len(pts_uniq) == 1:
        points = pts_uniq[0]

    src = "docs" if (fine is not None or points is not None) else None
    return {"fine": fine, "points": points, "source": src}

def extract_imposed(docs: List[Dict[str, Any]], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    a = _extract_imposed_from_extraction(extraction_core or {})
    if a.get("fine") is not None or a.get("points") is not None:
        return a
    b = _extract_imposed_from_docs(docs or [])
    if b.get("fine") is not None or b.get("points") is not None:
        return b
    return {"fine": None, "points": None, "source": None}

def build_velocity_verdict(docs: List[Dict[str, Any]], extraction_core: Dict[str, Any], velocity_calc: Dict[str, Any]) -> Dict[str, Any]:
    verdict: Dict[str, Any] = {
        "ok": False,
        "mode": "unknown",
        "dominant_argument": "metrologia",
        "severity_level": "normal",
        "tramo_error": False,
        "fine_mismatch": False,
        "points_mismatch": False,
        "needs_prudence": True,
        "imposed": {"fine": None, "points": None, "source": None},
        "expected": {"fine": None, "points": None, "band": None, "table_limit": None, "corrected_int": None},
        "notes": "",
        "directives": {"primary_override": None, "reorder_alegaciones": False, "insert_blocks": []},
    }

    if not isinstance(velocity_calc, dict) or not velocity_calc.get("ok"):
        verdict["notes"] = "velocity_calc_no_ok"
        return verdict

    expected = (velocity_calc.get("expected") or {})
    if not isinstance(expected, dict):
        verdict["notes"] = "expected_not_dict"
        return verdict

    verdict["expected"] = {
        "fine": expected.get("fine"),
        "points": expected.get("points"),
        "band": expected.get("band"),
        "table_limit": expected.get("table_limit"),
        "corrected_int": expected.get("corrected_int"),
    }

    imposed = extract_imposed(docs, extraction_core)
    verdict["imposed"] = imposed

    exp_fine = _try_int(expected.get("fine"))
    exp_pts = _try_int(expected.get("points"))
    imp_fine = _try_int(imposed.get("fine"))
    imp_pts = _try_int(imposed.get("points"))

    verdict["ok"] = True

    if imp_fine is None and imp_pts is None:
        verdict["mode"] = "unknown"
        verdict["severity_level"] = "reforzado"
        verdict["notes"] = "imposed_missing"
        return verdict

    fine_mismatch = (imp_fine is not None and exp_fine is not None and imp_fine != exp_fine)
    pts_mismatch = (imp_pts is not None and exp_pts is not None and imp_pts != exp_pts)
    verdict["fine_mismatch"] = bool(fine_mismatch)
    verdict["points_mismatch"] = bool(pts_mismatch)

    if (not fine_mismatch) and (not pts_mismatch):
        verdict["mode"] = "correcto"
        verdict["severity_level"] = "reforzado"
        verdict["notes"] = "match_ok"
        return verdict

    verdict["severity_level"] = "critico"

    if imp_fine is not None and imp_fine in STANDARD_SPEED_FINES:
        verdict["mode"] = "error_tramo"
        verdict["dominant_argument"] = "tramo"
        verdict["tramo_error"] = True
        verdict["directives"]["primary_override"] = "ERROR_TRAMO"
        verdict["directives"]["reorder_alegaciones"] = True
        verdict["directives"]["insert_blocks"] = ["bloque_error_tramo_prudente"]
        verdict["notes"] = "std_fine_mismatch_error_tramo"
        return verdict

    verdict["mode"] = "incongruente"
    verdict["dominant_argument"] = "motivacion_tipo"
    verdict["directives"]["primary_override"] = "INCONGRUENCIA"
    verdict["directives"]["reorder_alegaciones"] = True
    verdict["directives"]["insert_blocks"] = ["bloque_incongruencia_cuantia_prudente"]
    verdict["notes"] = "nonstd_fine_or_complex_context"
    return verdict

def build_prudente_text_blocks(verdict: Dict[str, Any], velocity_calc: Dict[str, Any]) -> Dict[str, str]:
    mode = (verdict or {}).get("mode") or "unknown"
    imposed = (verdict or {}).get("imposed") or {}
    expected = (verdict or {}).get("expected") or {}
    imp_fine = imposed.get("fine")
    imp_pts = imposed.get("points")
    exp_fine = expected.get("fine")
    exp_pts = expected.get("points")
    band = expected.get("band")

    corrected = (velocity_calc or {}).get("corrected")
    limit = (velocity_calc or {}).get("limit")
    measured = (velocity_calc or {}).get("measured")
    margin_value = (velocity_calc or {}).get("margin_value")

    blocks: Dict[str, str] = {}

    if mode == "error_tramo":
        blocks["primary_title"] = "ALEGACIÓN PRIMERA — POSIBLE ERROR DE GRADUACIÓN SANCIONADORA Y TRAMO INDEBIDAMENTE APLICADO"
        blocks["primary_body"] = (
            "Sin perjuicio de la prueba que corresponde a la Administración, la aplicación del margen legal podría situar la velocidad corregida "
            "en un tramo distinto al finalmente sancionado, lo que exige motivación reforzada y acreditación técnica completa. "
            "No consta acreditado el criterio de graduación aplicado ni su encaje exacto en el tramo sancionador correspondiente."
        )
        if all(x is not None for x in [limit, measured, margin_value, corrected]):
            blocks["primary_calc_paragraph"] = (
                f"A efectos ilustrativos, con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin_value} km/h, "
                f"la velocidad corregida se situaría en torno a {corrected} km/h, extremo cuya acreditación corresponde a la Administración."
            )
        else:
            blocks["primary_calc_paragraph"] = (
                "A efectos ilustrativos, la aplicación del margen legal puede modificar la velocidad corregida y, por tanto, el tramo sancionador; "
                "corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda resultante."
            )
        if imp_fine is not None or imp_pts is not None:
            blocks["primary_mismatch_line"] = (
                f"En particular, la sanción impuesta ({imp_fine}€ / {imp_pts} puntos, si consta) no resulta coherente con el tramo orientativo "
                f"derivado de la velocidad corregida ({band} — {exp_fine}€ / {exp_pts} puntos, si procede)."
            )
        blocks["bridge_to_metrology"] = (
            "Lo anterior se conecta directamente con la exigencia de prueba técnica completa del cinemómetro, margen aplicado y cadena de custodia del dato."
        )
        return blocks

    if mode == "incongruente":
        blocks["primary_title"] = "ALEGACIÓN PRIMERA — EXIGENCIA DE MOTIVACIÓN Y CLARIFICACIÓN DEL CRITERIO DE CUANTIFICACIÓN (INDEFENSIÓN)"
        blocks["primary_body"] = (
            "No consta acreditado el criterio jurídico-técnico seguido para la cuantificación de la sanción ni su correspondencia con el tramo legal aplicable, "
            "impidiendo verificar la correcta tipificación y graduación. En ausencia de motivación individualizada y de acreditación completa del margen/velocidad corregida, "
            "se genera indefensión material."
        )
        if imp_fine is not None or imp_pts is not None:
            blocks["primary_mismatch_line"] = (
                f"Con los elementos disponibles, la cuantía impuesta ({imp_fine}€) y/o los puntos ({imp_pts}) no permiten verificar su encaje con un tramo concreto "
                f"derivado de la velocidad corregida ({band}, si procede), por lo que se solicita aclaración y expediente íntegro."
            )
        blocks["bridge_to_metrology"] = (
            "En todo caso, debe exigirse la acreditación documental del control metrológico, margen aplicado, velocidad corregida, captura/fotograma completo e integridad/cadena de custodia."
        )
        return blocks

    return blocks
