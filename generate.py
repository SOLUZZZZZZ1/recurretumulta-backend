import json
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from scoring import classify

from database import get_engine

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import build_movil_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template
from ai.infractions.distracciones import build_auriculares_strong_template
from ai.infractions.atencion import build_atencion_strong_template
from ai.infractions.marcas_viales import build_marcas_viales_strong_template
from ai.infractions.seguro import build_seguro_strong_template
from ai.infractions.cinturon import build_cinturon_strong_template
from ai.infractions.itv import build_itv_strong_template
from ai.infractions.carril import build_carril_strong_template
from ai.infractions.generic import build_generic_body
from ai.infractions.municipal_semaforo import build_municipal_semaforo_template
from ai.infractions.casco import build_casco_strong_template
from ai.infractions.municipal_sentido_contrario import build_municipal_sentido_contrario_template
from ai.infractions.municipal_generic import build_municipal_generic_template
from ai.infractions.velocidad import (
    build_velocity_calc_paragraph,
    build_tramo_error_paragraph,
)

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from ai.infractions.dispatch import dispatch_deterministic_template

router = APIRouter(tags=["generate"])


_ADMIN_PREFIXES = [
    "organismo:",
    "expediente_ref:",
    "tipo_sancion:",
    "observaciones:",
    "vision_raw_text:",
    "raw_text_pdf:",
    "raw_text_vision:",
    "raw_text_blob:",
    "fecha_documento:",
    "fecha_notificacion:",
    "importe:",
    "jurisdiccion:",
    "tipo_infraccion:",
    "facts_phrases:",
    "preceptos_detectados:",
    "articulo_infringido_num:",
    "apartado_infringido_num:",
    "norma_hint:",
]


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _clean_hecho_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r", " ").replace("\n", " ")
    low = t.lower()

    for p in _ADMIN_PREFIXES:
        idx = low.find(p)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    stop_signals = [
        " datos vehiculo",
        " datos vehículo",
        " importe",
        " puntos",
        " fecha limite",
        " fecha límite",
        " boletin",
        " boletín",
        " agente denunciante",
        " telefono de informacion",
        " teléfono de información",
        " telefono de atencion",
        " teléfono de atención",
        " fax",
        " correo ordinario",
        " remitir el presente",
        " impreso relleno",
        " total principal",
        " precepto infringido",
    ]
    for s in stop_signals:
        idx = low.find(s)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    t = re.sub(r'^[\"“”]+|[\"“”]+$', "", t).strip()
    t = re.sub(r"^(movil|m[oó]vil)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(5a|5b|5c)\s+", "", t, flags=re.IGNORECASE)
    return t



def _cleanup_ocr_noise(text: str) -> str:
    txt = _safe_str(text)
    if not txt:
        return ""

    replacements = {
        "contral": "contra el",
        "del ": "del ",
        "vehicuio": "vehículo",
        "vehicu1o": "vehículo",
        "rumor": "",
        "situacion": "situación",
        "atencion": "atención",
        "conduccion": "conducción",
        "via": "vía",
        "demas": "demás",
        "asi ": "así ",
    }

    out = txt
    for bad, good in replacements.items():
        out = re.sub(rf"\b{re.escape(bad)}\b", good, out, flags=re.IGNORECASE)

    out = re.sub(r"\[ilegable\]|\[ilegible\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip(" .:-\t")
    return out.strip()


def _compress_long_hecho(text: str, max_len: int = 220) -> str:
    txt = _safe_str(text).strip()
    if len(txt) <= max_len:
        return txt
    cut = txt[:max_len]
    if "." in cut:
        cut = cut[:cut.rfind(".") + 1]
    else:
        cut = cut.rsplit(" ", 1)[0].strip() + "."
    return cut.strip()


def _premium_hecho_rewrite(text: str, tipo: str = "") -> str:
    raw = _cleanup_ocr_noise(text)
    low = raw.lower()

    if tipo in ("atencion", "atencion_bicicleta"):
        if any(x in low for x in ["bailando", "tocando las palmas", "golpeando", "tambor"]):
            return "Conducir de forma negligente realizando conductas incompatibles con la atención debida a la conducción"
        if any(x in low for x in ["bicicleta", "ciclista", "ciclistas", "circula de a tres", "ocupando parte del carril derecho"]):
            return "Circular en bicicleta sin mantener la atención permanente a la conducción, ocupando indebidamente parte del carril"

    if tipo == "velocidad":
        facts = {
            "measured": None,
            "limit": None,
        }
        # la resolución principal la hace _resolve_velocity_facts; aquí solo pulimos el literal
        m = re.search(r"(\d{2,3})\s*km/?h", low)
        if m:
            facts["measured"] = m.group(1)
        m2 = re.search(r"(?:limitad[ao]a?|limite|límite|velocidad maxima|velocidad máxima)[^\d]{0,30}(\d{2,3})", low)
        if m2:
            facts["limit"] = m2.group(1)
        if facts["measured"] and facts["limit"]:
            return f"Presunto exceso de velocidad con medición consignada de {facts['measured']} km/h en tramo limitado a {facts['limit']} km/h"

    if tipo == "semaforo":
        if any(x in low for x in ["fase roja", "luz roja", "semaforo en rojo", "semáforo en rojo", "linea de detencion", "línea de detención"]):
            return "No respetar la luz roja del semáforo"

    if tipo == "movil":
        if any(x in low for x in ["telefono movil", "teléfono móvil", "pantalla", "whatsapp", "manipulando"]):
            return "Utilizar manualmente el teléfono móvil durante la conducción"

    if tipo == "cinturon":
        return "No utilizar correctamente el cinturón de seguridad"

    if tipo == "auriculares":
        return "Utilizar auriculares o cascos conectados durante la conducción"

    if tipo == "casco":
        return "No utilizar el casco de protección en las condiciones exigidas"

    if tipo == "seguro":
        return "Circular con el vehículo careciendo de seguro obligatorio en vigor"

    if tipo == "itv":
        return "Circular con la inspección técnica del vehículo no vigente"

    cleaned = _compress_long_hecho(raw)
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _clean_hecho_para_recurso(text: str, tipo: str = "", core: Optional[Dict[str, Any]] = None) -> str:
    core = core or {}
    cleaned = _premium_hecho_rewrite(text, tipo=tipo)

    if tipo == "velocidad":
        facts = _resolve_velocity_facts(core)
        measured = facts.get("measured")
        limit = facts.get("limit")
        if measured and limit:
            return f"Presunto exceso de velocidad con medición consignada de {int(measured)} km/h en tramo limitado a {int(limit)} km/h"

    return _compress_long_hecho(cleaned, 220)

def _extract_speed_candidates(text: str) -> list[int]:
    txt = _safe_str(text)
    vals = []
    for m in re.finditer(r"(?<!\d)(\d{2,3})(?:\s*km/?h)?", txt, flags=re.IGNORECASE):
        try:
            n = int(m.group(1))
        except Exception:
            continue
        if 20 <= n <= 250:
            vals.append(n)
    return vals


def _looks_like_noisy_velocity_text(text: str) -> bool:
    txt = _safe_str(text)
    low = txt.lower()
    if not txt.strip():
        return False
    weird_markers = [
        "notif1",
        "cir[[",
        "[ilegible]",
        "meriega",
        "inter[leccion",
        "anenal",
        "1006/2009",
        "|=",
        "[[",
        "]]",
    ]
    if any(w in low for w in weird_markers):
        return True
    bad_chars = sum(1 for ch in txt if ch in "[]|{}")
    return bad_chars >= 3


def _velocity_margin_info(measured: Optional[float], radar_hint: str = "") -> Dict[str, Any]:
    if not isinstance(measured, (int, float)) or measured <= 0:
        return {"margin_value": None, "corrected_speed": None, "margin_label": ""}

    radar_low = _safe_str(radar_hint).lower()
    if measured > 100:
        margin_value = round(float(measured) * 0.05, 2)
        margin_label = "5 %"
    else:
        margin_value = 5.0
        margin_label = "5 km/h"

    corrected_speed = round(float(measured) - margin_value, 2)
    return {
        "margin_value": margin_value,
        "corrected_speed": corrected_speed,
        "margin_label": margin_label,
    }


def _resolve_radar_profile(core: Dict[str, Any]) -> Dict[str, Any]:
    raw_sources = [
        _safe_str(core.get("radar_modelo_hint")),
        _safe_str(core.get("radar_tipo")),
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("hecho_denunciado_resumido")),
        _safe_str(core.get("hecho_imputado")),
        _safe_str(core.get("raw_text_pdf")),
        _safe_str(core.get("raw_text_vision")),
        _safe_str(core.get("raw_text_blob")),
        _safe_str(core.get("vision_raw_text")),
    ]
    blob = "\n".join(s for s in raw_sources if s.strip()).lower()

    profile = {
        "kind": "cinemometro_no_especificado",
        "label": "cinemómetro (modelo no consignado en la copia)",
        "margin_percent_high": 5.0,
        "margin_kmh_low": 5.0,
        "attack_focus": "Debe aportarse identificación completa del equipo, certificado metrológico vigente y prueba técnica bastante."
    }

    if "pegasus" in blob or "helicoptero" in blob or "helicóptero" in blob:
        profile.update({
            "kind": "pegasus",
            "label": "sistema aéreo Pegasus (modelo pendiente de acreditación)",
            "margin_percent_high": 7.0,
            "margin_kmh_low": 7.0,
            "attack_focus": "Tratándose de medición aérea, debe acreditarse de forma especialmente rigurosa la identificación del sistema, la secuencia completa de captación y la trazabilidad técnica de la medición.",
        })
        return profile

    if "tramo" in blob:
        profile.update({
            "kind": "radar_tramo",
            "label": "sistema de control de velocidad por tramo (modelo pendiente de acreditación)",
            "margin_percent_high": 5.0,
            "margin_kmh_low": 5.0,
            "attack_focus": "En controles de tramo debe acreditarse con precisión el punto inicial y final de medición, la sincronización temporal del sistema y la integridad del cálculo efectuado.",
        })
        return profile

    if any(k in blob for k in ["velolaser", "lasertech", "lti 20/20", "lti20/20", "ultralyte"]):
        exact = "Velolaser" if "velolaser" in blob else "cinemómetro láser portátil"
        profile.update({
            "kind": "velolaser_laser",
            "label": f"{exact} (modelo pendiente de acreditación)",
            "margin_percent_high": 7.0,
            "margin_kmh_low": 7.0,
            "attack_focus": "En mediciones con láser portátil debe acreditarse con especial detalle la instalación, alineación, verificación y la concreta operativa de captación del vehículo denunciado.",
        })
        return profile

    if "multanova" in blob:
        label = "cinemómetro Multanova"
        if "antena" in blob:
            label += " antena"
        profile.update({
            "kind": "multanova",
            "label": f"{label} (modelo pendiente de acreditación)",
            "margin_percent_high": 5.0,
            "margin_kmh_low": 5.0,
            "attack_focus": "En controles con Multanova debe acreditarse la concreta homologación del equipo, su verificación vigente, el fotograma íntegro y la correspondencia inequívoca con el vehículo denunciado.",
        })
        return profile

    if any(k in blob for k in ["antena", "cabina", "radar fijo", "pórtico", "portico"]):
        subtype = "cinemómetro fijo de cabina" if ("cabina" in blob or "radar fijo" in blob) else "cinemómetro fijo tipo antena"
        profile.update({
            "kind": "radar_fijo",
            "label": f"{subtype} (modelo pendiente de acreditación)",
            "margin_percent_high": 5.0,
            "margin_kmh_low": 5.0,
            "attack_focus": "En controles fijos debe acreditarse la concreta homologación del equipo, su verificación vigente, el fotograma íntegro y la correspondencia inequívoca con el vehículo denunciado.",
        })
        return profile

    if any(k in blob for k in ["vehiculo patrulla", "vehículo patrulla", "movil", "móvil", "coche patrulla"]):
        profile.update({
            "kind": "radar_movil",
            "label": "cinemómetro móvil (modelo pendiente de acreditación)",
            "margin_percent_high": 7.0,
            "margin_kmh_low": 7.0,
            "attack_focus": "En controles móviles debe acreditarse la modalidad concreta de captación, la posición del vehículo policial, la verificación metrológica del equipo y la secuencia completa de medición.",
        })
        return profile

    return profile


