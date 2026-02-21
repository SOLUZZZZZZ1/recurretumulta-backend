# -*- coding: utf-8 -*-
"""Score técnico v3.2 (0–100) — determinista, interno"""

from __future__ import annotations

import json
from typing import Any, Dict, List

def _blob(docs: List[Dict[str, Any]], extraction_core: Dict[str, Any]) -> str:
    parts = []
    try:
        parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass
    for d in docs or []:
        t = d.get("text_excerpt") or ""
        if t:
            parts.append(t)
    return " ".join(parts).lower()

def compute_velocity_strength_score(
    docs: List[Dict[str, Any]],
    extraction_core: Dict[str, Any],
    tipicity_verdict: Dict[str, Any],
    velocity_verdict: Dict[str, Any],
    velocity_calc: Dict[str, Any],
) -> Dict[str, Any]:
    score = 0
    reasons = []

    # A) Graduación (máx 40)
    mode = (velocity_verdict or {}).get("mode") or "unknown"
    if mode == "error_tramo":
        score += 40; reasons.append("error_tramo")
    elif mode == "incongruente":
        score += 20; reasons.append("incongruente_cuantia")
    elif mode == "unknown":
        score += 5; reasons.append("info_incompleta")

    # B) Metrología (máx 25): heurística por ausencia de señales
    b = _blob(docs, extraction_core)
    checks = [
        ("margen", ["margen"]),
        ("velocidad_corregida", ["velocidad corregida"]),
        ("certificado", ["certificado"]),
        ("verificacion", ["verificación", "verificacion"]),
        ("cinemometro", ["cinemómetro", "cinemometro", "radar"]),
        ("cadena_custodia", ["cadena de custodia"]),
    ]
    missing = 0
    for key, needles in checks:
        if not any(n in b for n in needles):
            missing += 1
            reasons.append(f"missing_{key}")
    score += min(25, missing * 4)

    # C) Tipicidad (máx 15)
    m = (tipicity_verdict or {}).get("match")
    if m is False:
        score += 15; reasons.append("tipicidad_mismatch")
    elif m is None:
        score += 5; reasons.append("tipicidad_unknown")

    # D) Prueba material (máx 10)
    pm = 0
    if not any(k in b for k in ["fotograma", "captura", "imagen", "fotografía", "fotografia"]):
        pm += 1; reasons.append("missing_fotograma")
    if not any(k in b for k in ["punto kilom", "pk", "kilómetro", "kilometro", "ubicación", "ubicacion"]):
        pm += 1; reasons.append("missing_ubicacion")
    score += min(10, pm * 5)

    # E) Robustez matemática (máx 10)
    try:
        if (velocity_calc or {}).get("ok"):
            if mode == "error_tramo":
                score += 10; reasons.append("margen_cambia_tramo")
            else:
                score += 5; reasons.append("margen_relevante")
    except Exception:
        pass

    score = max(0, min(100, int(score)))

    if score >= 85:
        label = "DEMOLEDOR"
    elif score >= 70:
        label = "MUY FUERTE"
    elif score >= 55:
        label = "SÓLIDO"
    elif score >= 35:
        label = "DEFENDIBLE"
    else:
        label = "TÉCNICO DÉBIL"

    return {"score": score, "label": label, "reasons": reasons}
