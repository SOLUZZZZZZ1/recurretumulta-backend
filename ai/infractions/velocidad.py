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
    # Anexo IV del texto refundido de la Ley de Tráfico. Rangos inclusivos.
    # Revisado para RTM Intelligence CORE v1.0 (2026-08-07).
    return {
        20:  [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],
        30:  [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],
        40:  [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],
        50:  [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (101,999,600,6,'600€ 6 puntos')],
        60:  [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],
        70:  [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],
        80:  [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],
        90:  [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],
        100: [(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],
        110: [(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],
        120: [(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
        130: [(131,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
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

# ============================================================================
# RTM Intelligence CORE — Especialista jurídico de VELOCIDAD v1.0
# ============================================================================
# Esta capa NO decide automáticamente que un margen concreto deba restarse.
# Su función es separar hechos documentales, detectar umbrales sancionadores,
# distinguir fechas por su contexto y proponer cuestiones jurídicas para OPS.
# Base jurídica revisada para v1.0: 2026-08-07.

import unicodedata
from datetime import datetime

VELOCITY_LEGAL_INTELLIGENCE_VERSION = "velocity_legal_v1_2"


def _v_safe(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _v_fold(text: str) -> str:
    txt = unicodedata.normalize("NFKD", _v_safe(text))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = txt.lower().replace("’", "'")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _v_blob(core: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "raw_text_blob", "vision_raw_text", "raw_text_vision", "raw_text_pdf",
        "hecho_denunciado_literal", "hecho_denunciado_resumido", "hecho_imputado",
        "observaciones",
    ):
        value = _v_safe((core or {}).get(key)).strip()
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def _v_segments(blob: str) -> List[str]:
    lines = [re.sub(r"\s+", " ", x).strip() for x in _v_safe(blob).splitlines() if x.strip()]
    out: List[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        if i + 1 < len(lines):
            out.append(f"{line} {lines[i + 1]}")
        if i + 2 < len(lines):
            out.append(f"{line} {lines[i + 1]} {lines[i + 2]}")
    # fallback para OCR que llega en una sola línea
    flat = re.sub(r"\s+", " ", _v_safe(blob)).strip()
    if flat:
        out.append(flat)
    return out


def _v_normalize_date(value: Any) -> Optional[str]:
    txt = _v_safe(value).strip()
    if not txt:
        return None
    txt = txt.replace(".", "-").replace("/", "-")
    m = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", txt)
    if not m:
        return None
    try:
        dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return dt.strftime("%d-%m-%Y")
    except Exception:
        return None


def _v_date_obj(value: Any) -> Optional[datetime]:
    norm = _v_normalize_date(value)
    if not norm:
        return None
    try:
        return datetime.strptime(norm, "%d-%m-%Y")
    except Exception:
        return None


def _v_first_date(segment: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b", _v_safe(segment))
    return _v_normalize_date(m.group(1)) if m else None


def _v_context_date(blob: str, include_keywords: List[str], exclude_keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """Extrae una fecha ligada al CONTEXTO de la etiqueta, no la primera fecha del párrafo.

    Prioriza una fecha situada después de la palabra-clave. Esto evita confundir, por
    ejemplo, la fecha del hecho con la fecha posterior de identificación del conductor
    cuando ambas aparecen en el mismo segmento OCR.
    """
    exclude_keywords = exclude_keywords or []
    for segment in _v_segments(blob):
        folded = _v_fold(segment)
        present = [(folded.find(k), k) for k in include_keywords if k in folded]
        if not present:
            continue
        if any(k in folded for k in exclude_keywords):
            continue

        key_pos, key = sorted(present, key=lambda x: x[0])[0]
        key_end = key_pos + len(key)
        dates = list(re.finditer(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b", folded))
        if not dates:
            continue

        # Primero, la fecha posterior más próxima a la etiqueta (máx. 260 caracteres).
        after = [m for m in dates if m.start() >= key_end and (m.start() - key_end) <= 260]
        chosen = min(after, key=lambda m: m.start() - key_end) if after else None

        # Si la redacción es "fecha DD/MM/AAAA de verificación", admitir la fecha anterior próxima.
        if chosen is None:
            near = [m for m in dates if abs(m.start() - key_pos) <= 140]
            if near:
                chosen = min(near, key=lambda m: abs(m.start() - key_pos))

        if chosen is not None:
            date_value = _v_normalize_date(chosen.group(1))
            if date_value:
                return {"date": date_value, "evidence": segment[:420]}
    return {"date": None, "evidence": ""}


def _v_vehicle_make(core: Dict[str, Any], blob: str) -> Dict[str, Any]:
    for key in ("marca_modelo", "marca", "vehicle_make_model", "marca_vehiculo", "vehicle_make"):
        value = _v_safe((core or {}).get(key)).strip()
        if value:
            return {"value": value, "evidence": f"core.{key}"}

    flat = re.sub(r"\s+", " ", _v_safe(blob)).strip()
    patterns = [
        r"MATR[IÍ]CULA\s+[A-Z0-9 ]{5,12}\s+MARCA\s+([A-ZÁÉÍÓÚÜÑ0-9-]{2,24})",
        r"\bMARCA\s*[:\-]?\s*([A-ZÁÉÍÓÚÜÑ0-9-]{2,24})\b",
        # Algunos OCR eliminan la etiqueta MARCA pero conservan la secuencia
        # matrícula + marca + calificación (p.ej. '1579MGV CITROEN GREU').
        # La matrícula puede estar mal leída; aquí solo usamos la palabra que
        # ocupa inequívocamente la posición de la marca.
        r"\b[0-9]{4}\s*[A-Z]{3}\s+([A-ZÁÉÍÓÚÜÑ0-9-]{2,24})\s+(?:GREU|GRAVE|LLEU|LEVE|MOLT\s+GREU|MUY\s+GRAVE)\b",
    ]
    bad = {"QUALIFICACIO", "QUALIFICACIÓ", "CALIFICACION", "CALIFICACIÓN", "MODEL", "MODELO"}
    for pat in patterns:
        m = re.search(pat, flat, flags=re.I)
        if not m:
            continue
        value = m.group(1).strip(" ,.-")
        if _v_fold(value).upper() in bad:
            continue
        return {"value": value.upper(), "evidence": m.group(0)[:220]}
    return {"value": None, "evidence": ""}


def _v_article(blob: str) -> Dict[str, Any]:
    flat = re.sub(r"\s+", " ", _v_safe(blob)).strip()
    patterns = [
        r"Reglament\s+General\s+de\s+Circulaci[oó]\s+([0-9]+(?:\.[0-9A-Za-z]+){0,3})",
        r"Reglamento\s+General\s+de\s+Circulaci[oó]n\s+([0-9]+(?:\.[0-9A-Za-z]+){0,3})",
    ]
    for pat in patterns:
        m = re.search(pat, flat, flags=re.I)
        if m:
            return {
                "norm": "Reglamento General de Circulación",
                "article": m.group(1).upper(),
                "evidence": m.group(0)[:220],
            }
    return {"norm": None, "article": None, "evidence": ""}


def _v_installation_mode(core: Dict[str, Any], blob: str) -> Dict[str, Any]:
    # IMPORTANTE: 'captada automáticamente' describe el modo de captura/notificación,
    # no convierte el cinemómetro en fijo/estático/móvil.
    for key in ("radar_installation_mode", "installation_mode", "modalidad_cinemometro"):
        value = _v_fold((core or {}).get(key))
        if value in {"fija", "fijo", "estatica", "estatico", "movil", "movil_en_movimiento", "tramo"}:
            return {"value": value, "known": True, "evidence": f"core.{key}"}

    for segment in _v_segments(blob):
        folded = _v_fold(segment)
        if "radar de tramo" in folded or "cinemometro de tramo" in folded or "control de velocidad por tramo" in folded:
            return {"value": "tramo", "known": True, "evidence": segment[:360]}
        if any(x in folded for x in ["vehiculo en movimiento", "radar movil en movimiento", "cinemometro movil en movimiento"]):
            return {"value": "movil_en_movimiento", "known": True, "evidence": segment[:360]}
        if any(x in folded for x in ["radar estatico", "cinemometro estatico", "vehiculo parado", "ubicacion estatica"]):
            return {"value": "estatica", "known": True, "evidence": segment[:360]}
        if any(x in folded for x in ["radar fijo", "cinemometro fijo", "instalacion fija", "cabina fija", "portico fijo"]):
            return {"value": "fija", "known": True, "evidence": segment[:360]}
    return {"value": "unknown", "known": False, "evidence": ""}


def _v_speed_semantics(blob: str) -> Dict[str, Any]:
    for segment in _v_segments(blob):
        folded = _v_fold(segment)
        if any(x in folded for x in ["velocitat mesurada", "velocidad medida"]):
            return {
                "label": "measured_label_present",
                "raw_or_corrected": "unknown",
                "evidence": segment[:420],
            }
        if any(x in folded for x in ["velocidad corregida", "velocitat corregida"]):
            return {
                "label": "corrected_label_present",
                "raw_or_corrected": "corrected",
                "evidence": segment[:420],
            }
    return {"label": "unknown", "raw_or_corrected": "unknown", "evidence": ""}


def _v_band_for(limit: Any, value: Any) -> Optional[Dict[str, Any]]:
    try:
        lim = int(limit)
        val = int(round(float(value)))
    except Exception:
        return None
    table = dgt_speed_sanction_table().get(lim) or []
    for idx, (lo, hi, fine, pts, label) in enumerate(table):
        if lo <= val <= hi:
            prev = table[idx - 1] if idx > 0 else None
            return {
                "limit": lim,
                "value": val,
                "lower": lo,
                "upper": hi,
                "fine": fine,
                "points": pts,
                "label": label,
                "is_lower_boundary": val == lo,
                "previous": (
                    {
                        "lower": prev[0], "upper": prev[1], "fine": prev[2],
                        "points": prev[3], "label": prev[4],
                    }
                    if prev else None
                ),
            }
    return None



def _v_secondary(core: Dict[str, Any]) -> Dict[str, Any]:
    sec = (core or {}).get("velocity_secondary_facts")
    return dict(sec) if isinstance(sec, dict) else {}


def _v_secondary_meta(core: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": _v_safe((core or {}).get("velocity_secondary_facts_version")).strip() or None,
        "confidence": (
            dict((core or {}).get("velocity_secondary_facts_confidence") or {})
            if isinstance((core or {}).get("velocity_secondary_facts_confidence"), dict)
            else {}
        ),
        "evidence": (
            dict((core or {}).get("velocity_secondary_facts_evidence") or {})
            if isinstance((core or {}).get("velocity_secondary_facts_evidence"), dict)
            else {}
        ),
    }


def build_velocity_legal_intelligence(core: Dict[str, Any]) -> Dict[str, Any]:
    """Construye inteligencia jurídica estructurada para expedientes de velocidad.

    Principios:
    - hechos y fechas solo se atribuyen a su contexto documental;
    - una fecha de identificación de conductor NO es una fecha de verificación;
    - 'imagen captada automáticamente' NO identifica la modalidad del radar;
    - no se aplica automáticamente un margen si la modalidad real no está acreditada;
    - se detectan umbrales sancionadores sin convertirlos en una conclusión automática.
    """
    core = dict(core or {})
    blob = _v_blob(core)
    folded_blob = _v_fold(blob)
    secondary = _v_secondary(core)
    secondary_meta = _v_secondary_meta(core)
    secondary_evidence = secondary_meta.get("evidence") or {}

    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")
    fine = sanitize_imposed_fine(core.get("sancion_importe_eur"))
    points = sanitize_imposed_points(core.get("puntos_detraccion"))
    fact_date = _v_normalize_date(core.get("fecha_infraccion") or core.get("fecha_hecho"))

    if _v_normalize_date(secondary.get("verification_date")):
        verification = {
            "date": _v_normalize_date(secondary.get("verification_date")),
            "evidence": _v_safe(secondary_evidence.get("verification_date")),
        }
    else:
        verification = _v_context_date(
            blob,
            include_keywords=[
                "verificacio periodica", "verificacion periodica", "darrera data de verificacio",
                "ultima fecha de verificacion", "certificat de verificacio", "certificado de verificacion",
            ],
            exclude_keywords=["dades del conductor", "datos del conductor", "facilitades pel titular"],
        )

    if _v_normalize_date(secondary.get("driver_data_date")):
        driver_data = {
            "date": _v_normalize_date(secondary.get("driver_data_date")),
            "evidence": _v_safe(secondary_evidence.get("driver_data_date")),
        }
    else:
        driver_data = _v_context_date(
            blob,
            include_keywords=["dades del conductor", "datos del conductor", "facilitades pel titular", "facilitados por el titular"],
        )

    if _v_normalize_date(secondary.get("initiation_document_date")):
        initiation_document_date = {
            "date": _v_normalize_date(secondary.get("initiation_document_date")),
            "evidence": _v_safe(secondary_evidence.get("initiation_document_date")),
        }
    else:
        initiation_document_date = _v_context_date(
            blob,
            include_keywords=[
                "acord d'incoacio de data", "acuerdo de incoacion de fecha",
                "acord d'incoacio dictat en data", "acuerdo de incoacion dictado en fecha",
            ],
            exclude_keywords=["dades del conductor", "datos del conductor"],
        )

    capture_secondary = secondary.get("capture_automatic")
    if isinstance(capture_secondary, bool):
        capture_automatic = capture_secondary
        capture_evidence = _v_safe(secondary_evidence.get("capture_automatic"))
    else:
        capture_automatic = any(x in folded_blob for x in [
            "imatge captada automaticament", "imagen captada automaticamente",
            "captacio automatica", "captacion automatica",
        ])
        capture_evidence = ""
        if capture_automatic:
            for segment in _v_segments(blob):
                if any(x in _v_fold(segment) for x in ["imatge captada automaticament", "imagen captada automaticamente"]):
                    capture_evidence = segment[:420]
                    break

    installation = _v_installation_mode(core, blob)
    semantics = _v_speed_semantics(blob)
    make = _v_vehicle_make(core, blob)

    secondary_article = secondary.get("normative_reference")
    if isinstance(secondary_article, dict) and (
        _v_safe(secondary_article.get("norm")).strip() or _v_safe(secondary_article.get("article")).strip()
    ):
        article = {
            "norm": _v_safe(secondary_article.get("norm")).strip() or None,
            "article": _v_safe(secondary_article.get("article")).strip().upper() or None,
            "evidence": _v_safe(secondary_evidence.get("normative_reference")),
        }
    else:
        article = _v_article(blob)

    document_subject = secondary.get("document_subject") if isinstance(secondary.get("document_subject"), dict) else {}
    vehicle_photo_present = secondary.get("vehicle_photo_present") if isinstance(secondary.get("vehicle_photo_present"), bool) else None
    certificate_reproduction_present = (
        secondary.get("certificate_reproduction_present")
        if isinstance(secondary.get("certificate_reproduction_present"), bool)
        else None
    )
    band = _v_band_for(limit, measured)

    verification_relation = "unknown"
    if verification.get("date") and fact_date:
        vd = _v_date_obj(verification.get("date"))
        fd = _v_date_obj(fact_date)
        if vd and fd:
            verification_relation = "after_fact" if vd > fd else "before_or_same_fact"

    imposed_matches_value_band = None
    if band and fine is not None and points is not None:
        imposed_matches_value_band = bool(fine == band.get("fine") and points == band.get("points"))

    issues: List[Dict[str, Any]] = []
    if band and band.get("is_lower_boundary") and band.get("previous"):
        issues.append({
            "code": "EXACT_SANCTION_BOUNDARY",
            "severity": "high",
            "message": (
                f"La cifra {band['value']} km/h coincide exactamente con el primer valor del tramo "
                f"de {band['fine']} € y {band['points']} puntos; el tramo anterior termina en "
                f"{band['previous']['upper']} km/h ({band['previous']['fine']} € y {band['previous']['points']} puntos)."
            ),
        })
    if not installation.get("known"):
        issues.append({
            "code": "INSTALLATION_MODE_NOT_PROVEN",
            "severity": "high",
            "message": "No se ha identificado de forma inequívoca si el cinemómetro operaba como fijo, estático o móvil en movimiento.",
        })
    if semantics.get("raw_or_corrected") == "unknown":
        issues.append({
            "code": "SPEED_VALUE_SEMANTICS_UNCLEAR",
            "severity": "high",
            "message": "La documentación no permite afirmar automáticamente si la cifra consignada es lectura previa a corrección o velocidad jurídicamente utilizada para sancionar.",
        })
    if verification.get("date"):
        if verification_relation == "after_fact":
            issues.append({
                "code": "VERIFICATION_DATE_AFTER_FACT",
                "severity": "high",
                "message": f"Se ha detectado una fecha de verificación ({verification['date']}) posterior a la fecha del hecho ({fact_date}). Debe comprobarse el certificado vigente en la fecha de la medición.",
            })
    else:
        issues.append({
            "code": "VERIFICATION_DATE_NOT_DETECTED",
            "severity": "medium",
            "message": "No se ha detectado en el texto analizado una fecha inequívocamente asociada a una verificación metrológica del cinemómetro.",
        })
    if capture_automatic:
        issues.append({
            "code": "AUTOMATIC_CAPTURE",
            "severity": "info",
            "message": "La notificación indica captación automática; conviene comprobar la imagen original, sus datos técnicos y la asignación inequívoca al vehículo.",
        })

    operator_review_reasons = [x["code"] for x in issues if x.get("severity") in {"high", "medium"}]

    return {
        "ok": True,
        "version": VELOCITY_LEGAL_INTELLIGENCE_VERSION,
        "facts": {
            "measured_kmh": measured,
            "limit_kmh": limit,
            "imposed_fine_eur": fine,
            "imposed_points": points,
            "radar_model": core.get("radar_modelo_hint") or core.get("radar_modelo"),
            "radar_antenna": core.get("radar_antena"),
            "fact_date": fact_date,
            "location": core.get("lugar_infraccion"),
            "vehicle_make_model": make.get("value"),
            "vehicle_make_evidence": make.get("evidence"),
            "capture_automatic": capture_automatic,
            "capture_automatic_evidence": capture_evidence,
            "installation_mode": installation,
            "speed_value_semantics": semantics,
            "verification": {
                "date": verification.get("date"),
                "evidence": verification.get("evidence"),
                "relation_to_fact": verification_relation,
                "detected": bool(verification.get("date")),
            },
            "driver_data_date": {
                "date": driver_data.get("date"),
                "evidence": driver_data.get("evidence"),
            },
            "initiation_document_date": {
                "date": initiation_document_date.get("date"),
                "evidence": initiation_document_date.get("evidence"),
            },
            "normative_reference": article,
            "document_subject": {
                "full_name": _v_safe(document_subject.get("full_name")).strip() or None,
                "id_number": _v_safe(document_subject.get("id_number")).strip().upper() or None,
                "evidence": _v_safe(secondary_evidence.get("document_subject")),
            },
            "vehicle_photo_present": vehicle_photo_present,
            "vehicle_photo_evidence": _v_safe(secondary_evidence.get("vehicle_photo_present")),
            "certificate_reproduction_present": certificate_reproduction_present,
            "certificate_reproduction_evidence": _v_safe(secondary_evidence.get("certificate_reproduction_present")),
        },
        "sanction_boundary": {
            "band": band,
            "imposed_matches_document_value_band": imposed_matches_value_band,
            "legal_conclusion_automatic": False,
        },
        "issues": issues,
        "requires_operator_review": bool(operator_review_reasons),
        "operator_review_reasons": operator_review_reasons,
        "provenance": {
            "source": "validated_extraction_raw_text+secondary_visual_facts",
            "verification_date_context_strict": True,
            "driver_date_separated_from_verification": True,
            "secondary_facts_version": secondary_meta.get("version"),
            "secondary_facts_confidence": secondary_meta.get("confidence") or {},
        },
    }