def _velocity_margin_info_from_profile(measured: Optional[float], profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(measured, (int, float)) or measured <= 0:
        return {"margin_value": None, "corrected_speed": None, "margin_label": ""}

    pct = float(profile.get("margin_percent_high") or 5.0)
    low_kmh = float(profile.get("margin_kmh_low") or 5.0)

    if measured > 100:
        margin_value = round(float(measured) * (pct / 100.0), 2)
        margin_label = f"{int(pct) if pct.is_integer() else pct} %"
    else:
        margin_value = round(low_kmh, 2)
        margin_label = f"{int(low_kmh) if low_kmh.is_integer() else low_kmh} km/h"

    corrected_speed = round(float(measured) - margin_value, 2)
    return {
        "margin_value": margin_value,
        "corrected_speed": corrected_speed,
        "margin_label": margin_label,
    }


def _resolve_velocity_facts(core: Dict[str, Any]) -> Dict[str, Any]:
    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")

    focused_sources = [
        _safe_str(core.get("hecho_denunciado_resumido")),
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("hecho_imputado")),
        _safe_str(core.get("radar_modelo_hint")),
        _safe_str(core.get("radar_tipo")),
    ]
    joined = "\n".join(s for s in focused_sources if s.strip())

    if not joined.strip() or len(joined.strip()) < 12:
        fallback_sources = [
            _safe_str(core.get("raw_text_pdf")),
            _safe_str(core.get("raw_text_vision")),
            _safe_str(core.get("raw_text_blob")),
            _safe_str(core.get("vision_raw_text")),
        ]
        joined = "\n".join(s for s in fallback_sources if s.strip())

    candidates = _extract_speed_candidates(joined)

    if (not isinstance(measured, (int, float)) or measured <= 0) and candidates:
        measured = max(candidates)

    if (not isinstance(limit, (int, float)) or limit <= 0):
        plausible_limits = [v for v in candidates if v in {20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120}]
        if plausible_limits:
            limit = max(plausible_limits)

    conflict = False
    if candidates:
        uniq = sorted(set(v for v in candidates if 20 <= v <= 250))
        if len(uniq) >= 2 and max(uniq) - min(uniq) >= 20:
            conflict = True

    if isinstance(measured, (int, float)) and isinstance(limit, (int, float)) and measured <= limit:
        if candidates:
            above = [v for v in candidates if isinstance(limit, (int, float)) and v > limit]
            if above:
                measured = min(above)
            else:
                conflict = True

    return {
        "measured": measured if isinstance(measured, (int, float)) and measured > 0 else None,
        "limit": limit if isinstance(limit, (int, float)) and limit > 0 else None,
        "conflict": conflict,
        "raw_joined": joined,
    }


def _looks_like_internal_extract(text: str) -> bool:
    low = _safe_str(text).lower().strip()
    if not low:
        return True
    bad_tokens = [
        "pone_fin_via_administrativa",
        "plazo_recurso_sugerido",
        "tipo_infraccion_scores",
        "tipo_infraccion_confidence",
        "subtipo_infraccion",
        "evidence_gaps",
        "recurso_strategy",
        "raw_text_pdf",
        "raw_text_vision",
        "raw_text_blob",
        "vision_raw_text",
        "radar_modelo_hint",
        "radar_tipo",
        "metrologia_requerida",
    ]
    return any(tok in low for tok in bad_tokens)


def _get_locked_tipo(core: Dict[str, Any]) -> str:
    """Return the family resolved upstream by analyze, if present."""
    for key in ("familia_resuelta", "template_usado", "tipo_infraccion"):
        val = _safe_str(core.get(key)).lower().strip()
        if val and val not in ("otro", "unknown", "desconocido", "generic"):
            return val
    return ""


def _has_locked_family(core: Dict[str, Any]) -> bool:
    return bool(_get_locked_tipo(core))


def _resolved_tipo_from_core(core: Dict[str, Any], fallback: str = "generic") -> str:
    """Single source of truth: use upstream classification only."""
    tipo = _get_locked_tipo(core)
    if tipo:
        return tipo
    for key in ("tipo_infraccion", "familia_resuelta", "template_usado"):
        val = _safe_str(core.get(key)).lower().strip()
        if val:
            return val
    return fallback


def get_hecho_para_recurso(core: Dict[str, Any], forced_tipo: Optional[str] = None) -> str:
    raw = (
        core.get("hecho_denunciado_resumido")
        or core.get("hecho_denunciado_literal")
        or core.get("hecho_imputado")
        or ""
    )
    txt = _clean_hecho_text(_safe_str(raw))
    low = txt.lower().strip()
    if (
        low.startswith("tipo_sancion:")
        or low.startswith("organismo:")
        or low.startswith("expediente_ref:")
        or low.startswith("hecho_imputado:")
    ):
        return ""

    tipo = forced_tipo or _resolved_tipo_from_core(core)
    if tipo == "velocidad":
        facts = _resolve_velocity_facts(core)
        measured = facts.get("measured")
        limit = facts.get("limit")
        if _looks_like_noisy_velocity_text(txt) or facts.get("conflict"):
            if measured and limit:
                return f"Presunto exceso de velocidad con medición consignada de {int(measured)} km/h en tramo limitado a {int(limit)} km/h"
            return "Presunto exceso de velocidad"
        if measured and limit and "km/h" not in low:
            return f"Presunto exceso de velocidad con medición consignada de {int(measured)} km/h en tramo limitado a {int(limit)} km/h"
    return _clean_hecho_para_recurso(txt, tipo=tipo, core=core)


def extract_hecho_denunciado_literal(core: Dict[str, Any]) -> str:
    text_parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "vision_raw_text"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)

    text = "\n".join(text_parts)
    if not text:
        return ""

    pattern = re.search(
        r"(hecho denunciado|hecho que se notifica|hecho imputado|hecho infringido)\s*[:\-]?\s*",
        text,
        re.IGNORECASE,
    )
    tail = text[pattern.end():] if pattern else text
    lines = [l.strip() for l in tail.split("\n") if l.strip()]

    collected = []
    started = False

    for ln in lines:
        low = ln.lower()

        if any(
            x in low
            for x in [
                "datos vehiculo",
                "datos vehículo",
                "importe",
                "bonificacion",
                "reduccion",
                "fecha limite",
                "fecha límite",
                "puntos",
                "entidad",
                "matricula",
                "marca:",
                "modelo",
                "domicilio",
                "boletin",
                "boletín",
                "telefono de informacion",
                "teléfono de información",
                "telefono de atencion",
                "teléfono de atención",
                "fax",
                "correo ordinario",
                "remitir el presente",
                "impreso relleno",
                "motivo de no notificacion",
                "motivo de no notificación",
            ]
        ):
            if started:
                break
            continue

        if not started:
            if any(
                s in low
                for s in [
                    "circular a",
                    "circulaba a",
                    "conducir",
                    "cruce",
                    "fase roja",
                    "luz roja",
                    "semaforo",
                    "utilizando",
                    "auricular",
                    "auriculares",
                    "cascos",
                    "bail",
                    "palmas",
                    "volante",
                    "km/h",
                    "velocidad",
                    "linea continua",
                    "línea continua",
                    "itv",
                    "seguro",
                    "alumbrado",
                    "detención",
                ]
            ):
                started = True
                collected.append(ln)
        else:
            collected.append(ln)

        if len(" ".join(collected)) > 900:
            break

    return _clean_hecho_text(" ".join(collected))


def resolve_jurisdiction(core: Dict[str, Any]) -> str:
    j = _safe_str(core.get("jurisdiccion")).lower().strip()
    if j in ("municipal", "estatal", "desconocida"):
        return j

    blob = json.dumps(core, ensure_ascii=False).lower()
    if any(s in blob for s in ["ayuntamiento", "policia local", "policía local", "guardia urbana"]):
        return "municipal"
    if any(
        s in blob
        for s in [
            "direccion general de trafico",
            "dirección general de tráfico",
            "dgt",
            "guardia civil",
            "ministerio del interior",
        ]
    ):
        return "estatal"
    return "desconocida"


def _normalized_blob(core: Dict[str, Any]) -> str:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    return (
        blob.replace("semáforo", "semaforo")
            .replace("línea", "linea")
            .replace("detención", "detencion")
            .replace("policía", "policia")
            .replace("órdenes", "ordenes")
            .replace("señalización", "senalizacion")
    )


def _focused_infraction_blob(core: Dict[str, Any]) -> str:
    core = core or {}
    parts = [
        _safe_str(core.get("hecho_denunciado_resumido")),
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("hecho_imputado")),
        _safe_str(core.get("subtipo_infraccion")),
        _safe_str(core.get("tipo_infraccion")),
        _safe_str(core.get("norma_hint")),
    ]

    art = core.get("articulo_infringido_num")
    apt = core.get("apartado_infringido_num")
    if art not in (None, ""):
        parts.append(f"articulo {art}")
        parts.append(f"art. {art}")
    if art not in (None, "") and apt not in (None, ""):
        parts.append(f"articulo {art} apartado {apt}")

    blob = " ".join(p for p in parts if isinstance(p, str) and p.strip()).lower()
    return (
        blob.replace("semáforo", "semaforo")
            .replace("línea", "linea")
            .replace("detención", "detencion")
            .replace("policía", "policia")
            .replace("órdenes", "ordenes")
            .replace("señalización", "senalizacion")
    )


def _has_meaningful_focus(core: Dict[str, Any]) -> bool:
    blob = _focused_infraction_blob(core)
    return len(blob.strip()) >= 12


def _semaforo_positive_signals(blob: str) -> int:
    score = 0
    weighted = [
        ("cruce con fase roja del semaforo", 8),
        ("cruce con fase roja", 6),
        ("cruce fase roja", 6),
        ("semaforo en fase roja", 6),
        ("luz roja del semaforo", 6),
        ("no respetar luz roja", 8),
        ("no respetar la luz roja", 8),
        ("luz roja en interseccion", 7),
        ("luz roja en intersección", 7),
        ("semaforo en rojo", 5),
        ("cruce en rojo", 5),
        ("señal luminosa roja", 7),
        ("senal luminosa roja", 7),
        ("semaforo", 4),
        ("fase roja", 4),
        ("linea de detencion", 6),
        ("rebase la linea de detencion", 7),
        ("rebasar la linea de detencion", 7),
        ("rebase la linea de detencion con luz roja", 8),
        ("rebasar la linea de detencion sin respetar la luz roja", 8),
        ("no detenerse ante semaforo", 5),
        ("reanudar la marcha con semaforo", 5),
        ("articulo 146", 5),
        ("art. 146", 5),
    ]
    for token, pts in weighted:
        if token in blob:
            score += pts

    if ("roja" in blob and "cruce" in blob):
        score += 3

    if "200,00" in blob or "200.00" in blob or "200 €" in blob or "200 eur" in blob:
        score += 1
    if "4 puntos" in blob or "puntos: 4" in blob or "puntos a detraer 4" in blob:
        score += 1
    return score


