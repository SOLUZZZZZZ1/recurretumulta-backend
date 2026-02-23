# -*- coding: utf-8 -*-
"""RTM — Velocity Decision Engine (v1)

Objetivo:
- Decidir el "modo" jurídico antes de redactar el recurso:
  A) inexistencia_infraccion
  B) error_tramo
  C) probatorio_puro
  D) falta_cuantificacion
  E) unknown (datos insuficientes)

Este módulo NO genera el escrito. Solo devuelve un veredicto estructurado.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple, List


STANDARD_SPEED_FINES = {100, 200, 300, 400, 500, 600}
STANDARD_SPEED_POINTS = {0, 2, 4, 6}


def _to_int(x: Any) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    s = str(x).strip()
    s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9\-]", "", s)
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except Exception:
        return None


def _speed_margin_value(measured: int, capture_mode: str = "UNKNOWN") -> float:
    """Margen conservador conforme Orden ICT/155/2020.
    - Estático/fijo: <=100 → 5 km/h; >100 → 5%
    - Móvil (si se conoce): <=100 → 7 km/h; >100 → 7%
    Si no se conoce modo, usa fijo (más favorable).
    """
    cm = (capture_mode or "UNKNOWN").upper()
    mobile = cm in ("MOBILE", "MOVING", "VEHICLE", "AGENT")
    if measured <= 100:
        return 7.0 if mobile else 5.0
    pct = 0.07 if mobile else 0.05
    return round(measured * pct, 2)


def _dgt_speed_sanction_table() -> Dict[int, List[Tuple[int, int, int, int, str]]]:
    """Tabla DGT por límite (rangos inclusivos)."""
    return {
        20: [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],
        30: [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],
        40: [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],
        50: [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (101,999,600,6,'600€ 6 puntos')],
        60: [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],
        70: [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],
        80: [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],
        90: [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],
        100:[(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],
        110:[(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],
        120:[(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
    }


def _expected_speed_sanction(limit: int, corrected: float) -> Dict[str, Any]:
    tbl = _dgt_speed_sanction_table()
    lim = int(limit) if int(limit) in tbl else None
    if lim is None:
        return {"fine": None, "points": None, "band": None, "table_limit": None, "corrected_int": int(round(corrected))}
    v = int(round(corrected))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}


def decide_modo_velocidad(core: Dict[str, Any], body: str = "", capture_mode: str = "UNKNOWN") -> Dict[str, Any]:
    """Decide el modo jurídico de VELOCIDAD."""
    reasons: List[str] = []

    measured = _to_int((core or {}).get("velocidad_medida_kmh"))
    limit = _to_int((core or {}).get("velocidad_limite_kmh"))

    # Fallback: intentar sacar números del body si core no los tiene
    if (measured is None or limit is None) and body:
        t = body.lower().replace("\n", " ")
        m = re.search(r"circular\s+a\s+(\d{2,3})\s*km\s*/?h[\s\S]{0,240}?(?:limitad|l[ií]mite|velocidad\s+m[aá]xima)[^\d]{0,60}(\d{2,3})", t)
        if m:
            if measured is None:
                measured = _to_int(m.group(1))
                reasons.append("measured_from_body")
            if limit is None:
                limit = _to_int(m.group(2))
                reasons.append("limit_from_body")

    imposed_fine = _to_int((core or {}).get("sancion_importe_eur"))
    imposed_points = _to_int((core or {}).get("puntos_detraccion"))

    if measured is None or limit is None:
        return {
            "mode": "unknown",
            "reasons": ["missing_speed_or_limit"] + reasons,
            "measured": measured,
            "limit": limit,
            "imposed": {"fine": imposed_fine, "points": imposed_points},
            "expected": {"fine": None, "points": None, "band": None},
        }

    margin = _speed_margin_value(int(measured), capture_mode=capture_mode)
    corrected = round(max(0.0, float(measured) - float(margin)), 2)
    exceso = round(float(corrected) - float(limit), 2)
    expected = _expected_speed_sanction(int(limit), corrected)

    # A) inexistencia infracción
    if corrected <= float(limit):
        return {
            "mode": "inexistencia_infraccion",
            "reasons": ["corrected_below_or_equal_limit"] + reasons,
            "measured": int(measured),
            "limit": int(limit),
            "margin": margin,
            "corrected": corrected,
            "exceso": exceso,
            "imposed": {"fine": imposed_fine, "points": imposed_points},
            "expected": expected,
        }

    # D) falta cuantificación
    if imposed_fine is None and imposed_points is None:
        return {
            "mode": "falta_cuantificacion",
            "reasons": ["missing_imposed_fine_and_points"] + reasons,
            "measured": int(measured),
            "limit": int(limit),
            "margin": margin,
            "corrected": corrected,
            "exceso": exceso,
            "imposed": {"fine": imposed_fine, "points": imposed_points},
            "expected": expected,
        }

    # B) error tramo
    exp_f = expected.get("fine")
    exp_p = expected.get("points")
    mismatch = False
    if imposed_fine is not None and exp_f is not None and imposed_fine != exp_f:
        mismatch = True
        reasons.append("fine_mismatch")
    if imposed_points is not None and exp_p is not None and imposed_points != exp_p:
        mismatch = True
        reasons.append("points_mismatch")

    if mismatch and (imposed_fine in STANDARD_SPEED_FINES or imposed_points in STANDARD_SPEED_POINTS):
        return {
            "mode": "error_tramo",
            "reasons": reasons or ["mismatch"],
            "measured": int(measured),
            "limit": int(limit),
            "margin": margin,
            "corrected": corrected,
            "exceso": exceso,
            "imposed": {"fine": imposed_fine, "points": imposed_points},
            "expected": expected,
        }

    # C) probatorio puro
    return {
        "mode": "probatorio_puro",
        "reasons": ["tramo_ok_or_nonstandard_quantification"] + reasons,
        "measured": int(measured),
        "limit": int(limit),
        "margin": margin,
        "corrected": corrected,
        "exceso": exceso,
        "imposed": {"fine": imposed_fine, "points": imposed_points},
        "expected": expected,
    }
