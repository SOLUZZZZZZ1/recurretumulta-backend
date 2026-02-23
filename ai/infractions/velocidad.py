"""
RTM — Módulo VELOCIDAD (VSE) — Render-safe

Objetivo:
- Separar la lógica de velocidad del engine principal (ai/expediente_engine.py).
- Evitar falsos positivos de importe impuesto (p.ej. 'BMW 120D' → '1200€').
- Mantener un cálculo interno determinista de margen y tramo (tabla DGT) y
  permitir inyecciones de párrafos (cálculo + posible error de tramo) solo cuando
  los datos sean confiables.

NOTA: Este módulo NO hace llamadas a OpenAI. Es 100% determinista.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# --------------------------
# Tabla DGT (bandas) — captación por cinemómetro
# Rangos inclusivos.
# --------------------------
def dgt_speed_sanction_table() -> Dict[int, List[tuple]]:
    return {
        20: [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],
        30: [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],
        40: [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],
        50: [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (121,999,600,6,'600€ 6 puntos')],
        60: [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],
        70: [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],
        80: [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],
        90: [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],
        100:[(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],
        110:[(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],
        120:[(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
    }


def expected_speed_sanction(limit: int, corrected: float) -> Dict[str, Any]:
    tbl = dgt_speed_sanction_table()
    lim = int(limit) if int(limit) in tbl else None
    if lim is None:
        return {"fine": None, "points": None, "band": None, "table_limit": None}
    v = int(round(float(corrected)))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}


# --------------------------
# Márgenes (ICT/155/2020) — conservador
# --------------------------
def speed_margin_value(measured: int, capture_mode: str = "UNKNOWN") -> float:
    cm = (capture_mode or "").upper()
    mobile = cm in ("MOBILE", "MOVING", "VEHICLE", "AGENT")
    if int(measured) <= 100:
        return 7.0 if mobile else 5.0
    pct = 0.07 if mobile else 0.05
    return round(float(measured) * pct, 2)


# --------------------------
# Sanitización de importes (evita 120D -> 1200)
# --------------------------
def sanitize_imposed_fine(value: Any) -> Optional[int]:
    """Devuelve int si el importe es plausible. Si no, None.

    Reglas:
    - Acepta solo {100,200,300,400,500,600} como sanciones administrativas típicas de radar.
    - Rechaza valores > 600 (p.ej. 1200) para evitar falsos positivos por OCR/matrícula/modelo.
    """
    try:
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            # Si contiene letras (ej. 120D), lo rechazamos
            if re.search(r"[A-Za-z]", v):
                return None
            # Quitar separadores comunes
            v = v.replace(".", "").replace(",", "").replace("€", "").strip()
            if not v.isdigit():
                return None
            value = int(v)
        if isinstance(value, (int, float)):
            iv = int(round(float(value)))
        else:
            return None

        allowed = {100, 200, 300, 400, 500, 600}
        if iv in allowed:
            return iv
        # Rechazar 0, negativos y >600
        return None
    except Exception:
        return None


def sanitize_imposed_points(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            if not v or not v.isdigit():
                return None
            value = int(v)
        if isinstance(value, (int, float)):
            iv = int(round(float(value)))
        else:
            return None
        if 0 <= iv <= 6:
            return iv
        return None
    except Exception:
        return None


# --------------------------
# Cálculo VSE desde core estructurado
# --------------------------
def compute_velocity_calc_from_core(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> Dict[str, Any]:
    """Calcula margen, velocidad corregida y sanción esperada.
    No inventa: si faltan datos, devuelve ok=False.
    """
    try:
        measured = core.get("velocidad_medida_kmh")
        limit = core.get("velocidad_limite_kmh")

        # Parse ints
        if isinstance(measured, str) and measured.strip().isdigit():
            measured = int(measured.strip())
        if isinstance(limit, str) and limit.strip().isdigit():
            limit = int(limit.strip())

        if not isinstance(measured, int) or not isinstance(limit, int):
            return {"ok": False, "reason": "missing_measured_or_limit"}

        margin = speed_margin_value(measured, capture_mode=capture_mode)
        corrected = max(0.0, float(measured) - float(margin))
        expected = expected_speed_sanction(int(limit), corrected)

        imposed_fine = sanitize_imposed_fine(core.get("sancion_importe_eur"))
        imposed_pts = sanitize_imposed_points(core.get("puntos_detraccion"))

        mismatch = False
        mismatch_reasons: List[str] = []
        if isinstance(imposed_fine, int) and isinstance(expected.get("fine"), int) and imposed_fine != expected.get("fine"):
            mismatch = True
            mismatch_reasons.append("fine_mismatch")
        if isinstance(imposed_pts, int) and isinstance(expected.get("points"), int) and imposed_pts != expected.get("points"):
            mismatch = True
            mismatch_reasons.append("points_mismatch")

        return {
            "ok": True,
            "limit": int(limit),
            "measured": int(measured),
            "capture_mode": (capture_mode or "UNKNOWN"),
            "margin_value": float(margin),
            "corrected": round(float(corrected), 2),
            "expected": expected,
            "imposed": {"fine": imposed_fine, "points": imposed_pts},
            "mismatch": mismatch,
            "mismatch_reasons": mismatch_reasons,
        }
    except Exception as e:
        return {"ok": False, "reason": f"error:{e}"}


# --------------------------
# Párrafos auxiliares
# --------------------------
def build_velocity_calc_paragraph(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> str:
    """Párrafo ilustrativo de cálculo. Si faltan datos, devuelve ''"""
    vc = compute_velocity_calc_from_core(core, capture_mode=capture_mode)
    if not vc.get("ok"):
        return ""
    limit = vc.get("limit")
    measured = vc.get("measured")
    margin = vc.get("margin_value")
    corrected = vc.get("corrected")
    exceso = float(corrected) - float(limit)

    if exceso <= 0:
        return (
            "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
            f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
            f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, lo que la situaría por debajo del límite máximo permitido. "
            "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
        )

    return (
        "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
        f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
        f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, "
        f"lo que supondría un exceso efectivo aproximado de {exceso:.2f} km/h sobre el límite. "
        "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
    )


def should_inject_tramo_error(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> bool:
    vc = compute_velocity_calc_from_core(core, capture_mode=capture_mode)
    if not vc.get("ok"):
        return False
    # Solo si imposed_fine existe y es plausible (sanitize), y hay mismatch real
    return bool(vc.get("mismatch")) and isinstance((vc.get("imposed") or {}).get("fine"), int)


def build_tramo_error_paragraph(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> str:
    vc = compute_velocity_calc_from_core(core, capture_mode=capture_mode)
    if not (vc.get("ok") and vc.get("mismatch")):
        return ""
    exp = vc.get("expected") or {}
    imp = vc.get("imposed") or {}
    parts: List[str] = []
    parts.append("De forma adicional, se aprecia posible error de tramo sancionador.")
    if isinstance(imp.get("fine"), int) and isinstance(exp.get("fine"), int) and imp.get("fine") != exp.get("fine"):
        parts.append(
            f"Consta un importe impuesto de {imp.get('fine')}€, mientras que, atendida la velocidad corregida, el tramo orientativo podría corresponder a {exp.get('fine')}€."
        )
    if isinstance(imp.get("points"), int) and isinstance(exp.get("points"), int) and imp.get("points") != exp.get("points"):
        parts.append(
            f"Asímismo, constan {imp.get('points')} puntos, cuando el tramo orientativo podría implicar {exp.get('points')} puntos."
        )
    if exp.get("band"):
        parts.append(f"Banda orientativa considerada: {exp.get('band')}.")
    parts.append("En todo caso, corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda/tramo aplicado, con motivación técnica verificable.")
    return " ".join(parts)


def velocity_strict_missing(body: str) -> List[str]:
    """Validación mínima de contenido para VELOCIDAD (similar a SVL-1)."""
    b = (body or "").lower()
    missing: List[str] = []
    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")
    if "margen" not in b:
        missing.append("margen")
    if ("velocidad corregida" not in b) and ("corregida" not in b):
        missing.append("velocidad_corregida")
    if not any(k in b for k in ["certificado", "verificación", "verificacion"]):
        missing.append("metrologia")
    if not any(k in b for k in ["cinemómetro", "cinemometro", "radar"]):
        missing.append("cinemometro")
    if not any(k in b for k in ["captura", "fotograma", "imagen"]):
        missing.append("captura")
    return list(dict.fromkeys(missing))