def _semaforo_blockers(blob: str) -> int:
    score = 0

    agent_tokens = [
        "ordenes de los agentes",
        "ordenes del agente",
        "orden del agente",
        "no se para",
        "no detiene el vehiculo",
        "no detenerse",
        "agente",
        "agentes",
        "policia",
        "alto",
    ]
    for tok in agent_tokens:
        if tok in blob:
            score += 3

    bike_tokens = [
        "bicicleta",
        "ciclista",
        "ciclistas",
        "patinete",
        "vmp",
        "vehiculo de movilidad personal",
        "destellos",
        "intermitente",
        "alumbrado",
        "senalizacion optica",
        "luz roja intermitente",
        "catadioptrico",
        "reflectante",
    ]
    for tok in bike_tokens:
        if tok in blob:
            score += 3

    attention_tokens = [
        "temeraria",
        "conducir de forma temeraria",
        "atencion permanente",
        "conduccion negligente",
        "distraccion",
        "articulo 3",
        "art. 3",
        '"articulo": 3',
        '"articulo_infringido_num": "3"',
    ]
    for tok in attention_tokens:
        if tok in blob:
            score += 4

    return score


def _looks_like_agent_order_case(core: Dict[str, Any]) -> bool:
    blob = _normalized_blob(core)
    return any(tok in blob for tok in [
        "ordenes de los agentes",
        "ordenes del agente",
        "orden del agente",
        "no se para",
        "no detiene el vehiculo",
        "alto",
        "agente",
        "agentes",
        "policia",
    ])


def _looks_like_bike_light_case(core: Dict[str, Any]) -> bool:
    blob = _normalized_blob(core)
    return any(tok in blob for tok in [
        "bicicleta",
        "ciclista",
        "ciclistas",
        "patinete",
        "vmp",
        "vehiculo de movilidad personal",
    ]) and any(tok in blob for tok in [
        "luz roja",
        "intermitente",
        "destellos",
        "alumbrado",
        "senalizacion optica",
    ])


def _looks_like_semaforo(core: Dict[str, Any]) -> bool:
    blob = _normalized_blob(core)

    positive = _semaforo_positive_signals(blob)
    blockers = _semaforo_blockers(blob)

    if positive >= 6 and positive >= blockers + 3:
        return True
    if "cruce con fase roja del semaforo" in blob:
        return True
    return False



def _score_infraction_from_core(core: Dict[str, Any]) -> Dict[str, int]:
    """Scoring de diagnóstico usado por /debug/test-classifier."""
    blob = _focused_infraction_blob(core)
    if not blob.strip():
        blob = _normalized_blob(core)

    scores = {
        "velocidad": 0,
        "semaforo": 0,
        "movil": 0,
        "auriculares": 0,
        "cinturon": 0,
        "casco": 0,
        "atencion": 0,
        "marcas_viales": 0,
        "seguro": 0,
        "itv": 0,
        "condiciones_vehiculo": 0,
        "carril": 0,
        "alcohol": 0,
        "tacografo": 0,
        "estiba": 0,
        "neumaticos": 0,
        "peso": 0,
        "documentacion_transporte": 0,
        "limitador_velocidad": 0,
        "adr": 0,
    }

    def add(tipo: str, signals, points: int) -> None:
        for s in signals:
            if s in blob:
                scores[tipo] += points

    add("velocidad", ["km/h", "radar", "cinemometro", "cinemómetro", "exceso de velocidad"], 3)
    scores["semaforo"] += _semaforo_positive_signals(blob)
    scores["semaforo"] -= _semaforo_blockers(blob)
    add("movil", [
        "telefono movil", "teléfono móvil", "whatsapp",
        "movil al volante", "móvil al volante",
        "uso manual del telefono", "uso manual del teléfono",
        "manipular el telefono", "manipular el teléfono",
        "interactuar con la pantalla", "pantalla del telefono", "pantalla del teléfono",
        "sujetar telefono movil", "sujetar teléfono móvil",
        "consultando whatsapp", "manipulando el movil", "manipulando el móvil",
    ], 3)
    add("auriculares", [
        "auricular", "auriculares", "dispositivo de audio",
        "cascos o auriculares", "llevar puestos auriculares",
        "portar auriculares", "usar dispositivos de audio",
        "ambos oidos", "ambos oídos", "reproductor de sonido",
    ], 3)
    add("cinturon", ["cinturon de seguridad", "sin cinturon", "sin cinturón"], 3)
    add("casco", [
        "sin casco", "casco desabrochado", "casco mal abrochado",
        "no utilizar casco", "no utilizar casco reglamentario",
        "no hacer uso del casco", "no hacer uso del casco obligatorio",
        "casco reglamentario", "casco obligatorio", "casco de proteccion", "casco de protección",
        "ciclomotor sin casco", "motociclista sin casco",
    ], 3)
    add("atencion", [
        "atencion permanente", "atención permanente", "distraccion", "distracción",
        "conduccion negligente", "conducción negligente", "sin la diligencia necesaria",
        "mirando reiteradamente al acompanante", "mirando reiteradamente al acompañante",
        "sin mantener la atencion", "sin mantener la atención",
    ], 3)
    add("marcas_viales", [
        "linea continua", "línea continua", "marca vial", "marca longitudinal continua",
        "marcas viales", "zona de marcas viales", "franquear marca vial continua",
    ], 3)
    add("seguro", [
        "seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehículo no asegurado", "8/2004",
        "vehiculo sin asegurar", "vehículo sin asegurar", "sin asegurar",
        "carencia de seguro", "carece de seguro", "ausencia de seguro",
        "sin cobertura de seguro", "sin cobertura",
    ], 3)
    add("itv", ["itv", "inspeccion tecnica", "inspección técnica", "itv caducada"], 3)
    add("alcohol", ["alcohol", "alcoholemia", "etilometro", "etilómetro", "mg/l"], 5)
    add("condiciones_vehiculo", [
        "alumbrado", "senalizacion optica", "señalización óptica", "dispositivo luminoso", "destellos",
        "deficiencias tecnicas", "deficiencias técnicas", "luces no reglamentarias",
        "luces no reglamentarias instaladas", "luces no reglamentarias en el vehiculo",
        "superficie acristalada", "visibilidad diafana", "visibilidad diáfana",
        "laminas", "láminas", "adhesivos", "cortinillas", "parabrisas",
        "luz azul", "panel rectangular", "deslumbramiento",
    ], 3)
    add("carril", [
        "carril derecho", "carril izquierdo", "carril central", "posicion en la calzada", "posición en la calzada",
        "carril distinto del situado mas a la derecha", "carril distinto del situado más a la derecha",
        "no ocupar el carril mas a la derecha", "no ocupar el carril más a la derecha",
        "mas a la derecha posible", "más a la derecha posible",
    ], 4)

    # Camiones / transporte profesional
    add("tacografo", [
        "tacografo", "tacógrafo",
        "tiempos de conduccion", "tiempos de conducción",
        "tiempo de conduccion", "tiempo de conducción",
        "tiempos de descanso", "descanso obligatorio",
        "descanso diario", "descanso semanal",
        "horas de conduccion", "horas de conducción",
        "registro tacografo", "registro tacógrafo",
        "registros del tacografo", "registros del tacógrafo",
        "tarjeta del conductor", "tarjeta conductor",
        "manipulacion del tacografo", "manipulación del tacógrafo",
        "descarga de datos del tacografo", "descarga de datos del tacógrafo",
        "disco diagrama",
    ], 10)

    add("estiba", [
        "estiba", "sujecion de carga", "sujeción de carga",
        "sujecion de la carga", "sujeción de la carga",
        "trincaje", "amarre de la carga",
        "carga mal colocada", "carga desplazada",
        "desplazamiento de la carga", "estabilidad de la carga",
        "cinchas",
    ], 10)

    add("neumaticos", [
        "neumaticos", "neumáticos",
        "desgaste", "profundidad del dibujo",
        "cubierta", "cubiertas", "banda de rodadura",
        "eje directriz", "neumatico", "neumático",
    ], 10)

    add("peso", [
        "sobrepeso", "sobrecarga",
        "masa maxima", "masa máxima",
        "masa maxima autorizada", "masa máxima autorizada",
        "mma", "pesaje", "bascula", "báscula",
        "peso por eje",
    ], 10)

    add("documentacion_transporte", [
        "carta de porte", "documento de control",
        "licencia comunitaria", "permiso comunitario",
        "documentacion del transporte", "documentación del transporte",
        "autorizacion de transporte", "autorización de transporte",
    ], 10)

    add("limitador_velocidad", [
        "limitador de velocidad", "limitador",
    ], 10)

    add("adr", [
        "adr", "mercancias peligrosas", "mercancías peligrosas",
        "panel naranja", "cisterna",
    ], 10)

    if _looks_like_bike_light_case(core):
        scores["semaforo"] -= 6
        scores["condiciones_vehiculo"] += 4
    if _looks_like_agent_order_case(core):
        scores["semaforo"] -= 6
        scores["atencion"] += 4

    return scores


def resolve_infraction_type(core: Dict[str, Any]) -> str:
    """V5 bloqueada: generate.py no reclasifica; solo respeta analyze.py."""
    return _resolved_tipo_from_core(core, fallback="generic")


def fix_roman_headings(text: str) -> str:
    replacements = {
        r"\bi\.\s*antecedentes": "I. ANTECEDENTES",
        r"\bii\.\s*alegaciones": "II. ALEGACIONES",
        r"\biii\.\s*solicito": "III. SOLICITO",
    }
    out = text or ""
    for pattern, repl in replacements.items():
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _fix_alegaciones_numeracion(text: str) -> str:
    labels = ["PRIMERA", "SEGUNDA", "TERCERA", "CUARTA", "QUINTA", "SEXTA"]
    idx = 0

    def repl(match):
        nonlocal idx
        out = f"ALEGACIÓN {labels[idx]}" if idx < len(labels) else match.group(0)
        idx += 1
        return out

    return re.sub(r"ALEGACIÓN\s+[A-ZÁÉÍÓÚÑ]+", repl, text)


def _apply_premium_legal_formatting(text: str) -> str:
    txt = _safe_str(text)
    if not txt:
        return ""

    replacements = [
        ("presunción de inocencia", "**presunción de inocencia**"),
        ("insuficiencia probatoria", "**insuficiencia probatoria**"),
        ("falta de motivación", "**falta de motivación**"),
        ("motivación suficiente", "**motivación suficiente**"),
        ("nulidad de pleno derecho", "**nulidad de pleno derecho**"),
        ("archivo del expediente", "**ARCHIVO DEL EXPEDIENTE**"),
        ("expediente íntegro", "**expediente íntegro**"),
        ("prueba completa", "**prueba completa**"),
        ("carga probatoria", "**carga probatoria**"),
    ]

    for src, dst in replacements:
        txt = re.sub(rf"\b{re.escape(src)}\b", dst, txt, flags=re.IGNORECASE)

    txt = re.sub(r"\*\*\*+", "**", txt)
    return txt


def _resolve_strategy_mode(core: Dict[str, Any]) -> str:
    viability = _safe_str(core.get("case_viability")).lower().strip()
    level = _safe_str((core.get("estrategia_legal") or {}).get("nivel")).lower().strip()
    error_score = core.get("error_score") or 0

    try:
        error_score = int(error_score)
    except Exception:
        error_score = 0

    if viability == "alta" or level in ("agresivo", "muy_agresivo") or error_score >= 70:
        return "agresivo"
    if viability == "media" or level in ("reforzado", "tecnico", "técnico") or error_score >= 40:
        return "tecnico"
    return "prudente"


def _apply_strategy_mode_to_body(body: str, core: Dict[str, Any], tipo: str) -> str:
    """
    El motor estratégico sigue operando internamente, pero no muestra etiquetas
    ni títulos internos en el texto final del recurso.
    """
    txt = _safe_str(body)
    return txt

def _fix_alegacion_titles(text: str) -> str:
    txt = _safe_str(text)
    txt = re.sub(
        r"ALEGACIÓN\s+—\s*\*\*insuficiencia probatoria\*\*\s+Y\s+VULNERACIÓN\s+DE\s+GARANTÍAS",
        "ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS",
        txt,
        flags=re.IGNORECASE,
    )
    txt = re.sub(
        r"ALEGACIÓN\s+—\s*insuficiencia probatoria\s+Y\s+VULNERACIÓN\s+DE\s+GARANTÍAS",
        "ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS",
        txt,
        flags=re.IGNORECASE,
    )
    txt = re.sub(r"^ALEGACIÓN ADICIONAL\s+—", "ALEGACIÓN SEXTA —", txt, flags=re.MULTILINE)

    for label in ["PRIMERA", "SEGUNDA", "TERCERA", "CUARTA", "QUINTA", "SEXTA"]:
        txt = re.sub(
            rf"^(ALEGACIÓN\s+{label})(\s+)([A-ZÁÉÍÓÚÑ])",
            rf"\1 — \3",
            txt,
            flags=re.MULTILINE,
        )
    return txt

def _upgrade_bullets(text: str) -> str:
    txt = _safe_str(text)

    replacements = [
        (r"•\s*\*\*insuficiencia probatoria\*\*", "• La prueba aportada resulta insuficiente para desvirtuar la presunción de inocencia del interesado."),
        (r"•\s*insuficiencia probatoria", "• La prueba aportada resulta insuficiente para desvirtuar la presunción de inocencia del interesado."),
        (r"•\s*posicion agente no acreditada", "• No consta acreditada la posición exacta del agente denunciante ni las condiciones de observación."),
        (r"•\s*posición agente no acreditada", "• No consta acreditada la posición exacta del agente denunciante ni las condiciones de observación."),
        (r"•\s*visibilidad no acreditada", "• No constan descritas de forma suficiente las condiciones de visibilidad concurrentes en el momento de los hechos."),
        (r"•\s*distancia no acreditada", "• No se precisa la distancia exacta desde la que se habría realizado la observación."),
        (r"•\s*duracion observacion no acreditada", "• No se concreta la duración de la observación atribuida al agente denunciante."),
        (r"•\s*duracion de observacion no acreditada", "• No se concreta la duración de la observación atribuida al agente denunciante."),
        (r"•\s*duración observación no acreditada", "• No se concreta la duración de la observación atribuida al agente denunciante."),
    ]

    for patt, repl in replacements:
        txt = re.sub(patt, repl, txt, flags=re.IGNORECASE)

    return txt

def _replace_hecho_imputado_line_with_clean(body: str, hecho_limpio: str) -> str:
    txt = _safe_str(body)
    if not hecho_limpio:
        return txt
    return re.sub(
        r"(3\)\s+Hecho\s+imputado:\s*).+",
        lambda m: m.group(1) + hecho_limpio,
        txt,
        count=1,
        flags=re.IGNORECASE,
    )


def _detect_boletin_incoherente(core: Dict[str, Any]) -> bool:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()

    escandaloso = [
        "pene",
        "calzoncillo",
        "pantalon bajado",
        "pantalón bajado",
        "acto sexual",
        "desnudo",
        "cabeza entre las piernas",
    ]

    riesgo_vial = [
        "invasion de carril",
        "invasión de carril",
        "frenada brusca",
        "perdida de control",
        "pérdida de control",
        "colision",
        "colisión",
        "maniobra evasiva",
        "riesgo vial",
    ]

    return any(s in blob for s in escandaloso) and not any(s in blob for s in riesgo_vial)


def _inject_tipicidad_material_en_alegaciones(body: str, core: Dict[str, Any]) -> str:
    if not _detect_boletin_incoherente(core):
        return body

    bloque = (
        "ALEGACIÓN PRIMERA — AUSENCIA DE TIPICIDAD MATERIAL\n\n"
        "La descripción del boletín incorpora elementos llamativos o de contenido moral, "
        "pero no concreta una conducta de conducción que genere riesgo vial objetivable.\n\n"
        "El Derecho sancionador no sanciona conductas meramente escandalosas, sino "
        "infracciones tipificadas que afecten a la seguridad vial.\n\n"
    )

    marker = "II. ALEGACIONES\n\n"
    if marker in body and bloque.strip() not in body:
        return body.replace(marker, marker + bloque, 1)
    if bloque.strip() not in body:
        return bloque + body
    return body


def _assess_legal_strength(core: Dict[str, Any], tipo: str = "") -> Dict[str, Any]:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    flags = []
    score = 0

    hecho = get_hecho_para_recurso(core)
    hecho_low = _safe_str(hecho).lower().strip()

    if not hecho_low or len(hecho_low) < 25:
        flags.append("hecho_generico")
        score += 2

    if _detect_boletin_incoherente(core):
        flags.append("boletin_incoherente")
        score += 4

    if tipo in ("atencion", "atencion_bicicleta", "generic") and not any(
        s in blob for s in [
            "invasion de carril",
            "invasión de carril",
            "frenada brusca",
            "perdida de control",
            "pérdida de control",
            "colision",
            "colisión",
            "maniobra evasiva",
            "riesgo vial",
        ]
    ):
        flags.append("sin_riesgo_vial_concreto")
        score += 3

    if any(s in blob for s in ["no consta acreditado", "no consta", "insuficiente motivacion", "insuficiente motivación"]):
        flags.append("motivacion_debil")
        score += 2

    if tipo == "velocidad":
        if not any(s in blob for s in ["cinemometro", "cinemómetro", "radar_modelo_hint", "multanova", "velocidad_medida_kmh"]):
            flags.append("sin_soporte_tecnico")
            score += 3
    elif tipo in ("movil", "auriculares", "cinturon", "casco", "atencion", "atencion_bicicleta"):
        if not any(s in blob for s in ["fotografia", "fotografía", "video", "vídeo", "distancia", "angulo visual", "ángulo visual", "duracion", "duración"]):
            flags.append("sin_prueba_objetiva")
            score += 2
    elif tipo == "semaforo":
        if not any(s in blob for s in ["fotografia", "fotografía", "video", "vídeo", "fase roja", "linea de detencion", "línea de detención"]):
            flags.append("sin_prueba_objetiva")
            score += 2

    if any(s in blob for s in [
        "tipicidad",
        "subsuncion",
        "subsunción",
        "redaccion ambigua",
        "redacción ambigua",
        "no concreta",
        "falta de precision",
        "falta de precisión",
    ]):
        flags.append("tipicidad_debil")
        score += 2

    if score >= 8:
        level = "muy_agresivo"
    elif score >= 6:
        level = "agresivo"
    elif score >= 3:
        level = "reforzado"
    else:
        level = "normal"

    return {
        "score": score,
        "level": level,
        "flags": flags,
    }


def _build_strategic_reinforcement_block(core: Dict[str, Any], tipo: str, assessment: Dict[str, Any]) -> str:
    flags = set(assessment.get("flags") or [])
    level = assessment.get("level", "normal")
    parts = []

    if "sin_prueba_objetiva" in flags or "sin_soporte_tecnico" in flags or "motivacion_debil" in flags:
        parts.append(
            "ALEGACIÓN DE REFUERZO — PRESUNCIÓN DE INOCENCIA Y CARGA PROBATORIA\n\n"
            "La presunción de inocencia solo puede quedar desvirtuada mediante prueba suficiente, "
            "válida y específicamente referida al hecho imputado. La mera redacción del boletín, "
            "si no viene acompañada de concreción bastante, soporte objetivo o motivación "
            "individualizada, no basta por sí sola para fundamentar válidamente una sanción "
            "administrativa.\n"
        )

    if "tipicidad_debil" in flags or "hecho_generico" in flags:
        parts.append(
            "ALEGACIÓN DE REFUERZO — FALTA DE TIPICIDAD MATERIAL Y JURÍDICA\n\n"
            "La Administración debe describir con precisión la conducta verdaderamente atribuida y "
            "justificar su exacta subsunción en el tipo sancionador aplicado. Cuando el boletín utiliza "
            "fórmulas genéricas, ambiguas o estandarizadas sin concretar de forma suficiente el hecho "
            "sancionable, se debilita gravemente la validez del expediente.\n"
        )

    if "sin_riesgo_vial_concreto" in flags and level in ("agresivo", "muy_agresivo"):
        parts.append(
            "ALEGACIÓN DE REFUERZO — AUSENCIA DE RIESGO VIAL OBJETIVABLE\n\n"
            "No toda conducta llamativa, impropia o socialmente reprobable constituye por sí misma "
            "una infracción sancionable en materia de tráfico. Resulta imprescindible la identificación "
            "de una maniobra peligrosa, una afectación real al control del vehículo o un riesgo vial "
            "concreto, individualizado y objetivable. Su ausencia impide sostener con rigor el tipo "
            "infractor aplicado.\n"
        )

    if "boletin_incoherente" in flags and level in ("agresivo", "muy_agresivo"):
        parts.append(
            "ALEGACIÓN DE REFUERZO — DESVIACIÓN DEL OBJETO DEL DERECHO SANCIONADOR\n\n"
            "Cuando el boletín enfatiza aspectos escandalosos, morales o contextuales, pero no concreta "
            "debidamente la conducta vial típica ni su peligrosidad material, se produce una desviación "
            "respecto del verdadero objeto de la potestad sancionadora en materia de tráfico. La sanción "
            "no puede descansar sobre impresiones llamativas, sino sobre hechos típicos, acreditados y "
            "jurídicamente bien motivados.\n"
        )

    return "\n\n".join(p.strip() for p in parts if p.strip())


def _inject_strategic_legal_reinforcement(body: str, core: Dict[str, Any], tipo: str) -> str:
    txt = _safe_str(body)
    assessment = _assess_legal_strength(core, tipo)
    strategy_prefix = _build_strategy_prefix(core, tipo)
    block = "\n\n".join([x for x in [strategy_prefix, _build_strategic_reinforcement_block(core, tipo, assessment)] if _safe_str(x).strip()])

    if not block.strip():
        return txt

    marker = "II. ALEGACIONES\n\n"
    if marker in txt:
        return txt.replace(marker, marker + block + "\n\n", 1)

    marker_alt = "I. ALEGACIONES\n\n"
    if marker_alt in txt:
        return txt.replace(marker_alt, marker_alt + block + "\n\n", 1)

    return txt


def _get_estrategia_legal(core: Dict[str, Any]) -> Dict[str, Any]:
    data = core.get("estrategia_legal")
    return data if isinstance(data, dict) else {}


def _build_strategy_prefix(core: Dict[str, Any], tipo: str) -> str:
    estrategia = _get_estrategia_legal(core)
    nivel = _safe_str(estrategia.get("nivel")).lower().strip()
    principales = estrategia.get("bloques_principales") or []
    secundarios = estrategia.get("bloques_secundarios") or []
    usar_nulidad = bool(estrategia.get("usar_nulidad"))

    pieces = []

    if usar_nulidad:
        pieces.append(
            "ALEGACIÓN — NULIDAD DE PLENO DERECHO\n\n"
            "Con carácter principal, esta parte interesa la nulidad de pleno derecho del acto impugnado cuando el expediente prescinde de elementos esenciales de prueba o de tramitación que impiden identificar con garantías el hecho realmente sancionado y su adecuado soporte probatorio.\n"
        )

    if principales:
        mapping = {
            "insuficiencia_probatoria": "La Administración no aporta un soporte probatorio bastante y objetivable del hecho imputado.",
            "fase_roja_no_acreditada": "No consta acreditada de forma objetiva la fase roja activa en el instante exacto del supuesto rebase.",
            "secuencia_incompleta": "No se aporta secuencia íntegra o soporte completo que permita reconstruir la dinámica del hecho.",
            "falta_motivacion": "La motivación del expediente aparece formulada en términos genéricos o estereotipados.",
            "metrologia_no_acreditada": "No consta acreditación metrológica bastante del dispositivo de medición utilizado.",
            "fotograma_no_aportado": "No se acompaña fotograma íntegro y legible con individualización inequívoca del vehículo.",
            "margen_no_aplicado": "No se justifica de forma transparente el margen de corrección aplicado o aplicable.",
            "observacion_subjetiva": "La imputación descansa esencialmente en una observación subjetiva insuficientemente circunstanciada.",
            "falta_concrecion": "El boletín no concreta con precisión suficiente la conducta material imputada.",
            "ausencia_riesgo_vial": "No se describe un riesgo vial objetivable que permita subsumir la conducta en el tipo aplicado.",
            "tipicidad_debil": "La descripción fáctica no permite una subsunción típica clara e inequívoca.",
            "falta_precision_tecnica": "No se identifica con precisión el defecto técnico o reglamentario imputado.",
            "norma_no_identificada": "No se concreta el apartado reglamentario o exigencia técnica supuestamente incumplida.",
            "prueba_insuficiente": "No se aporta un soporte técnico bastante para sustentar la imputación.",
        }
        bullets = [f"• {mapping[key]}" for key in principales if key in mapping]
        if bullets:
            pieces.append("ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS\n\n" + "\n".join(bullets) + "\n")

    if nivel in ("agresivo", "muy_agresivo") and secundarios:
        bullets2 = "\n".join(f"• {str(x).replace('_', ' ')}" for x in secundarios)
        pieces.append("ALEGACIÓN — CONSIDERACIONES COMPLEMENTARIAS\n\n" + bullets2 + "\n")

    return "\n\n".join(p.strip() for p in pieces if p.strip())


def _build_fundamentos_derecho(tipo: str = "", core: Dict[str, Any] = None) -> str:
    tipo = (tipo or "").lower().strip()

    fundamentos = []

    fundamentos.append(
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los artículos 24 y 25 de la Constitución Española, "
        "que consagran el derecho a la presunción de inocencia, la legalidad sancionadora y el principio de tipicidad."
    )

    fundamentos.append(
        "SEGUNDO.– Conforme a los artículos 53, 63 y concordantes de la Ley 39/2015, de Procedimiento Administrativo Común, "
        "la potestad sancionadora exige la existencia de un procedimiento válido, motivación suficiente y respeto a las garantías del administrado."
    )

    fundamentos.append(
        "TERCERO.– De acuerdo con el artículo 77 del Texto Refundido de la Ley sobre Tráfico, Circulación de Vehículos a Motor y Seguridad Vial, "
        "corresponde a la Administración la carga de probar de forma suficiente los hechos constitutivos de la infracción."
    )

    if tipo == "velocidad":
        fundamentos.append(
            "CUARTO.– En materia de control de velocidad, resulta de aplicación la Orden ICT/155/2020, "
            "que regula el control metrológico del Estado de los instrumentos de medida, exigiendo verificación periódica y correcta utilización del dispositivo."
        )
        fundamentos.append(
            "QUINTO.– La jurisprudencia del Tribunal Supremo exige la acreditación técnica suficiente del cinemómetro, "
            "incluyendo certificado de verificación, identificación del equipo y soporte probatorio completo."
        )

    elif tipo in ("semaforo", "municipal_semaforo"):
        fundamentos.append(
            "CUARTO.– Conforme al artículo 146 del Reglamento General de Circulación, las señales luminosas regulan la prioridad de paso, "
            "exigiendo la detención ante luz roja no intermitente."
        )
        fundamentos.append(
            "QUINTO.– La jurisprudencia exige la acreditación de la fase roja activa en el momento exacto del hecho, "
            "así como el rebase efectivo de la línea de detención, no siendo suficiente una mera referencia genérica a la luz roja."
        )

    elif tipo == "movil":
        fundamentos.append(
            "CUARTO.– Conforme al artículo 18.2 del Reglamento General de Circulación, está prohibido utilizar manualmente dispositivos de telefonía móvil durante la conducción."
        )
        fundamentos.append(
            "QUINTO.– La jurisprudencia exige que la infracción se base en una observación concreta de manipulación efectiva del dispositivo, "
            "no bastando una simple apreciación genérica."
        )

    elif tipo in ("atencion", "atencion_bicicleta"):
        fundamentos.append(
            "CUARTO.– El artículo 3.1 del Reglamento General de Circulación establece la obligación de conducir con la diligencia necesaria para evitar riesgos propios o ajenos."
        )
        fundamentos.append(
            "QUINTO.– La jurisprudencia ha reiterado que no toda conducta irregular constituye infracción sancionable, "
            "si no se acredita una afectación real a la seguridad vial o al control del vehículo."
        )

    elif tipo == "auriculares":
        fundamentos.append(
            "CUARTO.– Conforme al artículo 18 del Reglamento General de Circulación, la conducción debe realizarse con la libertad de movimientos necesaria y sin dispositivos que disminuyan la atención permanente."
        )
        fundamentos.append(
            "QUINTO.– La Administración debe acreditar con precisión el uso efectivo del dispositivo y su incidencia real en la conducción."
        )

    elif tipo == "cinturon":
        fundamentos.append(
            "CUARTO.– Resultan de aplicación los preceptos de la Ley de Seguridad Vial y del Reglamento General de Circulación relativos al uso obligatorio del cinturón de seguridad."
        )
        fundamentos.append(
            "QUINTO.– La Administración debe describir con precisión el concreto incumplimiento imputado, no siendo suficiente una fórmula estereotipada o ambigua."
        )

    elif tipo == "casco":
        fundamentos.append(
            "CUARTO.– Resultan de aplicación los preceptos de la Ley de Seguridad Vial y del Reglamento General de Circulación relativos al uso obligatorio del casco de protección."
        )
        fundamentos.append(
            "QUINTO.– La Administración debe concretar si se imputa ausencia de casco, uso incorrecto, falta de homologación o deficiente sujeción."
        )

    elif tipo == "condiciones_vehiculo":
        fundamentos.append(
            "CUARTO.– Conforme al Reglamento General de Vehículos y normativa técnica aplicable, la Administración debe identificar con precisión el defecto técnico imputado y el precepto reglamentario vulnerado."
        )
        fundamentos.append(
            "QUINTO.– No basta una descripción genérica del estado del vehículo si no se concreta el defecto, su relevancia jurídica y el modo objetivo de constatación."
        )

    elif tipo == "transporte_profesional":
        fundamentos.append(
            "CUARTO.– En materia de transporte profesional y vehículos pesados, la Administración debe identificar "
            "con precisión la norma sectorial concreta supuestamente vulnerada, así como la concreta conducta técnica "
            "atribuida y el soporte objetivo que la acredita."
        )
        fundamentos.append(
            "QUINTO.– Cuando la imputación se refiere a tacógrafo, tiempos de conducción y descanso, estiba, neumáticos, "
            "peso o documentación de transporte, resulta imprescindible la aportación del acta de inspección completa, "
            "registro, descarga, medición, ticket o documento técnico correspondiente, sin que baste una formulación "
            "genérica o estandarizada."
        )

    elif tipo == "itv":
        fundamentos.append(
            "CUARTO.– Conforme al Real Decreto 920/2017, por el que se regula la inspección técnica de vehículos, la Administración debe acreditar documentalmente la situación administrativa del vehículo en la fecha del hecho."
        )

    elif tipo == "seguro":
        fundamentos.append(
            "CUARTO.– Conforme al Real Decreto Legislativo 8/2004, sobre responsabilidad civil y seguro en la circulación de vehículos a motor, la inexistencia de seguro debe acreditarse de forma suficiente y verificable."
        )

    elif tipo == "marcas_viales":
        fundamentos.append(
            "CUARTO.– En las infracciones relativas a marcas viales, la Administración debe identificar con precisión la marca afectada, la maniobra realizada y la norma infringida."
        )

    elif tipo == "carril":
        fundamentos.append(
            "CUARTO.– En las infracciones relativas a la posición o uso del carril, la Administración debe describir con precisión la configuración de la calzada, el carril utilizado y la regla concreta supuestamente vulnerada."
        )

    elif tipo == "alcohol":
        fundamentos.append(
            "CUARTO.– En materia de alcoholemia, la Administración debe acreditar la regularidad del procedimiento de medición, el aparato utilizado, el resultado obtenido y la observancia de las garantías mínimas exigibles para la validez de la prueba."
        )

    else:
        fundamentos.append(
            "CUARTO.– La Administración debe describir con precisión suficiente la conducta imputada y el precepto aplicado, permitiendo una subsunción jurídica clara y una defensa efectiva."
        )

    fundamentos.append(
        "SEXTO.– Conforme a reiterada jurisprudencia del Tribunal Supremo, la potestad sancionadora exige una motivación suficiente "
        "y una acreditación probatoria bastante para enervar la presunción de inocencia del administrado."
    )

    fundamentos.append(
        "SÉPTIMO.– La ausencia de prueba suficiente, la insuficiente motivación del expediente o la falta de concreción del hecho "
        "determinan la improcedencia de la sanción propuesta."
    )

    return "\n\n".join(fundamentos)


def _build_unified_suplico(tipo: str = "") -> str:
    punto_4 = (
        "4) Subsidiariamente, que se imponga en su caso la sanción mínima legalmente\n"
        "procedente dentro del tipo infractor que finalmente pudiera considerarse\n"
        "aplicable.\n\n"
    )

    return (
        "S U P L I C A:\n\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n\n"
        "2) Que, en atención a las alegaciones presentadas y sus fundamentos, se acuerde "
        "el ARCHIVO del expediente por insuficiencia probatoria, falta de acreditación "
        "suficiente del hecho imputado o ausencia de motivación individualizada.\n\n"
        "3) Subsidiariamente, para el caso de no estimarse el archivo, que se proceda "
        "a una correcta recalificación jurídica de los hechos conforme a la prueba "
        "realmente acreditada en el expediente.\n\n"
        f"{punto_4}"
        "5) Subsidiariamente, que se aporte expediente íntegro y prueba completa "
        "para contradicción efectiva.\n\n"
        "OTROSÍ DIGO\n\n"
        "Que esta parte se reserva expresamente el ejercicio de cuantos recursos "
        "administrativos y acciones legales pudieran corresponder en defensa de sus "
        "derechos e intereses legítimos.\n"
    )


def _strip_initial_antecedentes_block(body: str) -> str:
    txt = _safe_str(body).strip()
    txt = re.sub(
        r"^\s*A la atención del órgano competente,?\s*",
        "",
        txt,
        flags=re.IGNORECASE,
    )
    txt = re.sub(
        r"^\s*I\.\s*ANTECEDENTES\s*\n+",
        "",
        txt,
        flags=re.IGNORECASE,
    )
    return txt.strip()


def _build_comparecencia_text(core: Dict[str, Any], asunto_out: str) -> str:
    tipo_accion = _safe_str(core.get("tipo_accion")).lower().strip()
    fecha_res = core.get("fecha_resolucion") or "........"
    tenor = core.get("tenor_resolucion") or "................................"

    if "alzada" in tipo_accion:
        return (
            "Que mediante el presente escrito, documentación adjunta y sus copias, "
            f"vengo a formular RECURSO DE ALZADA contra la resolución de fecha {fecha_res}, "
            f"dictada por ese organismo, por la que se acuerda {tenor}, y todo ello según los siguientes\n\n"
            "A N T E C E D E N T E S\n\n"
        )

    if "reposicion" in tipo_accion or "reposición" in tipo_accion:
        return (
            "Que mediante el presente escrito, documentación adjunta y sus copias, "
            f"vengo a formular RECURSO POTESTATIVO DE REPOSICIÓN contra la resolución de fecha {fecha_res}, "
            f"dictada por ese organismo, por la que se acuerda {tenor}, y todo ello según los siguientes\n\n"
            "A N T E C E D E N T E S\n\n"
        )

    return (
        "Que mediante el presente escrito, documentación adjunta y sus copias, "
        f"vengo a formular {asunto_out} en el expediente más arriba referenciado, "
        "y todo ello según los siguientes\n\n"
        "A N T E C E D E N T E S\n\n"
    )


def _resolve_header_destination(core: Dict[str, Any]) -> Dict[str, str]:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    organismo_raw = _safe_str(core.get("organismo")).strip()

    organismo_fmt = "............................................"
    provincia_fmt = "............................................"

    provincia_aliases = {
        "barcelona": "BARCELONA",
        "girona": "GIRONA",
        "gerona": "GIRONA",
        "madrid": "MADRID",
        "oviedo": "OVIEDO",
        "asturias": "ASTURIAS",
        "valencia": "VALENCIA",
        "sevilla": "SEVILLA",
        "zaragoza": "ZARAGOZA",
        "malaga": "MÁLAGA",
        "málaga": "MÁLAGA",
        "alicante": "ALICANTE",
        "murcia": "MURCIA",
        "bilbao": "BILBAO",
        "vizcaya": "VIZCAYA",
        "bizkaia": "BIZKAIA",
        "granada": "GRANADA",
        "cordoba": "CÓRDOBA",
        "córdoba": "CÓRDOBA",
        "valladolid": "VALLADOLID",
        "coruña": "A CORUÑA",
        "a coruña": "A CORUÑA",
        "pontevedra": "PONTEVEDRA",
        "tarragona": "TARRAGONA",
        "lleida": "LLEIDA",
        "lerida": "LLEIDA",
        "castellon": "CASTELLÓN",
        "castellón": "CASTELLÓN",
        "badajoz": "BADAJOZ",
        "cadiz": "CÁDIZ",
        "cádiz": "CÁDIZ",
        "huelva": "HUELVA",
        "jaen": "JAÉN",
        "jaén": "JAÉN",
        "leon": "LEÓN",
        "león": "LEÓN",
        "salamanca": "SALAMANCA",
        "toledo": "TOLEDO",
        "burgos": "BURGOS",
        "palma": "PALMA",
        "mallorca": "MALLORCA",
    }

    for k, v in provincia_aliases.items():
        if k in blob:
            provincia_fmt = v
            break

    if any(s in blob for s in ["jefatura provincial de trafico", "jefatura provincial de tráfico", "dgt", "guardia civil", "ministerio del interior"]):
        organismo_fmt = "JEFATURA PROVINCIAL DE TRÁFICO"
    elif "guardia urbana" in blob:
        organismo_fmt = "GUARDIA URBANA"
    elif any(s in blob for s in ["policia local", "policía local"]):
        organismo_fmt = "POLICÍA LOCAL"
    elif "ajuntament" in blob:
        organismo_fmt = "AJUNTAMENT"
    elif "ayuntamiento" in blob:
        organismo_fmt = "AYUNTAMIENTO"
    elif organismo_raw:
        organismo_fmt = organismo_raw.upper()

    return {
        "organismo_cabecera": organismo_fmt,
        "provincia_cabecera": provincia_fmt,
    }


def _integrate_extract_after_comparecencia(body: str, hecho: str, core: Dict[str, Any] = None, forced_tipo: Optional[str] = None) -> str:
    txt = _safe_str(body)
    hecho = _safe_str(hecho).strip()
    core = core or {}
    if not hecho:
        return txt

    tipo = forced_tipo or _resolved_tipo_from_core(core)
    if tipo == "velocidad" and (_looks_like_noisy_velocity_text(hecho) or _resolve_velocity_facts(core).get("conflict")):
        facts = _resolve_velocity_facts(core)
        measured = facts.get("measured")
        limit = facts.get("limit")
        if measured and limit:
            hecho = f"Presunto exceso de velocidad con medición consignada de {int(measured)} km/h en tramo limitado a {int(limit)} km/h."
        else:
            hecho = "Presunto exceso de velocidad según denuncia automatizada."

    bloque = f'Extracto literal del boletín:\n“{hecho}”\n\n'

    if bloque.strip() in txt:
        return txt

    marker = "A N T E C E D E N T E S\n\n"
    if marker in txt:
        return txt.replace(marker, marker + bloque, 1)

    return bloque + txt


def _center_text_line(text: str, width: int = 90) -> str:
    s = _safe_str(text).strip()
    if not s:
        return ""
    return s.center(width).rstrip()


def _upgrade_generated_template(asunto: str, cuerpo: str, tipo: str = "", core: Dict[str, Any] = None, inferred_type: str = "", scores: Dict[str, int] | None = None, jurisdiction: str = "") -> Dict[str, str]:
    core = core or {}
    asunto_out = "ESCRITO DE ALEGACIONES"

    exp_ref = core.get("expediente_ref") or core.get("numero_expediente") or "........ / ........"
    destino = _resolve_header_destination(core)
    organismo = destino["organismo_cabecera"]
    provincia = destino["provincia_cabecera"]

    comparecencia = _build_comparecencia_text(core, asunto_out)

    linea_titulo = _center_text_line("ESCRITO DE ALEGACIONES", 90)
    linea_destino = _center_text_line(f"A LA {str(organismo).upper()} DE {str(provincia).upper()}", 90)

    cabecera = (
        f"REFERENCIA: EXPTE. {exp_ref}\n\n"
        f"{linea_titulo}\n\n\n"
        f"{linea_destino}\n\n\n\n"
        "D./D.ª ........................................, mayor de edad, con DNI/NIE/TR "
        "........................, y con domicilio en ........................................, "
        "a efectos de notificaciones, actuando en su propio nombre e interés "
        "[o actuando por cuenta de D./D.ª ................................, según autorización "
        "o poder que se adjunta como documento núm. 1], ante esta Dependencia comparece y, "
        "como mejor proceda en Derecho,\n\n"
        "D I G O:\n\n\n"
        f"{comparecencia}"
    )

    body = _safe_str(cuerpo)
    fundamentos = _build_fundamentos_derecho(tipo, core)
    suplico = _build_unified_suplico(tipo)

    if re.search(r"\bIII\.\s*(SOLICITO|SUPLICO)\b", body, flags=re.IGNORECASE):
        body = re.sub(
            r"\bIII\.\s*(?:SOLICITO|SUPLICO)\b[\s\S]*$",
            fundamentos + "\n" + suplico,
            body,
            flags=re.IGNORECASE,
        )
    else:
        body = body.rstrip() + "\n\n\n" + fundamentos + "\n" + suplico

    body = fix_roman_headings(body)
    body = _strip_initial_antecedentes_block(body)
    body = re.sub(r"\bII\.\s*ALEGACIONES\b", "I. ALEGACIONES", body, count=1, flags=re.IGNORECASE)
    body = re.sub(r"\n{4,}", "\n\n\n", body).strip() + "\n"

    body = cabecera + body

    return {"asunto": asunto_out, "cuerpo": body}


def build_cinturon_v4_template(core: Dict[str, Any]) -> Dict[str, str]:
    tpl = build_cinturon_strong_template(core)
    if not isinstance(tpl, dict):
        return {"asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE", "cuerpo": str(tpl or "")}

    subtipo = _safe_str(core.get("subtipo_infraccion")).lower().strip()
    evidence_gaps = core.get("evidence_gaps") or []
    extra = ""

    if subtipo == "cinturon_redaccion_ambigua":
        extra += (
            "\n\nALEGACIÓN ESPECÍFICA — AMBIGÜEDAD DEL HECHO IMPUTADO\n\n"
            "La propia redacción del boletín resulta internamente equívoca al combinar fórmulas propias del no uso absoluto con referencias a un supuesto cinturón 'correctamente abrochado'. "
            "Esa formulación híbrida impide conocer con precisión qué conducta concreta se atribuye realmente: ausencia total de uso, uso incorrecto, mal abrochado o colocación defectuosa. "
            "Tal indeterminación debilita la tipicidad y exige una descripción mucho más concreta y circunstanciada del hecho imputado.\n"
        )
    elif subtipo == "cinturon_mal_abrochado":
        extra += (
            "\n\nALEGACIÓN ESPECÍFICA — FALTA DE PRECISIÓN MATERIAL\n\n"
            "No basta afirmar de manera estereotipada que el cinturón no estaba correctamente abrochado. "
            "Debe concretarse si se observó ausencia total, mala fijación, colocación defectuosa o desabrochado momentáneo, con detalle bastante para permitir contradicción efectiva.\n"
        )

    if evidence_gaps:
        bullets = []
        gap_map = {
            "no_prueba_objetiva": "No consta fotografía, vídeo ni soporte objetivo adicional.",
            "distancia_no_acreditada": "No se precisa la distancia de observación.",
            "posicion_agente_no_acreditada": "No consta la posición exacta del agente respecto del vehículo.",
            "duracion_observacion_no_acreditada": "No se concreta el tiempo durante el cual se mantuvo la observación.",
            "visibilidad_no_acreditada": "No se describen las condiciones de visibilidad concurrentes.",
            "concrecion_missing": "No se precisa si se imputa ausencia total, mal abrochado o colocación incorrecta.",
        }
        for g in evidence_gaps:
            if g in gap_map:
                bullets.append("• " + gap_map[g])
        if bullets:
            extra += "\n\nREFUERZO PROBATORIO\n\n" + "\n".join(bullets) + "\n"

    body = _safe_str(tpl.get("cuerpo"))
    if extra and extra not in body:
        insert_after = "II. ALEGACIONES\n\n"
        if insert_after in body:
            body = body.replace(insert_after, insert_after + extra + "\n", 1)
        else:
            body += extra

    tpl["cuerpo"] = body
    return tpl


def build_atencion_bicicleta_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = get_hecho_para_recurso(core) or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FALTA DE DESCRIPCIÓN SUFICIENTE Y CIRCUNSTANCIADA\n\n"
        "La denuncia describe una conducta observada durante la circulación en bicicleta, pero no concreta con el detalle exigible la conducta exacta, su duración, ni las circunstancias espaciales y temporales que permitirían verificarla con fiabilidad.\n\n"
        "ALEGACIÓN SEGUNDA — AUSENCIA DE SOPORTE OBJETIVO Y DE DATOS DE OBSERVACIÓN\n\n"
        "No consta en el expediente soporte objetivo adicional, ni se precisa desde qué posición se realizó la observación, a qué distancia ni durante cuánto tiempo, extremos imprescindibles para valorar la consistencia de una observación de este tipo en vía abierta.\n\n"
        "ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DE LA CONDUCTA DENUNCIADA\n\n"
        "Tratándose de una persona que circula en bicicleta junto con otros ciclistas, la Administración debe concretar de forma especialmente rigurosa la posición exacta del denunciante respecto del ciclista, la visibilidad existente y la forma en que se individualizó la conducta denunciada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa para contradicción efectiva.\n"
    )
    return {
        "asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(cuerpo),
    }


def _is_bicicleta_context(core: Dict[str, Any]) -> bool:
    contexto = _safe_str(core.get("contexto_movilidad")).lower().strip()
    if contexto == "bicicleta":
        return True
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    return any(s in blob for s in ["bicicleta", "ciclista", "ciclistas", "arcen", "arcén"])


def _sanitize_bicicleta_body(body: str) -> str:
    txt = _safe_str(body)
    if not txt:
        return txt

    txt = txt.replace("ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO", "ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DE LA CONDUCTA DENUNCIADA")
    txt = txt.replace("La denuncia describe conductas realizadas dentro del habitáculo del vehículo.", "La denuncia atribuye una conducta observada durante la circulación en bicicleta junto con otros ciclistas.")
    txt = txt.replace("interior del vehículo", "circulación en bicicleta")
    txt = txt.replace("habitáculo del vehículo", "entorno de circulación")
    txt = txt.replace("dentro del vehículo", "durante la circulación")

    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt




def build_camion_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "[EXPEDIENTE]"
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = get_hecho_para_recurso(core, forced_tipo="transporte_profesional") or "INFRACCIÓN EN TRANSPORTE PROFESIONAL"
    subtipo = _safe_str(core.get("subtipo_infraccion")).lower().strip()

    subtipo_title = "TRANSPORTE PROFESIONAL"
    subtipo_text = (
        "La denuncia se refiere a una presunta infracción en materia de transporte profesional, "
        "sector sometido a normativa técnica específica y a un estándar reforzado de motivación y prueba."
    )
    requisitos = [
        "La norma sectorial concreta supuestamente vulnerada.",
        "El acta de inspección o documento de control completo.",
        "La identificación precisa del vehículo y, en su caso, del conductor.",
        "La prueba técnica u objetiva que sustenta la imputación.",
        "La motivación individualizada de la conducta y su encaje en el tipo aplicado.",
    ]

    if subtipo == "camion_tacografo":
        subtipo_title = "TACÓGRAFO / TIEMPOS DE CONDUCCIÓN Y DESCANSO"
        subtipo_text = (
            "La imputación exige identificar con precisión la concreta irregularidad atribuida al tacógrafo, "
            "a los tiempos de conducción o a los descansos, así como aportar la descarga, impresión o registro "
            "íntegro que permita contradicción real."
        )
        requisitos += [
            "La descarga completa del tacógrafo o la impresión original utilizada.",
            "La identificación de la tarjeta del conductor o disco-diagrama afectado.",
            "La concreta franja temporal analizada y el criterio normativo aplicado.",
        ]
    elif subtipo == "camion_estiba":
        subtipo_title = "ESTIBA / SUJECIÓN DE LA CARGA"
        subtipo_text = (
            "En materia de estiba no basta una afirmación genérica sobre el riesgo. Debe constar una descripción "
            "técnica concreta de la carga, su forma de sujeción, los puntos de anclaje, la supuesta deficiencia observada "
            "y el soporte objetivo que documente la situación real."
        )
        requisitos += [
            "Reportaje fotográfico o soporte objetivo de la estiba observada.",
            "Descripción concreta del defecto de sujeción y del riesgo apreciado.",
            "Referencia normativa sectorial aplicada a la concreta carga transportada.",
        ]
    elif subtipo == "camion_neumaticos":
        subtipo_title = "NEUMÁTICOS"
        subtipo_text = (
            "Si la imputación se funda en el estado de los neumáticos, la Administración debe acreditar mediante "
            "medición o constatación técnica objetiva cuál era la profundidad del dibujo, el neumático afectado "
            "y por qué ese estado infringía exactamente la norma sectorial aplicable."
        )
        requisitos += [
            "Medición concreta de profundidad o defecto apreciado.",
            "Identificación del eje y neumático afectados.",
            "Soporte fotográfico o técnico suficientemente legible.",
        ]
    elif subtipo == "camion_peso":
        subtipo_title = "PESAJE / SOBRECARGA"
        subtipo_text = (
            "Las infracciones por exceso de peso requieren una acreditación muy rigurosa del sistema de pesaje, "
            "de la fecha, del ticket emitido y del concreto peso total o por eje atribuido al vehículo."
        )
        requisitos += [
            "Ticket o acta oficial de pesaje.",
            "Identificación del sistema de báscula utilizado y su validez.",
            "Detalle del peso total o por eje y de la MMA aplicable.",
        ]
    elif subtipo == "camion_documentacion":
        subtipo_title = "DOCUMENTACIÓN DEL TRANSPORTE"
        subtipo_text = (
            "Cuando la imputación se refiere a documentación del transporte, debe concretarse con precisión qué documento "
            "faltaba, estaba caducado o era insuficiente, y cuál era la obligación jurídica exacta incumplida."
        )
    elif subtipo == "camion_limitador":
        subtipo_title = "LIMITADOR DE VELOCIDAD"
        subtipo_text = (
            "Las infracciones relativas al limitador de velocidad exigen identificación técnica del equipo, del defecto "
            "detectado y del método de comprobación utilizado."
        )
    elif subtipo == "camion_adr":
        subtipo_title = "MERCANCÍAS PELIGROSAS / ADR"
        subtipo_text = (
            "En materia ADR la Administración debe concretar con especial precisión la obligación infringida, el tipo de "
            "mercancía, el vehículo afectado y la prueba objetiva del incumplimiento."
        )

    bullets = "\n".join(f"{i+1}) {r}" for i, r in enumerate(requisitos[:8]))

    cuerpo = (
        "A la atención del órgano competente.\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        f"ALEGACIÓN PRIMERA — {subtipo_title}: FALTA DE PRECISIÓN TÉCNICA Y NORMATIVA\n\n"
        f"{subtipo_text}\n\n"
        "No consta acreditado en el expediente, de forma completa y verificable:\n"
        f"{bullets}\n\n"
        "ALEGACIÓN SEGUNDA — INSUFICIENCIA PROBATORIA Y CARGA DE LA PRUEBA\n\n"
        "La Administración no puede sostener válidamente una sanción de contenido técnico con una mera referencia "
        "genérica a la existencia de una infracción. Resulta imprescindible aportar prueba objetiva bastante, acta "
        "de inspección completa y motivación individualizada que permita contradicción real.\n\n"
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo el acta o boletín de control, la normativa "
        "sectorial exacta aplicada, los documentos técnicos utilizados para la imputación y cualquier fotografía, "
        "medición, descarga, ticket o soporte objetivo en que la Administración pretenda fundar la sanción.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de concreción técnica suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba técnica completa para contradicción efectiva.\n"
    )

    return {
        "asunto": "ESCRITO DE ALEGACIONES — TRANSPORTE PROFESIONAL",
        "cuerpo": fix_roman_headings(cuerpo),
    }

def _select_template(core: Dict[str, Any], tipo: str, jurisdiccion: str):
    if tipo == "semaforo" and jurisdiccion == "municipal":
        return build_municipal_semaforo_template(core), "municipal_semaforo"
    elif tipo == "semaforo":
        return build_semaforo_strong_template(core), "semaforo"
    elif tipo == "velocidad":
        return build_velocity_strong_template(core), "velocidad"
    elif tipo == "movil":
        return build_movil_strong_template(core), "movil"
    elif tipo == "auriculares":
        return build_auriculares_strong_template(core), "auriculares"
    elif tipo == "cinturon":
        return build_cinturon_v4_template(core), "cinturon"
    elif tipo == "casco":
        return build_casco_strong_template(core), "casco"
    elif tipo == "atencion":
        if _is_bicicleta_context(core):
            return build_atencion_bicicleta_template(core), "atencion_bicicleta"
        return build_atencion_strong_template(core), "atencion"
    elif tipo == "marcas_viales":
        return build_marcas_viales_strong_template(core), "marcas_viales"
    elif tipo == "seguro":
        return build_seguro_strong_template(core), "seguro"
    elif tipo == "itv":
        return build_itv_strong_template(core), "itv"
    elif tipo == "condiciones_vehiculo":
        return build_condiciones_vehiculo_strong_template(core), "condiciones_vehiculo"
    elif tipo == "carril":
        return build_carril_strong_template(core), "carril"
    elif tipo == "transporte_profesional":
        return build_camion_template(core), "camiones"
    elif jurisdiccion == "municipal":
        blob = json.dumps(core, ensure_ascii=False).lower()
        if "sentido contrario" in blob or "direccion prohibida" in blob or "dirección prohibida" in blob:
            return build_municipal_sentido_contrario_template(core), "municipal_sentido_contrario"
        elif _looks_like_semaforo(core):
            return build_municipal_semaforo_template(core), "municipal_semaforo_fallback"
        else:
            return build_municipal_generic_template(core), "municipal_generic"
    else:
        return build_generic_body(core), "generic"


def ensure_tpl_dict(tpl: Any, core: Dict[str, Any]) -> Dict[str, str]:
    if isinstance(tpl, dict):
        asunto = tpl.get("asunto")
        cuerpo = tpl.get("cuerpo")
        if isinstance(asunto, str) and asunto.strip() and isinstance(cuerpo, str) and cuerpo.strip():
            return {"asunto": asunto.strip(), "cuerpo": fix_roman_headings(cuerpo.strip())}

    fallback = build_generic_body(core)
    return {
        "asunto": fallback.get("asunto") or "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(fallback.get("cuerpo") or "A la atención del órgano competente."),
    }


def build_velocity_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "[EXPEDIENTE]"
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."

    facts = _resolve_velocity_facts(core)
    measured = facts.get("measured")
    limit = facts.get("limit")
    conflict = facts.get("conflict")

    if measured and limit:
        hecho = f"Presunto exceso de velocidad con medición consignada de {int(measured)} km/h en tramo limitado a {int(limit)} km/h"
    else:
        hecho = "PRESUNTO EXCESO DE VELOCIDAD"

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    radar_profile = _resolve_radar_profile(core)
    radar = radar_profile.get("label") or "cinemómetro (modelo no consignado en la copia)"
    margin_info = _velocity_margin_info_from_profile(measured, radar_profile)
    margin_value = margin_info.get("margin_value")
    corrected_speed = margin_info.get("corrected_speed")
    margin_label = margin_info.get("margin_label")
    radar_focus = radar_profile.get("attack_focus") or ""

    tech_lines = []
    margin_txt = ""
    corrected_txt = ""
    if measured:
        tech_lines.append(f"• Velocidad medida: {int(measured)} km/h")
    if limit:
        tech_lines.append(f"• Velocidad límite: {int(limit)} km/h")
    if radar:
        tech_lines.append(f"• Dispositivo de control: {radar}")
    if margin_value is not None:
        if isinstance(margin_value, float) and not margin_value.is_integer():
            margin_txt = f"{margin_value:.2f}".replace(".", ",")
        else:
            margin_txt = str(int(margin_value))
        if isinstance(corrected_speed, float) and not corrected_speed.is_integer():
            corrected_txt = f"{corrected_speed:.2f}".replace(".", ",")
        else:
            corrected_txt = str(int(corrected_speed))
        tech_lines.append(f"• Margen mínimo de corrección aplicable: {margin_txt} km/h ({margin_label})")
        tech_lines.append(f"• Velocidad resultante tras la corrección: {corrected_txt} km/h")
    if conflict:
        tech_lines.append("• Observación: del examen de la copia aportada se desprenden discrepancias numéricas que exigen la exhibición del expediente íntegro y de la prueba técnica original.")

    tech_block = ""
    if tech_lines:
        tech_block = "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n" + "\n".join(tech_lines) + "\n\n"

    if measured and limit and measured > limit and not conflict:
        calc_paragraph = (
            "A efectos de contradicción, la Administración debe acreditar de forma documental la velocidad "
            "medida, el límite aplicable, el margen efectivamente aplicado y la velocidad corregida resultante. "
            f"Tomando como referencia la medición consignada de {int(measured)} km/h, el margen mínimo de corrección aplicable "
            f"sería de {margin_txt} km/h, lo que dejaría una velocidad resultante tras la corrección de {corrected_txt} km/h."
        )
        tramo_paragraph = build_tramo_error_paragraph({
            **core,
            "velocidad_medida_kmh": measured,
            "velocidad_limite_kmh": limit,
        })
    else:
        calc_paragraph = (
            "A efectos de contradicción, la Administración debe acreditar de forma documental la velocidad "
            "medida, el límite aplicable, el margen efectivamente aplicado y la velocidad corregida resultante, "
            "evitando cualquier duda derivada de lecturas automatizadas o transcripciones defectuosas del boletín."
        )
        tramo_paragraph = ""

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA DEL DISPOSITIVO DE CONTROL\n\n"
        "La imputación por exceso de velocidad exige acreditación técnica completa y verificable. No basta "
        "una referencia genérica al radar o cinemómetro: debe constar de forma precisa el dispositivo utilizado, "
        "su situación exacta, su verificación metrológica vigente y la trazabilidad íntegra del dato captado. "
        f"{radar_focus}\n\n"
        "No consta acreditado de forma completa en el expediente:\n"
        "1) Identificación completa del cinemómetro utilizado (marca/modelo/número de serie).\n"
        "2) Certificado de verificación metrológica vigente en la fecha del hecho.\n"
        "3) Acreditación del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020 o la normativa metrológica que corresponda en la fecha del hecho).\n"
        "4) Captura o fotograma completo y legible, con identificación inequívoca del vehículo.\n"
        "5) Aplicación concreta del margen y determinación de la velocidad corregida.\n"
        "6) Acreditación de la cadena de custodia del dato y su correspondencia inequívoca con el vehículo denunciado.\n"
        "7) Acreditación del límite aplicable y de su señalización en el punto exacto.\n\n"
        f"{tech_block}"
        f"{calc_paragraph}\n\n"
    )

    if tramo_paragraph:
        cuerpo += f"{tramo_paragraph}\n\n"

    cuerpo += (
        "ALEGACIÓN SEGUNDA — DEFECTOS DE MOTIVACIÓN Y FALTA DE SOPORTE COMPLETO\n\n"
        "La Administración debe motivar de forma individualizada por qué la velocidad atribuida, una vez aplicado "
        "el margen correspondiente, encaja exactamente en el tramo sancionador impuesto. Sin fotograma completo, "
        "certificado metrológico, identificación técnica del equipo y acreditación de la cadena de custodia, no puede "
        "enervarse la presunción de inocencia con el rigor exigible en Derecho sancionador.\n\n"
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo: boletín/denuncia completa, fotograma o secuencia "
        "completa, certificado de verificación metrológica, identificación del equipo, documentación técnica del control "
        "y motivación detallada del tramo sancionador aplicado.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba técnica completa para contradicción efectiva.\n"
    )

    return {
        "asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(cuerpo),
    }



def build_v2_dgt_layout(cuerpo: str, core: Dict[str, Any], interesado: Dict[str, Any]) -> str:
    """
    Inserta una cabecera tipo DGT con espacios del modelo oficial sin romper
    el resto del recurso. Sustituye la cabecera antigua del escrito y conserva
    desde el extracto literal del boletín hacia abajo.
    """
    core = core or {}
    interesado = interesado or {}

    def g(k: str, default: str = "") -> str:
        value = interesado.get(k)
        if value in (None, "", [], {}):
            value = core.get(k)
        if value in (None, "", [], {}):
            value = default
        return str(value)

    def _cleanup(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _infer_provincia() -> str:
        # prioridad: provincia explícita
        prov = _cleanup(g("provincia", ""))
        if prov:
            prov = prov.upper().replace("TRÁFICO DE", "").replace("TRAFICO DE", "").strip(" .,-")
            if prov:
                return prov

        # intentar inferir desde organismo / cabecera
        candidates = [
            _cleanup(g("organismo", "")),
            _cleanup(g("organismo_cabecera", "")),
            _cleanup(g("destination", "")),
            _cleanup(g("delivery_destination", "")),
        ]
        for cand in candidates:
            upper = cand.upper()
            for marker in ["JEFATURA PROVINCIAL DE TRÁFICO DE ", "JEFATURA PROVINCIAL DE TRAFICO DE "]:
                if marker in upper:
                    return upper.split(marker, 1)[1].strip(" .,-")
        return "........"

    def _infer_organismo_destino() -> str:
        cand = _cleanup(g("organismo", "")) or _cleanup(g("organismo_cabecera", ""))
        upper = cand.upper()

        if "JEFATURA PROVINCIAL DE TRÁFICO" in upper or "JEFATURA PROVINCIAL DE TRAFICO" in upper:
            return "JEFATURA PROVINCIAL DE TRÁFICO"

        if "DIRECCIÓN GENERAL DE TRÁFICO" in upper or "DIRECCION GENERAL DE TRAFICO" in upper:
            return "JEFATURA PROVINCIAL DE TRÁFICO"

        if "MINISTERIO DEL INTERIOR" in upper and "TRAFICO" in upper:
            return "JEFATURA PROVINCIAL DE TRÁFICO"

        if "AYUNTAMIENTO" in upper:
            return "AYUNTAMIENTO"

        if "AJUNTAMENT" in upper:
            return "AJUNTAMENT"

        if "POLICÍA LOCAL" in upper or "POLICIA LOCAL" in upper:
            return "POLICÍA LOCAL"

        if "GUARDIA URBANA" in upper:
            return "GUARDIA URBANA"

        return "JEFATURA PROVINCIAL DE TRÁFICO"

    def _strip_old_header(text: str) -> str:
        txt = str(text or "").replace("\r\n", "\n")
        # quitar restos típicos de cabecera vieja
        txt = txt.replace("A la atención del órgano competente,", "")
        txt = txt.replace("A la atención del órgano competente", "")
        # conservar desde el extracto literal, si existe
        markers = [
            "Extracto literal del boletín:",
            "Extracto literal del boletin:",
            "I. ALEGACIONES",
        ]
        for marker in markers:
            idx = txt.find(marker)
            if idx >= 0:
                return txt[idx:].lstrip()
        return txt.strip()

    provincia = _infer_provincia()
    organismo_destino = _infer_organismo_destino()

    body = _strip_old_header(cuerpo)

    header = f"""REFERENCIA: EXPTE. {g("expediente_ref", "........")}

ESCRITO DE ALEGACIONES

A LA {organismo_destino} DE {provincia}

1.- DATOS DE LA DENUNCIA

Nº EXPEDIENTE: {g("expediente_ref")}
CARRETERA / LUGAR: {g("lugar_infraccion")}
FECHA DE LA DENUNCIA: {g("fecha_infraccion")}
MATRÍCULA: {g("matricula")}
MARCA / MODELO: {g("marca_modelo")}

2.- DATOS DEL RECURRENTE

PRIMER APELLIDO: {g("apellido1")}
SEGUNDO APELLIDO: {g("apellido2")}
NOMBRE: {g("nombre")}
DNI/NIE: {g("dni")}

DOMICILIO: {g("domicilio")}
LOCALIDAD: {g("localidad")}    PROVINCIA: {g("provincia")}    CP: {g("cp")}

TELÉFONO: {g("telefono")}
EMAIL: {g("email")}

3.- NATURALEZA DEL ESCRITO

[X] ESCRITO DE ALEGACIONES
[ ] RECURSO DE REPOSICIÓN

------------------------------------------------------------"""

    return header.strip() + "\n\n" + body.strip()

def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None, forced_tipo: Optional[str] = None) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    if (
        not core.get("hecho_denunciado_literal")
        and not core.get("hecho_para_recurso")
        and not core.get("hecho_imputado")
        and not core.get("hecho_denunciado_resumido")
    ):
        literal = extract_hecho_denunciado_literal(core)
        if literal:
            core["hecho_denunciado_literal"] = literal

    tipo = forced_tipo or _resolved_tipo_from_core(core, fallback="generic")
    jurisdiccion = resolve_jurisdiction(core)

    bicicleta_ctx = _is_bicicleta_context(core)

    # V5 bloqueada: no redispatch heurístico si ya hay familia resuelta upstream.
    tpl, final_kind = _select_template(core, tipo, jurisdiccion)

    tpl = ensure_tpl_dict(tpl, core)
    tpl = _upgrade_generated_template(
        tpl.get("asunto") or "",
        tpl.get("cuerpo") or "",
        tipo,
        core,
    )

    cuerpo = tpl.get("cuerpo") or ""
    if tipo == "atencion" and bicicleta_ctx:
        cuerpo = _sanitize_bicicleta_body(cuerpo)

    cuerpo = _inject_tipicidad_material_en_alegaciones(cuerpo, core)
    cuerpo = _inject_strategic_legal_reinforcement(cuerpo, core, tipo)
    cuerpo = re.sub(r'\bREFUERZO\s*[—-]\s*', '', cuerpo, flags=re.IGNORECASE)
    cuerpo = re.sub(r'\bESTRATEGIA PRINCIPAL\b', 'INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS', cuerpo, flags=re.IGNORECASE)
    cuerpo = re.sub(r'\bFACTORES ADICIONALES\b', 'CONSIDERACIONES COMPLEMENTARIAS', cuerpo, flags=re.IGNORECASE)
    cuerpo = re.sub(r'\bCONSIDERACIONES ADICIONALES\b', 'CONSIDERACIONES COMPLEMENTARIAS', cuerpo, flags=re.IGNORECASE)
    cuerpo = re.sub(r'\bALEGACIÓN\s+DE\s+\s*NULIDAD\s+DE\s+PLENO\s+DERECHO\b', 'ALEGACIÓN — NULIDAD DE PLENO DERECHO', cuerpo, flags=re.IGNORECASE)
    cuerpo = re.sub(r'\nA la atenci[oó]n del Ayuntamiento competente,\s*\nI\. ANTECEDENTES\s*\n', '\n', cuerpo, flags=re.IGNORECASE)

    hecho = _clean_hecho_para_recurso(get_hecho_para_recurso(core, forced_tipo=tipo), tipo=tipo, core=core)
    if hecho and not _looks_like_internal_extract(hecho):
        cuerpo = _integrate_extract_after_comparecencia(cuerpo, hecho, core, forced_tipo=tipo)

    cuerpo = _replace_hecho_imputado_line_with_clean(cuerpo, hecho)
    cuerpo = _apply_strategy_mode_to_body(cuerpo, core, tipo)
    cuerpo = _fix_alegaciones_numeracion(cuerpo)
    cuerpo = _apply_premium_legal_formatting(cuerpo)
    cuerpo = _fix_alegacion_titles(cuerpo)
    cuerpo = _upgrade_bullets(cuerpo)
    tpl["cuerpo"] = fix_roman_headings(cuerpo)
    tpl["cuerpo"] = build_v2_dgt_layout(tpl["cuerpo"], core, interesado or {})
    # FIX FINAL: fuerza mayúsculas después de todo el post-procesado
    tpl["cuerpo"] = re.sub(
        r"(?im)^ALEGACIÓN TERCERA\s+—\s+SOLICITUD DE expediente íntegro Y PRUEBA TÉCNICA",
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA",
        tpl["cuerpo"]
    )

    
    docx_bytes = build_docx("", tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf("", tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(case_id, "generated", pdf_bytes, ".pdf", "application/pdf")

    conn.execute(
        text("""
            INSERT INTO documents (case_id, kind, b2_bucket, b2_key, mime, created_at)
            VALUES (:case_id, :kind_docx, :bucket, :key_docx, :mime_docx, NOW()),
                   (:case_id, :kind_pdf,  :bucket, :key_pdf,  :mime_pdf,  NOW())
        """),
        {
            "case_id": case_id,
            "kind_docx": f"{final_kind}_docx",
            "kind_pdf": f"{final_kind}_pdf",
            "bucket": b2_bucket,
            "key_docx": b2_key_docx,
            "key_pdf": b2_key_pdf,
            "mime_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "mime_pdf": "application/pdf",
        },
    )

    destination_text = _extract_destination_from_generated_body(tpl["cuerpo"])

    return {
        "ok": True,
        "kind": final_kind,
        "asunto": tpl["asunto"],
        "cuerpo": tpl["cuerpo"],
        "docx": {"bucket": b2_bucket, "key": b2_key_docx},
        "pdf": {"bucket": b2_bucket, "key": b2_key_pdf},
        "tipo_infraccion": tipo,
        "jurisdiccion": jurisdiccion,
        "delivery": {
            "destination_text": destination_text,
            "source": "generate",
        },
    }




def _extract_destination_from_generated_body(body: str) -> str:
    txt = _safe_str(body)
    if not txt.strip():
        return ""
    for line in txt.splitlines():
        clean = line.strip()
        upper = clean.upper()
        if upper.startswith("A LA JEFATURA PROVINCIAL DE TRÁFICO DE "):
            return clean
        if upper.startswith("A LA JEFATURA PROVINCIAL DE TRAFICO DE "):
            return clean
        if upper.startswith("A LA DIRECCIÓN GENERAL DE TRÁFICO"):
            return clean
        if upper.startswith("A LA DIRECCION GENERAL DE TRAFICO"):
            return clean
        if upper.startswith("AL AYUNTAMIENTO DE "):
            return clean
        if upper.startswith("A L'AJUNTAMENT DE "):
            return clean
        if upper.startswith("A LA POLICÍA LOCAL DE "):
            return clean
        if upper.startswith("A LA POLICIA LOCAL DE "):
            return clean
        if upper.startswith("A LA GUARDIA URBANA DE "):
            return clean
    return ""

class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, forced_tipo=req.tipo)
    return {"ok": True, "message": "Recurso generado.", **result}
