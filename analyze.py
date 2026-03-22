import json
import hashlib
import mimetypes
import re
from typing import Any, Dict, List, Tuple, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_original
from openai_vision import extract_from_image_bytes
from text_extractors import (
    extract_text_from_pdf_bytes,
    extract_text_from_docx_bytes,
    has_enough_text,
)
from openai_text import extract_from_text
from hecho_imputado_engine import extract_hecho_imputado

router = APIRouter(tags=["analyze"])

DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _flatten_text(extracted_core: Dict[str, Any], text_content: str = "") -> str:
    parts: List[str] = []

    if isinstance(extracted_core, dict):
        preferred_keys = [
            "organismo",
            "expediente_ref",
            "tipo_sancion",
            "hecho_denunciado_literal",
            "hecho_denunciado_resumido",
            "hecho_imputado",
            "observaciones",
            "vision_raw_text",
            "raw_text_pdf",
            "raw_text_vision",
        ]

        used = set()

        for k in preferred_keys:
            if k in extracted_core:
                v = extracted_core.get(k)
                if v is None:
                    continue
                sv = _safe_str(v).strip()
                if sv:
                    parts.append(f"{k}: {sv}")
                used.add(k)

        for k, v in extracted_core.items():
            if k in used or v is None:
                continue
            sv = _safe_str(v).strip()
            if sv:
                parts.append(f"{k}: {sv}")

    if text_content:
        parts.append(text_content)

    return "\n".join(parts)


def _merge_extracted(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    primary = primary or {}
    secondary = secondary or {}
    out = dict(secondary)
    for k, v in primary.items():
        if v not in (None, "", [], {}):
            out[k] = v
    return out


def _normalize_for_matching(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("\r", "\n")
    t = t.replace("semáforo", "semaforo")
    t = t.replace("señal", "senal")
    t = t.replace("línea", "linea")
    t = t.replace("teléfono", "telefono")
    t = t.replace("móvil", "movil")
    t = t.replace("cinemómetro", "cinemometro")
    t = t.replace("inspección", "inspeccion")
    t = t.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").replace("ñ", "n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n+", "\n", t)
    return t.strip()


_HECHO_HEADERS = [
    "hecho denunciado",
    "hecho que se notifica",
    "lo que se notifica",
    "hecho imputado",
    "hecho infringido",
    "hecho infractor",
]


def _extract_labeled_hecho_field(raw_text: str, labels: List[str]) -> str:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ""

    original_text = raw_text.replace("\r", "\n")
    normalized_text = _normalize_for_matching(original_text)

    start_idx = None
    for h in labels:
        m = re.search(rf"{re.escape(h)}\s*[:\-]?\s*", normalized_text, flags=re.IGNORECASE)
        if m:
            start_idx = m.end()
            break

    if start_idx is None:
        return ""

    tail = original_text[start_idx:].strip()
    if not tail:
        return ""

    lines = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    if not lines:
        return ""

    current: List[str] = []
    started = False

    for ln in lines:
        if _is_admin_line(ln):
            if current:
                break
            continue

        norm = _normalize_for_matching(ln)

        if any(norm.startswith(_normalize_for_matching(lbl)) for lbl in _HECHO_HEADERS):
            if current:
                break
            continue

        if re.match(r"^\s*5[abc]\b", norm):
            ln = re.sub(r"^\s*5[abc]\s*", "", ln, flags=re.IGNORECASE).strip()
            norm = _normalize_for_matching(ln)

        if _looks_like_narrative_line(ln):
            started = True

        if started or current:
            current.append(ln)

        joined = " ".join(current).strip()
        if len(joined) >= 320:
            break

    return _clean_literal_text(" ".join(current))



_STOP_LINE_SIGNALS = [
    "datos vehiculo",
    "datos del vehiculo",
    "datos del interesado",
    "datos del conductor",
    "identificacion de la multa",
    "identificacion multa",
    "importe multa",
    "importe con reduccion",
    "puntos a detraer",
    "fecha limite",
    "motivo de no notificacion",
    "fecha y firma",
    "lugar de pago",
    "fecha decreto",
    "domicilio",
    "provincia",
    "codigo postal",
    "boletin",
    "agente denunciante",
    "telefono de informacion",
    "telefono de atencion",
    "fax",
    "correo ordinario",
    "correo certificado",
    "remitir el presente",
    "impreso relleno",
    "precepto infringido",
    "lugar de denuncia",
    "ejemplar para el infractor",
    "ejemplar para la infractora",
    "ejemplar para el/la infractor/a",
    "ejemplar para el/la infractor",
    "ejemplar para ella infractor",
    "identificacion de la multa",
    "identificación de la multa",
    "vehiculo titular",
    "apellidos y nombre del infractor",
    "identificador fiscal",
]

_ADMIN_KV_PREFIXES = [
    "organismo:",
    "expediente_ref:",
    "tipo_sancion:",
    "observaciones:",
    "vision_raw_text:",
    "raw_text_pdf:",
    "raw_text_vision:",
    "raw_text_blob:",
    "hecho_imputado:",
    "hecho_denunciado_literal:",
    "hecho_denunciado_resumido:",
    "fecha_documento:",
    "fecha_notificacion:",
    "importe:",
    "jurisdiccion:",
    "pone_fin_via_administrativa:",
    "plazo_recurso_sugerido:",
    "tipo_infraccion:",
    "facts_phrases:",
    "preceptos_detectados:",
    "articulo_infringido_num:",
    "apartado_infringido_num:",
    "norma_hint:",
    "tipo_infraccion_scores:",
    "tipo_infraccion_confidence:",
    "subtipo_infraccion:",
    "evidence_gaps:",
    "recurso_strategy:",
    "radar_modelo_hint:",
    "radar_tipo:",
    "metrologia_requerida:",
    "margen_legal_aplicado_hint_kmh:",
    "velocidad_corregida_kmh:",
    "tramo_sancionador_hint:",
    "velocidad_conflicto_detectado:",
]


def _clean_literal_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.strip()

    t = re.sub(
        r"^\s*(hecho denunciado|hecho que se notifica|hecho imputado|hecho infringido|hecho infractor)\s*[:\-]?\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"^\s*5[abc]\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r'^\s*[\"\'“”]+|[\"\'“”]+\s*$', "", t)
    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    t = re.sub(r"^(movil|m[oó]vil)\s+", "", t, flags=re.IGNORECASE)
    return t.strip()




def _is_internal_meta_line(line: str) -> bool:
    l = _normalize_for_matching(line)
    internal_tokens = [
        "pone_fin_via_administrativa",
        "plazo_recurso_sugerido",
        "tipo_infraccion_scores",
        "tipo_infraccion_confidence",
        "subtipo_infraccion",
        "evidence_gaps",
        "recurso_strategy",
        "radar_modelo_hint",
        "radar_tipo",
        "metrologia_requerida",
        "margen_legal_aplicado_hint_kmh",
        "velocidad_corregida_kmh",
        "tramo_sancionador_hint",
        "velocidad_conflicto_detectado",
        "facts_phrases",
        "preceptos_detectados",
        "raw_text_pdf",
        "raw_text_vision",
        "raw_text_blob",
        "vision_raw_text",
    ]
    return any(tok in l for tok in internal_tokens)

def _is_admin_line(line: str) -> bool:
    l = _normalize_for_matching(line)
    if _is_internal_meta_line(line):
        return True
    if any(l.startswith(p) for p in _ADMIN_KV_PREFIXES):
        return True
    if any(s in l for s in _STOP_LINE_SIGNALS):
        return True
    if re.match(r"^\s*\d+\.\s+", l):
        return any(k in l for k in ["datos", "fecha", "importe", "pago", "firma", "telefono", "correo"])
    return False


def _looks_like_narrative_line(line: str) -> bool:
    l = _normalize_for_matching(line)
    narrative_signals = [
        "conducir", "circular", "circulando", "circulaba", "cruce", "fase roja", "luz roja",
        "semaforo", "utilizando", "telefono", "movil", "auricular", "auriculares", "cascos",
        "bail", "palm", "golpe", "volante", "negligente", "atencion", "distraccion", "km/h",
        "velocidad", "cinemometro", "radar", "marca longitudinal", "linea continua", "itv",
        "seguro obligatorio", "alumbrado", "destellos", "porta auricular", "oido izquierdo",
        "oido derecho", "mordia las unas", "mordia las uñas", "libertad de movimientos",
        "sentido contrario", "direccion prohibida",
    ]
    if any(s in l for s in narrative_signals):
        return True
    if re.search(r"\b(?:circular|circulaba|circulando)\s+a\s+\d{2,3}\s*km", l):
        return True
    return False





def _looks_like_vehicle_ficha(text: str) -> bool:
    l = _normalize_for_matching(text)
    ficha_signals = [
        "itv: vigente",
        "itv vigente",
        "color:",
        "ano matric",
        "año matric",
        "mecanismo:",
        "potencia:",
        "turismo",
        "cilindrada",
        "bastidor",
        "marca:",
        "modelo:",
    ]
    hits = sum(1 for s in ficha_signals if s in l)
    return hits >= 2

def _score_candidate_hecho(line: str) -> int:
    l = _normalize_for_matching(line)

    bad = [
        "fecha caducidad documento",
        "referencia de cobro",
        "ejemplar para el",
        "ejemplar para la",
        "ejemplar para el/la",
        "identificacion de la multa",
        "vehiculo titular",
        "lugar de pago",
        "telefono",
        "fax",
        "correo ordinario",
        "impreso relleno",
        "remitir el presente",
        "notificaciones a traves de internet y movil",
        "notificaciones a través de internet y móvil",
        "puede recibir",
        "tablón edictal de sanciones de trafico",
        "tablon edictal de sanciones de trafico",
    ]
    if any(b in l for b in bad):
        return -100

    score = 0

    strong_verbs = [
        "no respetar",
        "superar",
        "circular",
        "utilizando",
        "no mantener",
        "carecer de",
        "conducir de forma negligente",
        "no llevar",
    ]
    for v in strong_verbs:
        if v in l:
            score += 20

    if "semaforo" in l or "fase roja" in l or "luz roja" in l:
        score += 10
    if "km/h" in l or "velocidad" in l:
        score += 10
    if "auricular" in l or "telefono movil" in l or "marca longitudinal continua" in l:
        score += 10

    return score


def _extract_hecho_denunciado_literal_from_text(raw_text: str) -> str:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ""

    original_text = raw_text.replace("\r", "\n")
    normalized_text = _normalize_for_matching(original_text)

    start_idx = None
    for h in _HECHO_HEADERS:
        m = re.search(rf"{re.escape(h)}\s*[:\-]?\s*", normalized_text, flags=re.IGNORECASE)
        if m:
            start_idx = m.end()
            break

    tail = ""
    if start_idx is not None:
        tail = original_text[start_idx:].strip()
    else:
        lines_fb = [ln.strip() for ln in original_text.split("\n") if ln.strip()]
        start_pos = None
        for i, ln in enumerate(lines_fb):
            if _looks_like_narrative_line(ln):
                start_pos = i
                break
        if start_pos is not None:
            tail = "\n".join(lines_fb[start_pos:]).strip()

    if not tail:
        return ""

    lines = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    if not lines:
        return ""

    candidates: List[str] = []
    current: List[str] = []
    started = False

    for ln in lines:
        if _is_admin_line(ln):
            if current:
                candidates.append(" ".join(current))
                current = []
            started = False
            continue

        norm = _normalize_for_matching(ln)

        if re.match(r"^\s*5[abc]\b", norm):
            cleaned = re.sub(r"^\s*5[abc]\s*", "", ln, flags=re.IGNORECASE).strip()
            if cleaned:
                if current:
                    candidates.append(" ".join(current))
                current = [cleaned]
                started = True
            continue

        if _looks_like_narrative_line(ln):
            if not started and current:
                candidates.append(" ".join(current))
                current = []
            started = True

        if started:
            current.append(ln)

        if len(" ".join(current)) > 1000:
            candidates.append(" ".join(current))
            current = []
            started = False

    if current:
        candidates.append(" ".join(current))

    if not candidates:
        second: List[str] = []
        for ln in lines:
            if _is_admin_line(ln):
                if second:
                    break
                continue
            second.append(ln)
            if len(" ".join(second)) > 1000:
                break
        if second:
            candidates.append(" ".join(second))

    cleaned_candidates: List[str] = []
    for c in candidates:
        cc = _clean_literal_text(c)
        if not cc:
            continue
        low = cc.lower()
        admin_poison = [
            "fax", "correo ordinario", "telefono de informacion", "teléfono de información",
            "telefono de atencion", "teléfono de atención", "remitir el presente", "impreso relleno",
            "ejemplar para el infractor", "ejemplar para la infractora", "ejemplar para el/la infractor/a",
            "identificacion de la multa", "vehiculo titular", "apellidos y nombre del infractor",
            "identificador fiscal", "fecha caducidad documento", "referencia de cobro",
            "pone_fin_via_administrativa", "plazo_recurso_sugerido", "tipo_infraccion_scores",
            "tipo_infraccion_confidence", "subtipo_infraccion", "evidence_gaps", "recurso_strategy",
            "radar_modelo_hint", "radar_tipo", "metrologia_requerida", "margen_legal_aplicado_hint_kmh",
            "velocidad_corregida_kmh", "tramo_sancionador_hint", "velocidad_conflicto_detectado",
            "raw_text_pdf", "raw_text_vision", "raw_text_blob", "vision_raw_text",
            "notificaciones a traves de internet y movil", "notificaciones a través de internet y móvil",
            "puede recibir", "tablón edictal de sanciones de trafico", "tablon edictal de sanciones de trafico",
        ]
        if any(s in low for s in admin_poison):
            continue
        if _looks_like_vehicle_ficha(cc):
            continue
        cleaned_candidates.append(cc)

    if not cleaned_candidates:
        return ""

    out = max(cleaned_candidates, key=_score_candidate_hecho)

    for kv in _ADMIN_KV_PREFIXES:
        pos = out.lower().find(kv)
        if pos > 0:
            out = out[:pos].strip()

    if len(out) > 700:
        out = out[:700].rsplit(" ", 1)[0].strip() + "…"

    out = out.strip()
    if _looks_like_vehicle_ficha(out):
        return ""

    return out

def _build_hecho_denunciado_resumido(literal: str, tipo_infraccion: str = "") -> str:
    text = _clean_literal_text(literal)
    if not text:
        return ""

    tipo = _normalize_for_matching(tipo_infraccion)

    if tipo == "velocidad":
        norm = _normalize_for_matching(text)
        m1 = re.search(r"\b(?:circular|circulaba|circulando)\s+a\s+(\d{2,3})\s*km", norm)
        m2 = re.search(r"\b(?:limitad[ao]a?|limite|velocidad maxima|velocidad max)\b[^\d]{0,40}(\d{2,3})", norm)
        measured = m1.group(1) if m1 else None
        limit = m2.group(1) if m2 else None
        radar = ""
        if "multanova" in norm:
            radar = " detectado mediante cinemometro Multanova"
        elif "cinemometro" in norm or "radar" in norm:
            radar = " detectado mediante cinemometro/radar"
        if measured and limit:
            return f"Circular a {measured} km/h teniendo limitada la velocidad a {limit} km/h{radar}."

    if tipo == "semaforo":
        norm = _normalize_for_matching(text)
        if "fase roja" in norm or "luz roja" in norm or "semaforo" in norm:
            return "Cruce o rebase con fase roja del semaforo, segun consta en el boletin."

    if tipo == "movil":
        return "Presunto uso manual del telefono movil durante la conduccion, segun consta en el boletin."

    if tipo == "auriculares":
        return "Presunto uso de auriculares o cascos conectados durante la conduccion, segun consta en el boletin."

    if tipo == "atencion":
        short = text[:320].rsplit(" ", 1)[0].strip() if len(text) > 320 else text
        return short.rstrip(".") + "."

    short = text[:240].rsplit(" ", 1)[0].strip() if len(text) > 240 else text
    return short.rstrip(".") + "."


def _extract_preferred_hecho_fields(text_blob: str, core: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    core = core or {}

    literal_sources = [
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("vision_raw_text")),
        _safe_str(core.get("raw_text_pdf")),
        _safe_str(core.get("raw_text_vision")),
        _safe_str(core.get("raw_text_blob")),
        _safe_str(text_blob),
    ]

    literal = ""
    hecho_imputado_textual = ""

    for src in literal_sources:
        if not hecho_imputado_textual:
            hecho_imputado_textual = _extract_labeled_hecho_field(
                src,
                ["hecho imputado", "hecho infringido", "hecho infractor"],
            )
        literal = _extract_labeled_hecho_field(
            src,
            ["hecho denunciado", "hecho que se notifica", "lo que se notifica"],
        ) or _extract_hecho_denunciado_literal_from_text(src)
        if literal and len(literal) >= 12:
            break

    tipo_hint = _safe_str(core.get("tipo_infraccion"))
    resumido = _build_hecho_denunciado_resumido(literal, tipo_hint) if literal else ""

    return {
        "hecho_denunciado_literal": literal or None,
        "hecho_denunciado_resumido": resumido or None,
        "hecho_imputado_textual": hecho_imputado_textual or None,
    }





def _apply_hecho_engine(out: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(out or {})
    hecho_data = extract_hecho_imputado(out)

    out["hecho_engine"] = hecho_data
    out["hecho_confianza"] = hecho_data.get("confianza")
    out["hecho_motivo"] = hecho_data.get("motivo_baja_confianza")
    out["familia_sugerida_hecho"] = hecho_data.get("familia_sugerida")
    out["hecho_crudo"] = hecho_data.get("hecho_crudo")
    out["hecho_reconstruido"] = hecho_data.get("hecho_reconstruido")
    out["hecho_limpio"] = hecho_data.get("hecho_limpio")

    if hecho_data.get("hecho_limpio"):
        out["hecho_imputado"] = hecho_data["hecho_limpio"]
        if not out.get("hecho_denunciado_literal"):
            out["hecho_denunciado_literal"] = hecho_data["hecho_limpio"]

    out["needs_operator_review"] = bool(hecho_data.get("needs_operator_review"))
    out["hecho_bloqueado"] = bool(hecho_data.get("bloqueado"))

    conf = float(hecho_data.get("confianza") or 0.0)
    family_hint = _safe_str(hecho_data.get("familia_sugerida")).strip().lower()
    current_tipo = _safe_str(out.get("tipo_infraccion")).strip().lower()

    reasons = []
    if conf < 0.75:
        reasons.append("hecho_confianza_baja")
    if family_hint and current_tipo and family_hint != current_tipo:
        reasons.append("familia_hecho_no_coincide")
    if _safe_str(hecho_data.get("motivo_baja_confianza")):
        reasons.append(_safe_str(hecho_data.get("motivo_baja_confianza")))

    out["operator_review_reasons"] = reasons
    out["needs_operator_review"] = bool(out["needs_operator_review"] or reasons)
    return out


def _extract_precepts(text_blob: str) -> Dict[str, Any]:
    t = _normalize_for_matching(text_blob)

    precepts: List[str] = []
    art_num: Optional[int] = None
    apt_num: Optional[int] = None

    m_art = re.search(r"\bart[ií]culo\s*0?(\d{1,3})\b", t) or re.search(r"\bart\.\s*0?(\d{1,3})\b", t)
    if m_art:
        try:
            art_num = int(m_art.group(1))
            precepts.append(f"articulo {art_num}")
        except Exception:
            art_num = None

    m_apt = re.search(r"\bapartado\s*(\d{1,3})\b", t) or re.search(r"\baptdo\.?\s*(\d{1,3})\b", t)
    if m_apt:
        try:
            apt_num = int(m_apt.group(1))
            if art_num is not None:
                precepts.append(f"articulo {art_num} apartado {apt_num}")
            else:
                precepts.append(f"apartado {apt_num}")
        except Exception:
            apt_num = None

    m_code = re.search(r"\b0?(\d{1,3})\.(\d{1,3})\b", t)
    if m_code:
        try:
            art_from_code = int(m_code.group(1))
            apt_from_code = int(m_code.group(2))
            if art_num is None:
                art_num = art_from_code
                precepts.append(f"articulo {art_num}")
            if apt_num is None:
                apt_num = apt_from_code
            if art_num is not None and apt_num is not None:
                precepts.append(f"articulo {art_num} apartado {apt_num}")
        except Exception:
            pass

    norma_hint: Optional[str] = None

    if (
        ("r.d. legislativo" in t and "8/2004" in t)
        or ("rd legislativo" in t and "8/2004" in t)
        or ("8/2004" in t and "responsabilidad civil" in t)
    ):
        norma_hint = "RDL 8/2004"
        precepts.append("RDL 8/2004")

    if "reglamento general de circul" in t or "rgc" in t:
        precepts.append("Reglamento General de Circulacion")

    if "lsoa" in t:
        norma_hint = norma_hint or "LSOA"
        precepts.append("LSOA")

    seen = set()
    uniq: List[str] = []
    for p in precepts:
        pp = (p or "").strip()
        if pp and pp not in seen:
            seen.add(pp)
            uniq.append(pp)

    return {
        "preceptos_detectados": uniq,
        "articulo_num": art_num,
        "apartado_num": apt_num,
        "norma_hint": norma_hint,
    }


def _extract_speed_and_sanction_fields(text_blob: str) -> Dict[str, Any]:
    t = _normalize_for_matching(text_blob).replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()

    semaforo_hard_signals = [
        "luz roja no intermitente",
        "luz roja del semaforo",
        "luz roja de un semaforo",
        "no respetar el conductor de un vehiculo la luz roja",
        "cruce con fase del rojo",
        "cruce con fase roja",
        "fase del rojo",
        "fase roja",
        "senal luminosa roja",
        "linea de detencion",
        "articulo 146",
        "art. 146",
    ]
    semaforo_context = any(s in t for s in semaforo_hard_signals)

    radar_speed_context = (
        ("radar" in t)
        or ("cinemometro" in t)
        or ("exceso de velocidad" in t)
        or bool(re.search(r"\bcircular\s+a\s+\d{2,3}\s*km\s*/?\s*h\b", t))
        or bool(re.search(r"\bcirculaba\s+a\s+\d{2,3}\s*km\s*/?\s*h\b", t))
        or bool(re.search(r"\bvelocidad\s+medida\b", t))
        or bool(re.search(r"\bvelocidad\s+maxima\b", t))
    )

    velocity_context = (
        radar_speed_context
        or (
            ("km/h" in t)
            and not semaforo_context
            and bool(re.search(r"\bvelocidad\b", t))
        )
    )

    if semaforo_context and not radar_speed_context:
        return {
            "velocidad_medida_kmh": None,
            "velocidad_limite_kmh": None,
            "sancion_importe_eur": None,
            "puntos_detraccion": None,
            "radar_modelo_hint": None,
            "tipo_via_hint": None,
        }

    measured = None
    limit = None
    conflict = False
    candidates_all: List[int] = []

    if velocity_context:
        # 1) Todos los candidatos km/h
        for mm in re.finditer(r"\b(\d{2,3})\s*km\s*/?\s*h\b", t):
            try:
                n = int(mm.group(1))
                if 10 <= n <= 250:
                    candidates_all.append(n)
            except Exception:
                pass

        # 2) Prioridad máxima: patrón narrativo fuerte del hecho
        strong_measured: List[int] = []
        for mm in re.finditer(r"\b(?:circular|circulaba|circulando)\s+a\s+(\d{2,3})\s*km\s*/?\s*h\b", t):
            try:
                n = int(mm.group(1))
                if 10 <= n <= 250:
                    strong_measured.append(n)
            except Exception:
                pass

        if strong_measured:
            measured = max(strong_measured)

        # 3) Extraer límite con patrones fuertes
        t_no_deadlines = re.sub(r"fecha\s*l[ií]mite[^\d]{0,40}\d{1,2}/\d{1,2}/\d{2,4}", "", t)

        limit_candidates: List[int] = []
        for mm in re.finditer(
            r"\b(?:teniendo\s+limitada\s+la\s+velocidad\s+a|limitad[ao]a?|limitada\s+la\s+velocidad|l[ií]mite|limite|velocidad\s+m[aá]xima|velocidad\s+maxima)\b[^\d]{0,80}(\d{2,3})\b",
            t_no_deadlines,
        ):
            try:
                n = int(mm.group(1))
                if 10 <= n <= 200:
                    limit_candidates.append(n)
            except Exception:
                pass

        if limit_candidates:
            limit = sorted(set(limit_candidates))[0]

        # 4) Fallback si no hubo patrón narrativo fuerte
        if measured is None and candidates_all:
            if limit is not None:
                above = [x for x in candidates_all if x >= (limit + 5)]
                measured = max(above) if above else max(candidates_all)
            else:
                measured = max(candidates_all)

        # 5) Detección de conflicto
        uniq = sorted(set(candidates_all))
        if len(uniq) >= 2:
            if (max(uniq) - min(uniq)) >= 15:
                conflict = True
            if strong_measured and any(x != measured for x in uniq):
                conflict = True

        if measured is not None and limit is not None and measured <= limit:
            conflict = True

    fine_eur = None
    mf = re.search(r"\b(\d{2,4})\s*(?:€|euros)\b", t)
    if mf:
        try:
            fine_eur = int(mf.group(1))
        except Exception:
            fine_eur = None

    points = None
    mp = re.search(r"\b(\d)\s*puntos?\b", t)
    if mp:
        try:
            points = int(mp.group(1))
        except Exception:
            points = None

    radar_model = None
    tipo_via = None
    if "autovia" in t or "autovía" in t:
        tipo_via = "autovia"
    elif "autopista" in t:
        tipo_via = "autopista"
    elif "via urbana" in t or "vía urbana" in t or "zona urbana" in t or "urbana" in t:
        tipo_via = "urbana"
    elif "via interurbana" in t or "vía interurbana" in t or "carretera" in t:
        tipo_via = "interurbana"

    if velocity_context:
        mr = re.search(r"(multanova\s*[a-z0-9\-]*)", t)
        if mr:
            radar_model = mr.group(1).strip()
        elif "multaradar" in t:
            mr2 = re.search(r"(multaradar\s*[a-z0-9\-]*)", t)
            radar_model = mr2.group(1).strip() if mr2 else "multaradar"
        elif "cinem" in t:
            radar_model = "cinemometro (no especificado)"

    if ("bonificacion" in t or "bonificacion del 50" in t or "reduccion del 50" in t) and not radar_speed_context:
        measured = None
        limit = None
        candidates_all = []
        conflict = False

    out = {
        "velocidad_medida_kmh": measured,
        "velocidad_limite_kmh": limit,
        "sancion_importe_eur": fine_eur,
        "puntos_detraccion": points,
        "radar_modelo_hint": radar_model,
        "tipo_via_hint": tipo_via,
    }

    if velocity_context and candidates_all:
        out["velocidad_kmh_candidatos"] = sorted(set(candidates_all))

    if velocity_context and conflict:
        out["velocidad_conflicto_detectado"] = True

    return out




def _detect_mobility_context(text_blob: str, core: Optional[Dict[str, Any]] = None) -> str:
    core = core or {}
    blob = _normalize_for_matching(
        "\n".join([
            _safe_str(text_blob),
            _safe_str(core.get("hecho_denunciado_literal")),
            _safe_str(core.get("hecho_denunciado_resumido")),
            _safe_str(core.get("raw_text_blob")),
        ])
    )

    if any(s in blob for s in ["bicicleta", "ciclistas", "ciclista", "arcen", "arcén", "pedalea", "pedalear"]):
        return "bicicleta"
    if any(s in blob for s in ["vehiculo", "vehículo", "turismo", "camion", "camión", "matricula", "matrícula", "conductor"]):
        return "vehiculo"
    return "desconocido"

def _extract_jurisdiction(text_blob: str, core: Optional[Dict[str, Any]] = None) -> str:
    core = core or {}
    organismo = _normalize_for_matching(_safe_str(core.get("organismo")))
    t = _normalize_for_matching(text_blob)

    blob = f"{organismo}\n{t}"

    municipal_signals = [
        "ayuntamiento",
        "ajuntament",
        "concejalia de trafico",
        "policia local",
        "guardia urbana",
    ]
    estatal_signals = [
        "dgt",
        "direccion general de trafico",
        "jefatura provincial de trafico",
        "guardia civil",
        "ministerio del interior",
    ]

    if any(s in blob for s in municipal_signals):
        return "municipal"

    if any(s in blob for s in estatal_signals):
        return "estatal"

    return "desconocida"


def _detect_facts_and_type(text_blob: str, core: Optional[Dict[str, Any]] = None) -> Tuple[str, str, List[str]]:
    """
    Devuelve:
      (tipo_infraccion, hecho_imputado_canonico, facts_phrases)

    12 familias principales:
      1) condiciones_vehiculo
      2) casco
      3) auriculares
      4) cinturon
      5) movil
      6) semaforo
      7) velocidad
      8) seguro
      9) itv
      10) marcas_viales
      11) carril
      12) atencion
    """
    core = core or {}
    facts: List[str] = []

    t = _normalize_for_matching(text_blob)
    hecho_literal = _normalize_for_matching(_safe_str(core.get("hecho_denunciado_literal")))
    hecho_resumido = _normalize_for_matching(_safe_str(core.get("hecho_denunciado_resumido")))
    organismo = _normalize_for_matching(_safe_str(core.get("organismo")))
    tipo_sancion = _normalize_for_matching(_safe_str(core.get("tipo_sancion")))

    combined = "\n".join(
        [x for x in [t, hecho_literal, hecho_resumido, organismo] if x]
    ).strip()

    hecho_focus = "\n".join(
        [x for x in [hecho_literal, hecho_resumido] if x]
    ).strip()

    # -------------------------------------------------
    # 1) CONDICIONES DEL VEHÍCULO
    # -------------------------------------------------
    vehicle_light_context = any(
        s in combined
        for s in [
            "alumbrado",
            "senalizacion optica",
            "señalizacion optica",
            "senalizacion",
            "señalizacion",
            "luz trasera",
            "parte trasera",
            "parte delantera",
            "posterior",
            "trasera",
            "destellos",
            "intermitente",
            "oscilante",
            "anexo i",
            "reglamentacion del anexo",
            "reglamentación del anexo",
            "dispositivos de alumbrado",
            "dispositivos de senalizacion",
            "dispositivos de señalizacion",
            "dispositivos luminosos no autorizados",
            "dispositivos luminosos",
            "luces no reglamentarias",
            "luces azules",
            "luz azul",
            "panel rectangular",
            "no cumplan las exigencias",
            "condiciones reglamentarias",
            "homologacion",
            "homologación",
            "reflectante",
            "deslumbramiento",
            "espejo",
            "no homologada",
            "no homologado",
        ]
    )

    visibilidad_context = any(
        s in combined
        for s in [
            "superficie acristalada",
            "visibilidad diafana",
            "visibilidad diáfana",
            "visibilidad no diafana",
            "visibilidad no diáfana",
            "laminas",
            "láminas",
            "laminas adhesivas",
            "láminas adhesivas",
            "adhesivos",
            "cortinillas",
            "elementos no autorizados",
            "objetos adheridos",
            "parabrisas",
            "no permite a su conductor la visibilidad",
            "visibilidad suficiente",
            "visibilidad directa",
            "visibilidad del conductor",
        ]
    )

    if vehicle_light_context or visibilidad_context:
        facts.append("INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO")
        return ("condiciones_vehiculo", facts[0], facts)

    # -------------------------------------------------
    # 2) CASCO
    # -------------------------------------------------
    casco_context = any(
        s in combined
        for s in [
            "sin casco",
            "no llevar casco",
            "no utilizar casco",
            "no hacer uso del casco",
            "sin hacer uso del casco",
            "casco obligatorio",
            "casco reglamentario",
            "casco de proteccion",
            "casco de protección",
            "cascos de proteccion homologados",
            "cascos de protección homologados",
            "casco homologado",
            "casco abrochado",
            "debidamente abrochado",
            "sin hacer uso del casco de proteccion",
            "sin hacer uso del casco de protección",
            "anclado al casco",
            "camara de video",
            "cámara de vídeo",
            "camara en casco",
            "cámara en casco",
            "ciclomotor sin casco",
            "motociclista sin casco",
        ]
    )

    if casco_context:
        facts.append("NO UTILIZAR CASCO DE PROTECCIÓN")
        return ("casco", facts[0], facts)

    # -------------------------------------------------
    # 3) AURICULARES
    # -------------------------------------------------
    auriculares_context = any(
        s in combined
        for s in [
            "auricular",
            "auriculares",
            "cascos conectados",
            "cascos o auriculares",
            "reproductores de sonido",
            "aparatos receptores",
            "aparatos reproductores",
            "porta auricular",
            "oido izquierdo",
            "oído izquierdo",
            "oido derecho",
            "oído derecho",
            "bluetooth instalado en casco",
        ]
    )

    if auriculares_context:
        facts.append("USO DE AURICULARES O CASCOS CONECTADOS")
        return ("auriculares", facts[0], facts)

    # -------------------------------------------------
    # 4) CINTURÓN
    # -------------------------------------------------
    cinturon_context = any(
        s in combined
        for s in [
            "cinturon de seguridad",
            "cinturón de seguridad",
            "sin cinturon",
            "sin cinturón",
            "no utilizar el cinturon",
            "no utilizar el cinturón",
            "no llevar abrochado el cinturon",
            "no llevar abrochado el cinturón",
            "correctamente abrochado",
            "no utiliza el conductor del vehiculo el cinturon",
            "no utiliza el conductor del vehículo el cinturón",
        ]
    )

    if cinturon_context:
        facts.append("NO UTILIZAR CINTURÓN DE SEGURIDAD")
        return ("cinturon", facts[0], facts)

    # -------------------------------------------------
    # 5) MÓVIL
    # -------------------------------------------------
    movil_context = any(
        s in combined
        for s in [
            "telefono movil",
            "teléfono móvil",
            "uso manual del movil",
            "uso manual del móvil",
            "uso manual del telefono",
            "uso manual del teléfono",
            "utilizando manualmente",
            "sujetando con la mano el dispositivo",
            "manipulando el movil",
            "manipulando el móvil",
            "interactuando con la pantalla",
        ]
    )

    # Prioridad alta para atención / temeraria antes de semáforo
    atencion_hard_priority = any(
        s in combined
        for s in [
            "conducir de forma temeraria",
            "conduccion temeraria",
            "conducción temeraria",
            "conducir de forma negligente",
            "no mantener la atencion",
            "no mantener la atención",
        ]
    )
    if atencion_hard_priority:
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    # -------------------------------------------------
    # 6) SEMÁFORO
    # -------------------------------------------------
    semaforo_hard_signals = [
        "semaforo",
        "semáforo",
        "fase roja",
        "fase del rojo",
        "luz roja no intermitente",
        "luz roja del semaforo",
        "luz roja del semáforo",
        "luz roja de un semaforo",
        "luz roja de un semáforo",
        "cruce con fase roja",
        "cruce con fase del rojo",
        "cruce en rojo",
        "senal luminosa roja",
        "señal luminosa roja",
        "linea de detencion",
        "línea de detención",
        "rebase la linea de detencion",
        "rebasar la linea de detencion",
        "semaforo en rojo",
        "semáforo en rojo",
        "paso en rojo",
        "cruce fase roja",
        "articulo 146",
        "artículo 146",
        "art. 146",
        "no respetar el conductor de un vehiculo la luz roja",
        "no respetar el conductor de un vehículo la luz roja",
    ]

    semaforo_context = any(s in combined for s in semaforo_hard_signals) or (
        ("roja" in combined and "cruce" in combined)
        or ("roja" in combined and "detencion" in combined)
        or ("roja" in combined and "semaforo" in combined)
    )

    semaforo_false_friends = [
        "ordenarle la detencion",
        "al ordenarle la detencion",
        "orden de detencion",
        "no se para en el lugar",
        "no se para",
        "hacer caso omiso a la orden de detenerse",
        "desobedecer la orden de detencion",
    ]

    semaforo_false_friend_hit = any(s in combined for s in semaforo_false_friends)

    semaforo_legal_priority = (
        ("articulo 146" in combined or "artículo 146" in combined or "art. 146" in combined)
        or ("luz roja no intermitente" in combined and ("semaforo" in combined or "semáforo" in combined))
        or ("cruce con fase del rojo" in combined)
        or ("fase del rojo" in combined and ("cruce" in combined or "semaforo" in combined or "semáforo" in combined))
        or ("no respetar el conductor de un vehiculo la luz roja" in combined and ("semaforo" in combined or "semáforo" in combined))
    )

    velocity_context = (
        not semaforo_context
        and ("km/h" in combined)
        and any(
            s in combined
            for s in [
                "velocidad",
                "radar",
                "cinemometro",
                "cinemómetro",
                "exceso de velocidad",
                "limitada a",
                "siendo limitada la velocidad a",
                "teniendo limitada la velocidad a",
                "velocidad maxima",
                "velocidad máxima",
                "velocidad registrada",
                "velocidad fotografica",
                "velocidad fotográfica",
                "superar el limite de velocidad",
                "superar el límite de velocidad",
                "circular a",
                "circulaba a",
            ]
        )
    )

    if semaforo_false_friend_hit and not semaforo_legal_priority:
        semaforo_context = False

    if semaforo_legal_priority and not vehicle_light_context and not visibilidad_context:
        facts.append("NO RESPETAR LA LUZ ROJA (SEMÁFORO)")
        return ("semaforo", facts[0], facts)

    if semaforo_context and not semaforo_false_friend_hit and not velocity_context and not vehicle_light_context and not visibilidad_context:
        facts.append("NO RESPETAR LA LUZ ROJA (SEMÁFORO)")
        return ("semaforo", facts[0], facts)

    if movil_context and not semaforo_context:
        facts.append("USO MANUAL DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    # -------------------------------------------------
    # 7) VELOCIDAD
    # -------------------------------------------------
    if velocity_context:
        facts.append("EXCESO DE VELOCIDAD")
        return ("velocidad", facts[0], facts)

    # -------------------------------------------------
    # 8) SEGURO
    # -------------------------------------------------
    seguro_context = (
        ("lsoa" in combined)
        or (("r.d. legislativo" in combined or "rd legislativo" in combined) and "8/2004" in combined)
        or ("8/2004" in combined and "responsabilidad civil" in combined)
        or any(
            s in combined
            for s in [
                "seguro obligatorio",
                "sin seguro",
                "vehiculo no asegurado",
                "vehículo no asegurado",
                "vehiculo sin asegurar",
                "vehículo sin asegurar",
                "sin asegurar",
                "sin tener asegurado",
                "vehiculo carece de seguro",
                "vehículo carece de seguro",
                "poliza de seguro",
                "póliza de seguro",
                "cobertura de seguro",
                "aseguramiento obligatorio",
                "fiva",
                "responsabilidad civil derivada de su circulacion",
                "responsabilidad civil derivada de su circulación",
            ]
        )
    )

    if seguro_context:
        facts.append("CARENCIA DE SEGURO OBLIGATORIO")
        return ("seguro", facts[0], facts)

    # -------------------------------------------------
    # 9) ITV
    # -------------------------------------------------
    itv_context = any(
        s in hecho_focus
        for s in [
            "itv",
            "inspeccion tecnica",
            "inspección técnica",
            "inspeccion tecnica de vehiculos",
            "inspección técnica de vehículos",
            "itv caducada",
            "caducidad de itv",
        ]
    )

    if itv_context:
        facts.append("ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA")
        return ("itv", facts[0], facts)

    # -------------------------------------------------
    # 10) MARCAS VIALES
    # -------------------------------------------------
    marcas_context = any(
        s in combined
        for s in [
            "linea continua",
            "línea continua",
            "marca longitudinal continua",
            "marca vial",
            "senalizacion horizontal",
            "señalización horizontal",
            "no respetar una marca longitudinal continua",
            "adelantamiento",
            "articulo 167",
            "artículo 167",
            "art. 167",
        ]
    )

    if marcas_context:
        facts.append("NO RESPETAR MARCA VIAL")
        return ("marcas_viales", facts[0], facts)

    # -------------------------------------------------
    # 11) CARRIL / POSICIÓN EN VÍA / ADELANTAMIENTO
    # -------------------------------------------------
    carril_context = any(
        s in combined
        for s in [
            "carril distinto del situado mas a la derecha",
            "carril distinto del situado más a la derecha",
            "carril mas a la derecha",
            "carril más a la derecha",
            "no ocupar el carril mas a la derecha",
            "no ocupar el carril más a la derecha",
            "no circular por el carril mas a la derecha",
            "no circular por el carril más a la derecha",
            "no utilizar el carril mas a la derecha disponible",
            "no utilizar el carril más a la derecha disponible",
            "posicion en la via",
            "posición en la vía",
            "posicion a la derecha",
            "posición a la derecha",
            "posicion correcta en calzada",
            "posición correcta en calzada",
            "fuera de posicion correcta en calzada",
            "fuera de posición correcta en calzada",
            "articulo 31",
            "artículo 31",
            "art. 31",
            "adelantar por la derecha",
            "adelantar a un vehiculo por la derecha",
            "adelantar a un vehículo por la derecha",
            "por parte del arcen",
            "por parte del arcén",
            "carril derecho libre",
        ]
    )

    if carril_context:
        facts.append("POSICIÓN INCORRECTA EN LA VÍA / USO INDEBIDO DEL CARRIL")
        return ("carril", facts[0], facts)

    # -------------------------------------------------
    # 12) ATENCIÓN / CONDUCCIÓN NEGLIGENTE
    # -------------------------------------------------
    atencion_context = any(
        s in combined
        for s in [
            "no mantener la atencion",
            "no mantener la atención",
            "atencion permanente",
            "atención permanente",
            "conduccion negligente",
            "conducción negligente",
            "distraccion",
            "distracción",
            "bail",
            "palmas",
            "tocando las palmas",
            "tocar las palmas",
            "golpeando el volante",
            "golpear el volante",
            "volante",
            "tambor",
            "menor",
            "bebe",
            "bebé",
            "intercept",
            "mordia las unas",
            "mordía las uñas",
            "libertad de movimientos",
            "ciclistas",
            "circular de a tres",
            "conversando con ellos",
            "conversacion",
            "conversación",
            "mirando en repetidas ocasiones",
            "diligencia",
            "precaucion",
            "precaución",
            "no distraccion",
            "no distracción",
        ]
    )

    if atencion_context:
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    return ("otro", "", [])

def _score_infraction_families(text_blob: str, core: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    core = core or {}
    combined = _normalize_for_matching(
        "\n".join([
            _safe_str(text_blob),
            _safe_str(core.get("hecho_denunciado_literal")),
            _safe_str(core.get("hecho_denunciado_resumido")),
            _safe_str(core.get("organismo")),
            _safe_str(core.get("tipo_sancion")),
            _safe_str(core.get("norma_hint")),
            _safe_str(core.get("raw_text_blob")),
        ])
    )

    scores: Dict[str, int] = {
        "condiciones_vehiculo": 0,
        "casco": 0,
        "auriculares": 0,
        "cinturon": 0,
        "movil": 0,
        "semaforo": 0,
        "velocidad": 0,
        "seguro": 0,
        "itv": 0,
        "marcas_viales": 0,
        "carril": 0,
        "atencion": 0,
    }

    def add(tipo: str, signal: str, points: int) -> None:
        if signal in combined:
            scores[tipo] += points

    # Velocidad
    for s, pts in [
        ("km/h", 5),
        ("velocidad", 3),
        ("limitada la velocidad a", 4),
        ("teniendo limitada la velocidad a", 4),
        ("radar", 4),
        ("cinemometro", 5),
        ("cinemómetro", 5),
        ("multanova", 4),
        ("velocidad fotografica", 3),
        ("velocidad fotográfica", 3),
        ("exceso de velocidad", 5),
    ]:
        add("velocidad", s, pts)

    # Cinturón
    for s, pts in [
        ("cinturon de seguridad", 6),
        ("cinturón de seguridad", 6),
        ("sin cinturon", 5),
        ("sin cinturón", 5),
        ("no utilizar el cinturon", 6),
        ("no utilizar el cinturón", 6),
        ("no llevar abrochado el cinturon", 5),
        ("no llevar abrochado el cinturón", 5),
        ("correctamente abrochado", 5),
        ("sistema de retencion", 2),
    ]:
        add("cinturon", s, pts)

    # Móvil
    for s, pts in [
        ("telefono movil", 6),
        ("teléfono móvil", 6),
        ("uso manual", 4),
        ("manipulando el movil", 5),
        ("manipulando el móvil", 5),
        ("sujetando con la mano el dispositivo", 5),
        ("interactuando con la pantalla", 5),
    ]:
        add("movil", s, pts)

    # Auriculares
    for s, pts in [
        ("auricular", 6),
        ("auriculares", 6),
        ("cascos conectados", 5),
        ("cascos o auriculares", 5),
        ("reproductores de sonido", 4),
        ("porta auricular", 3),
        ("bluetooth instalado en casco", 3),
    ]:
        add("auriculares", s, pts)

    # Casco
    for s, pts in [
        ("sin casco", 6),
        ("no llevar casco", 6),
        ("no utilizar casco", 6),
        ("no hacer uso del casco", 7),
        ("sin hacer uso del casco", 7),
        ("casco obligatorio", 6),
        ("casco reglamentario", 5),
        ("casco de proteccion", 5),
        ("casco de protección", 5),
        ("casco homologado", 4),
        ("debidamente abrochado", 2),
        ("ciclomotor sin casco", 5),
        ("motociclista sin casco", 5),
    ]:
        add("casco", s, pts)

    # Semáforo
    for s, pts in [
        ("semaforo", 6),
        ("semáforo", 6),
        ("fase roja", 6),
        ("fase del rojo", 8),
        ("luz roja", 7),
        ("luz roja no intermitente", 10),
        ("cruce con fase del rojo", 10),
        ("cruce en rojo", 5),
        ("linea de detencion", 4),
        ("línea de detención", 4),
        ("paso en rojo", 5),
        ("articulo 146", 10),
        ("art. 146", 10),
    ]:
        add("semaforo", s, pts)

    # Seguro
    for s, pts in [
        ("seguro obligatorio", 6),
        ("sin seguro", 6),
        ("vehiculo no asegurado", 6),
        ("vehículo no asegurado", 6),
        ("sin asegurar", 7),
        ("vehiculo sin asegurar", 8),
        ("vehículo sin asegurar", 8),
        ("circular sin asegurar", 8),
        ("con vehiculo sin asegurar", 8),
        ("con vehículo sin asegurar", 8),
        ("conducir vehiculo sin asegurar", 8),
        ("conducir vehículo sin asegurar", 8),
        ("sin tener asegurado", 7),
        ("poliza de seguro", 4),
        ("póliza de seguro", 4),
        ("cobertura de seguro", 4),
        ("aseguramiento obligatorio", 5),
        ("fiva", 4),
        ("8/2004", 4),
        ("responsabilidad civil", 3),
    ]:
        add("seguro", s, pts)

    # ITV
    for s, pts in [
        ("itv", 6),
        ("inspeccion tecnica", 5),
        ("inspección técnica", 5),
        ("itv caducada", 6),
        ("caducidad de itv", 6),
    ]:
        add("itv", s, pts)

    # Marcas viales
    for s, pts in [
        ("linea continua", 6),
        ("línea continua", 6),
        ("marca longitudinal continua", 5),
        ("marca vial", 4),
        ("senalizacion horizontal", 3),
        ("señalización horizontal", 3),
        ("articulo 167", 2),
        ("art. 167", 2),
    ]:
        add("marcas_viales", s, pts)

    # Carril
    for s, pts in [
        ("carril distinto del situado mas a la derecha", 6),
        ("carril distinto del situado más a la derecha", 6),
        ("carril mas a la derecha", 6),
        ("carril más a la derecha", 6),
        ("mas a la derecha posible", 6),
        ("más a la derecha posible", 6),
        ("no circular por el carril mas a la derecha", 7),
        ("no circular por el carril más a la derecha", 7),
        ("no ocupar el carril mas a la derecha", 7),
        ("no ocupar el carril más a la derecha", 7),
        ("posicion en la via", 4),
        ("posición en la vía", 4),
        ("posicion a la derecha", 4),
        ("posición a la derecha", 4),
        ("posicion correcta en calzada", 4),
        ("posición correcta en calzada", 4),
        ("fuera de posicion correcta en calzada", 4),
        ("fuera de posición correcta en calzada", 4),
        ("adelantar por la derecha", 5),
        ("por parte del arcen", 4),
        ("por parte del arcén", 4),
    ]:
        add("carril", s, pts)

    # Atención
    for s, pts in [
        ("atencion permanente", 5),
        ("atención permanente", 5),
        ("conduccion negligente", 6),
        ("conducción negligente", 6),
        ("distraccion", 5),
        ("distracción", 5),
        ("golpeando el volante", 3),
        ("mordía las uñas", 3),
        ("mordia las unas", 3),
        ("libertad de movimientos", 2),
    ]:
        add("atencion", s, pts)

    # Condiciones vehículo
    for s, pts in [
        ("alumbrado", 4),
        ("senalizacion optica", 4),
        ("señalizacion optica", 4),
        ("dispositivos de alumbrado", 4),
        ("condiciones reglamentarias", 5),
        ("homologacion", 3),
        ("homologación", 3),
        ("reflectante", 3),
        ("espejo", 2),
        ("superficie acristalada", 7),
        ("visibilidad diafana", 7),
        ("visibilidad diáfana", 7),
        ("visibilidad", 4),
        ("laminas", 6),
        ("láminas", 6),
        ("laminas adhesivas", 7),
        ("láminas adhesivas", 7),
        ("adhesivos", 6),
        ("cortinillas", 6),
        ("parabrisas", 4),
        ("panel rectangular", 5),
        ("luz azul", 6),
        ("luces azules", 6),
        ("dispositivos luminosos no autorizados", 7),
        ("luces no reglamentarias", 7),
        ("dispositivo trasero", 5),
        ("alumbrado trasero", 6),
        ("parte trasera", 5),
        ("posterior", 3),
        ("oscilante", 5),
        ("intermitente", 5),
        ("destellos", 5),
        ("señalizacion trasera", 5),
        ("señalización trasera", 5),
        ("no homologada", 4),
        ("no homologado", 4),
    ]:
        add("condiciones_vehiculo", s, pts)

    # Prioridad: luz roja trasera/destellos/alumbrado = vehículo, no semáforo
    if "luz roja" in combined and any(
        s in combined for s in [
            "trasera", "posterior", "parte trasera", "vehiculo", "vehículo",
            "alumbrado", "senalizacion optica", "señalizacion optica",
            "destellos", "intermitente", "oscilante", "dispositivo",
            "no homologada", "no homologado", "señalizacion trasera", "señalización trasera",
        ]
    ):
        scores["condiciones_vehiculo"] += 8
        scores["semaforo"] -= 8

    if any(s in combined for s in ["semaforo", "semáforo", "fase roja", "linea de detencion", "línea de detención", "interseccion", "intersección", "cruce"]):
        scores["semaforo"] += 2

    return scores


def _pick_best_infraction(scores: Dict[str, int]) -> Tuple[str, float]:
    if not scores:
        return "otro", 0.0
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0
    if best_score <= 0:
        return "otro", 0.0
    confidence = round(best_score / max(best_score + second, 1), 4)
    return best_type, confidence



def _validate_tipo_infraccion(tipo: str, hecho_focus: str) -> Tuple[str, float]:

    if not hecho_focus:
        return tipo, 0.5

    # SEMÁFORO
    if tipo == "semaforo":
        signals = [
            "semaforo",
            "fase roja",
            "fase del rojo",
            "luz roja",
            "luz roja no intermitente",
            "cruce en rojo",
            "cruce con fase del rojo",
            "linea de detencion",
            "articulo 146",
            "art. 146",
        ]
        if any(s in hecho_focus for s in signals):
            return "semaforo", 0.98
        return "otro", 0.30

    # ITV
    if tipo == "itv":
        signals = ["itv caducada", "caducidad de itv", "inspeccion tecnica"]
        if any(s in hecho_focus for s in signals):
            return "itv", 0.95
        return "otro", 0.20

    # MÓVIL
    if tipo == "movil":
        signals = ["telefono movil", "uso manual", "manipulando", "sujetando con la mano"]
        if any(s in hecho_focus for s in signals):
            return "movil", 0.95
        return "otro", 0.25

    # CINTURÓN
    if tipo == "cinturon":
        signals = ["cinturon", "cinturón"]
        if any(s in hecho_focus for s in signals):
            return "cinturon", 0.95
        return "otro", 0.25

    return tipo, 0.80


def _resolve_cinturon_subtype(text_blob: str, core: Optional[Dict[str, Any]] = None) -> str:
    core = core or {}
    combined = _normalize_for_matching(
        "\n".join([
            _safe_str(text_blob),
            _safe_str(core.get("hecho_denunciado_literal")),
            _safe_str(core.get("hecho_denunciado_resumido")),
        ])
    )
    if "correctamente abrochado" in combined and (
        "no utilizar" in combined
        or "no utiliza" in combined
        or "sin cinturon" in combined
        or "sin cinturón" in combined
    ):
        return "cinturon_redaccion_ambigua"
    if "colocacion incorrecta" in combined or "colocación incorrecta" in combined:
        return "cinturon_colocacion_incorrecta"
    if "mal abrochado" in combined or "no llevar abrochado" in combined:
        return "cinturon_mal_abrochado"
    if "sin cinturon" in combined or "sin cinturón" in combined or "no utilizar el cinturon" in combined or "no utilizar el cinturón" in combined:
        return "cinturon_no_uso_total"
    return "cinturon_generico"


def _detect_evidence_gaps(text_blob: str, core: Optional[Dict[str, Any]] = None, tipo: str = "") -> List[str]:
    core = core or {}
    blob = _normalize_for_matching(
        "\n".join([
            _safe_str(text_blob),
            _safe_str(core.get("raw_text_blob")),
            _safe_str(core.get("hecho_denunciado_literal")),
        ])
    )
    gaps: List[str] = []

    if tipo in ("cinturon", "movil", "auriculares", "casco", "atencion", "semaforo"):
        if not any(s in blob for s in ["foto", "fotografia", "fotografía", "video", "vídeo", "fotograma", "secuencia"]):
            gaps.append("no_prueba_objetiva")
        if not any(s in blob for s in ["distancia", "metros", "m "]):
            gaps.append("distancia_no_acreditada")
        if not any(s in blob for s in ["posicion del agente", "posición del agente", "desde el punto", "ubicado", "situado"]):
            gaps.append("posicion_agente_no_acreditada")
        if not any(s in blob for s in ["segundos", "durante", "instantes", "tiempo de observacion", "tiempo de observación"]):
            gaps.append("duracion_observacion_no_acreditada")
        if not any(s in blob for s in ["visibilidad", "iluminacion", "iluminación", "campo visual"]):
            gaps.append("visibilidad_no_acreditada")

    if tipo == "velocidad":
        if not any(s in blob for s in ["certificado de verificacion", "certificado de verificación", "control metrologico", "control metrológico"]):
            gaps.append("metrologia_no_acreditada")
        if not any(s in blob for s in ["fotograma", "captura", "imagen", "fotografia", "fotografía"]):
            gaps.append("fotograma_no_aportado")
        if not any(s in blob for s in ["margen", "velocidad corregida"]):
            gaps.append("margen_no_explicitado")

    if tipo == "cinturon":
        if not any(s in blob for s in ["ausencia total", "mal abrochado", "correctamente abrochado", "colocacion incorrecta", "colocación incorrecta"]):
            gaps.append("concrecion_missing")

    return gaps


def _infer_recurso_strategy(tipo: str, subtipo: str, evidence_gaps: List[str]) -> List[str]:
    strategy: List[str] = []
    if evidence_gaps:
        strategy.append("insuficiencia_probatoria")
    if tipo == "velocidad":
        strategy.append("prueba_tecnica_radar")
    if tipo in ("cinturon", "movil", "auriculares", "casco", "atencion"):
        strategy.append("observacion_agente")
    if subtipo == "cinturon_redaccion_ambigua":
        strategy.append("ambiguedad_hecho")
        strategy.append("falta_concrecion")
    if "concrecion_missing" in evidence_gaps and "falta_concrecion" not in strategy:
        strategy.append("falta_concrecion")
    if "solicitud_expediente_integro" not in strategy:
        strategy.append("solicitud_expediente_integro")
    return strategy


def _score_attack_routes(tipo: str, subtipo: str, evidence_gaps: List[str], core: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    core = core or {}
    routes: Dict[str, int] = {}

    def add(route: str, points: int) -> None:
        routes[route] = routes.get(route, 0) + points

    gap_count = len(evidence_gaps or [])
    if gap_count:
        add("insuficiencia_probatoria", min(95, 45 + gap_count * 10))
        add("solicitud_expediente_integro", min(70, 20 + gap_count * 8))

    if tipo == "velocidad":
        add("prueba_tecnica_radar", 70)
        if "metrologia_no_acreditada" in evidence_gaps:
            add("prueba_tecnica_radar", 15)
        if "fotograma_no_aportado" in evidence_gaps:
            add("prueba_tecnica_radar", 10)
        if "margen_no_explicitado" in evidence_gaps:
            add("defecto_motivacion", 15)

    if tipo in ("cinturon", "movil", "auriculares", "casco", "atencion"):
        add("observacion_agente", 55)
        if "no_prueba_objetiva" in evidence_gaps:
            add("observacion_agente", 15)

    if tipo == "semaforo":
        add("secuencia_y_sincronizacion", 55)
        if "no_prueba_objetiva" in evidence_gaps:
            add("secuencia_y_sincronizacion", 15)

    if subtipo == "cinturon_redaccion_ambigua":
        add("ambiguedad_hecho", 88)
        add("falta_concrecion", 80)

    if "concrecion_missing" in evidence_gaps:
        add("falta_concrecion", 70)

    if "margen_no_explicitado" in evidence_gaps or "fotograma_no_aportado" in evidence_gaps:
        add("defecto_motivacion", 45)

    ordered = sorted(
        [{"route": k, "score": int(v)} for k, v in routes.items() if v > 0],
        key=lambda x: x["score"],
        reverse=True,
    )
    return ordered


def _infer_expediente_strength(evidence_gaps: List[str], tipo: str = "") -> str:
    gap_count = len(evidence_gaps or [])
    if tipo == "velocidad":
        if gap_count >= 3:
            return "debil"
        if gap_count >= 1:
            return "medio"
        return "fuerte"

    if gap_count >= 4:
        return "debil"
    if gap_count >= 2:
        return "medio"
    return "fuerte"


def _infer_recommended_tone(expediente_strength: str, attack_routes: List[Dict[str, Any]]) -> str:
    primary = attack_routes[0]["route"] if attack_routes else ""
    if expediente_strength == "debil":
        return "agresivo"
    if primary in ("prueba_tecnica_radar", "secuencia_y_sincronizacion"):
        return "tecnico"
    if expediente_strength == "medio":
        return "tecnico"
    return "prudente"




def _infer_operational_strategy(case_viability: str, critical_errors: List[str], evidence_gaps: List[str]) -> Dict[str, Any]:
    critical_count = len(critical_errors or [])
    gap_count = len(evidence_gaps or [])

    if case_viability == "alta" and critical_count >= 1:
        return {
            "resultado_estrategico": "archivo_probable",
            "motivo_estrategico": "Se detectan errores críticos del expediente y una base defensiva fuerte para solicitar archivo.",
            "presentacion_automatica_recomendada": True,
        }

    if case_viability in ("alta", "media") and gap_count >= 2:
        return {
            "resultado_estrategico": "defensa_viable",
            "motivo_estrategico": "Existen carencias probatorias o documentales suficientes para sostener una defensa sólida, aunque no necesariamente determinante de archivo inmediato.",
            "presentacion_automatica_recomendada": True,
        }

    return {
        "resultado_estrategico": "revision_manual_recomendada",
        "motivo_estrategico": "El expediente no presenta por ahora una debilidad bastante clara; conviene revisión manual antes de presentación automática.",
        "presentacion_automatica_recomendada": False,
    }

def _infer_modelo_defensa(tipo: str, subtipo: str, expediente_errors: List[str], critical_errors: List[str], attack_routes: List[Dict[str, Any]]) -> str:
    gap_set = set(expediente_errors or [])
    critical_set = set(critical_errors or [])
    primary = attack_routes[0]["route"] if attack_routes else ""

    if tipo == "velocidad":
        if "metrologia_no_acreditada" in critical_set:
            return "metrologia_radar_no_acreditada"
        if "fotograma_no_aportado" in critical_set:
            return "fotograma_no_aportado"
        if "margen_no_acreditado" in critical_set:
            return "margen_no_acreditado"
        return "prueba_tecnica_insuficiente"

    if tipo == "semaforo":
        if "fase_roja_no_acreditada" in critical_set:
            return "fase_roja_no_acreditada"
        if "secuencia_no_aportada" in critical_set:
            return "secuencia_no_aportada"
        return "prueba_semaforo_insuficiente"

    if tipo == "cinturon":
        if "hecho_ambiguo" in critical_set:
            return "hecho_ambiguo_cinturon"
        if "sin_prueba_objetiva" in gap_set:
            return "observacion_agente_sin_soporte"
        return "falta_concrecion_cinturon"

    if tipo == "movil":
        if primary == "observacion_agente":
            return "uso_manual_no_acreditado"
        return "falta_prueba_objetiva_movil"

    if tipo == "auriculares":
        return "uso_auriculares_no_acreditado"

    if tipo == "atencion":
        if "posicion_agente_no_acreditada" in gap_set:
            return "observacion_insuficiente"
        return "conduccion_negligente_no_concretada"

    if tipo == "itv":
        return "itv_no_acreditada"

    if tipo == "seguro":
        return "seguro_no_acreditado"

    if tipo == "marcas_viales":
        return "marca_vial_no_acreditada"

    if tipo == "carril":
        return "maniobra_no_acreditada"

    if tipo == "casco":
        return "no_uso_casco_no_acreditado"

    return "defensa_general_sancionador"

HECHO_CANONICO = {
    "velocidad": "EXCESO DE VELOCIDAD",
    "movil": "USO MANUAL DEL TELÉFONO MÓVIL",
    "auriculares": "USO DE AURICULARES O CASCOS CONECTADOS",
    "cinturon": "NO UTILIZAR CINTURÓN DE SEGURIDAD",
    "semaforo": "NO RESPETAR LA LUZ ROJA (SEMÁFORO)",
    "marcas_viales": "NO RESPETAR MARCA VIAL",
    "casco": "NO UTILIZAR CASCO DE PROTECCIÓN",
    "seguro": "CARENCIA DE SEGURO OBLIGATORIO",
    "itv": "ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA",
    "condiciones_vehiculo": "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO",
    "carril": "POSICIÓN INCORRECTA EN LA VÍA / USO INDEBIDO DEL CARRIL",
    "atencion": "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN",
}


def _canonical_hecho_imputado(tipo_infraccion: str, hecho_actual: str = "") -> str:
    tipo = (tipo_infraccion or "").strip().lower()
    canon = HECHO_CANONICO.get(tipo)
    if canon:
        return canon
    return (hecho_actual or "").strip()


def _build_legal_strategy(out: Dict[str, Any], tipo: str, subtipo: str, evidence_gaps: List[str], attack_routes: List[Dict[str, Any]], confidence: float) -> Dict[str, Any]:
    """
    Motor estratégico único para producción.
    No sustituye la clasificación; la convierte en una salida jurídica accionable.
    """
    tipo = (tipo or "otro").strip().lower()
    subtipo = (subtipo or "").strip().lower()
    gap_set = set(evidence_gaps or [])
    primary_route = attack_routes[0]["route"] if attack_routes else None

    blocks_main: List[str] = []
    blocks_secondary: List[str] = []
    motivo_nulidad: List[str] = []
    usar_nulidad = False

    if tipo == "semaforo":
        blocks_main = [
            "insuficiencia_probatoria",
            "fase_roja_no_acreditada",
            "secuencia_incompleta",
            "falta_motivacion",
        ]
        if "no_prueba_objetiva" in gap_set:
            usar_nulidad = True
            motivo_nulidad.extend(["ausencia_prueba_objetiva", "falta_procedimiento_esencial"])
        if "posicion_agente_no_acreditada" in gap_set:
            blocks_secondary.append("posicion_agente_no_acreditada")
        if "visibilidad_no_acreditada" in gap_set:
            blocks_secondary.append("visibilidad_no_acreditada")

    elif tipo == "velocidad":
        blocks_main = [
            "metrologia_no_acreditada",
            "fotograma_no_aportado",
            "margen_no_aplicado",
            "cadena_custodia_no_acreditada",
        ]
        if "metrologia_no_acreditada" in gap_set:
            usar_nulidad = True
            motivo_nulidad.append("soporte_tecnico_esencial_ausente")
        if "margen_no_explicitado" in gap_set:
            blocks_secondary.append("margen_no_explicitado")

    elif tipo == "movil":
        blocks_main = [
            "insuficiencia_probatoria",
            "observacion_subjetiva",
            "falta_concrecion",
        ]
        if "duracion_observacion_no_acreditada" in gap_set:
            blocks_secondary.append("duracion_observacion_no_acreditada")

    elif tipo == "auriculares":
        blocks_main = [
            "uso_no_acreditado",
            "observacion_subjetiva",
            "falta_concrecion",
        ]

    elif tipo == "cinturon":
        blocks_main = [
            "hecho_ambiguo",
            "falta_concrecion",
            "insuficiencia_probatoria",
        ]
        if subtipo == "cinturon_redaccion_ambigua":
            blocks_secondary.append("redaccion_ambigua")

    elif tipo == "casco":
        blocks_main = [
            "falta_concrecion",
            "insuficiencia_probatoria",
        ]

    elif tipo == "condiciones_vehiculo":
        blocks_main = [
            "falta_precision_tecnica",
            "norma_no_identificada",
            "prueba_insuficiente",
        ]

    elif tipo == "marcas_viales":
        blocks_main = [
            "maniobra_no_acreditada",
            "falta_motivacion",
        ]

    elif tipo == "carril":
        blocks_main = [
            "contexto_via_no_acreditado",
            "maniobra_no_acreditada",
        ]

    elif tipo == "seguro":
        blocks_main = [
            "consulta_no_acreditada",
            "falta_trazabilidad",
        ]

    elif tipo == "itv":
        blocks_main = [
            "situacion_administrativa_no_acreditada",
            "falta_documentacion",
        ]

    elif tipo == "atencion":
        blocks_main = [
            "ausencia_riesgo_vial",
            "descripcion_ambigua",
            "tipicidad_debil",
        ]
        if "posicion_agente_no_acreditada" in gap_set:
            blocks_secondary.append("posicion_agente_no_acreditada")

    else:
        blocks_main = ["insuficiencia_probatoria", "falta_motivacion"]

    # Refuerzo transversal
    if "no_prueba_objetiva" in gap_set and "insuficiencia_probatoria" not in blocks_main:
        blocks_secondary.append("insuficiencia_probatoria")
    if "concrecion_missing" in gap_set and "falta_concrecion" not in blocks_main:
        blocks_secondary.append("falta_concrecion")

    gap_score = len(gap_set)
    if usar_nulidad:
        estrategia = "archivo_probable"
    elif gap_score >= 4:
        estrategia = "defensa_viable_reforzada"
    elif gap_score >= 2:
        estrategia = "defensa_viable"
    else:
        estrategia = "defensa_basica"

    if usar_nulidad or gap_score >= 4:
        nivel = "muy_agresivo"
    elif gap_score >= 2:
        nivel = "agresivo"
    elif primary_route in ("prueba_tecnica_radar", "secuencia_y_sincronizacion"):
        nivel = "tecnico"
    else:
        nivel = "prudente"

    seen = set()
    blocks_main = [b for b in blocks_main if b and not (b in seen or seen.add(b))]
    seen2 = set(blocks_main)
    blocks_secondary = [b for b in blocks_secondary if b and b not in seen2 and not (b in seen or seen.add(b))]

    return {
        "tipo": tipo,
        "estrategia": estrategia,
        "nivel": nivel,
        "confianza": confidence,
        "bloques_principales": blocks_main,
        "bloques_secundarios": blocks_secondary,
        "usar_nulidad": usar_nulidad,
        "motivo_nulidad": motivo_nulidad,
        "ruta_primaria": primary_route,
    }


def _enrich_with_triage(extracted_core: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    out = dict(extracted_core or {})

    hecho_fields = _extract_preferred_hecho_fields(text_blob, out)
    for k, v in hecho_fields.items():
        if v:
            out[k] = v

    out = _apply_hecho_engine(out)

    tipo, hecho, facts = _detect_facts_and_type(text_blob, out)
    score_map = _score_infraction_families(text_blob, out)
    best_tipo, confidence = _pick_best_infraction(score_map)

    hecho_focus = _normalize_for_matching(
        "\n".join([
            _safe_str(out.get("hecho_denunciado_literal")),
            _safe_str(out.get("hecho_denunciado_resumido")),
        ])
    )

    if tipo in ("otro", "", None) and best_tipo not in ("", "otro"):
        tipo = best_tipo

    tipo_validado, conf_override = _validate_tipo_infraccion(tipo, hecho_focus)

    if tipo_validado != "otro":
        tipo = tipo_validado
        confidence = max(confidence, conf_override)

    out["tipo_infraccion"] = tipo

    hecho_imputado_textual = _safe_str(out.get("hecho_imputado_textual"))
    hecho_denunciado_literal = _safe_str(out.get("hecho_denunciado_literal"))
    hecho_detectado = _safe_str(hecho)

    out["hecho_imputado"] = (
        hecho_imputado_textual
        or hecho_denunciado_literal
        or hecho_detectado
        or _canonical_hecho_imputado(tipo, hecho)
        or None
    )
    out["facts_phrases"] = facts
    out["jurisdiccion"] = _extract_jurisdiction(text_blob, out)
    out["contexto_movilidad"] = _detect_mobility_context(text_blob, out)
    out["tipo_infraccion_scores"] = score_map
    out["tipo_infraccion_confidence"] = confidence

    subtipo = ""
    if tipo == "cinturon":
        subtipo = _resolve_cinturon_subtype(text_blob, out)
    out["subtipo_infraccion"] = subtipo or None

    evidence_gaps = _detect_evidence_gaps(text_blob, out, tipo=tipo)
    out["evidence_gaps"] = evidence_gaps
    out["recurso_strategy"] = _infer_recurso_strategy(tipo, subtipo or "", evidence_gaps)

    attack_routes = _score_attack_routes(tipo, subtipo or "", evidence_gaps, out)
    out["attack_routes"] = attack_routes
    out["primary_attack_route"] = attack_routes[0]["route"] if attack_routes else None
    out["expediente_strength"] = _infer_expediente_strength(evidence_gaps, tipo=tipo)
    out["recommended_tone"] = _infer_recommended_tone(out["expediente_strength"], attack_routes)

    # ---- FASE 5: diagnóstico jurídico del expediente ----
    expediente_errors = []
    critical_errors = []

    gap_set = set(evidence_gaps or [])

    if tipo == "velocidad":
        if "metrologia_no_acreditada" in gap_set:
            expediente_errors.append("metrologia_no_acreditada")
            critical_errors.append("metrologia_no_acreditada")

        if "fotograma_no_aportado" in gap_set:
            expediente_errors.append("fotograma_no_aportado")
            critical_errors.append("fotograma_no_aportado")

        if "margen_no_explicitado" in gap_set:
            expediente_errors.append("margen_no_acreditado")
            critical_errors.append("margen_no_acreditado")

    elif tipo == "semaforo":
        expediente_errors.extend([
            "fase_roja_no_acreditada",
            "rebase_linea_no_acreditado",
            "secuencia_no_aportada"
        ])

        critical_errors.extend([
            "fase_roja_no_acreditada",
            "secuencia_no_aportada"
        ])

    elif tipo == "cinturon":
        if "concrecion_missing" in gap_set:
            expediente_errors.append("hecho_ambiguo")
            critical_errors.append("hecho_ambiguo")

        if "no_prueba_objetiva" in gap_set:
            expediente_errors.append("sin_prueba_objetiva")

    elif tipo == "atencion":
        if "posicion_agente_no_acreditada" in gap_set:
            expediente_errors.append("posicion_agente_no_acreditada")

        if "duracion_observacion_no_acreditada" in gap_set:
            expediente_errors.append("duracion_observacion_no_acreditada")

    error_score = min(len(expediente_errors) * 8 + len(critical_errors) * 18, 100)

    if error_score >= 75:
        case_viability = "alta"
    elif error_score >= 45:
        case_viability = "media"
    else:
        case_viability = "baja"

    out["expediente_errors"] = expediente_errors
    out["critical_errors"] = critical_errors
    out["error_score"] = error_score
    out["case_viability"] = case_viability

    strategy_meta = _infer_operational_strategy(
        case_viability=case_viability,
        critical_errors=critical_errors,
        evidence_gaps=evidence_gaps,
    )
    out["resultado_estrategico"] = strategy_meta["resultado_estrategico"]
    out["motivo_estrategico"] = strategy_meta["motivo_estrategico"]
    out["presentacion_automatica_recomendada"] = strategy_meta["presentacion_automatica_recomendada"]

    out["modelo_defensa"] = _infer_modelo_defensa(
        tipo,
        subtipo or "",
        out.get("expediente_errors") or [],
        out.get("critical_errors") or [],
        attack_routes,
    )

    pre = _extract_precepts(text_blob)
    out["preceptos_detectados"] = pre.get("preceptos_detectados") or []
    out["articulo_infringido_num"] = pre.get("articulo_num")
    out["apartado_infringido_num"] = pre.get("apartado_num")

    if not out.get("norma_hint"):
        out["norma_hint"] = pre.get("norma_hint")
    else:
        out["norma_hint"] = out.get("norma_hint")

    extra_fields = _extract_speed_and_sanction_fields(text_blob)
    for k, v in (extra_fields or {}).items():
        if v is not None:
            out[k] = v

    literal = _safe_str(out.get("hecho_denunciado_literal"))
    if literal and not out.get("hecho_denunciado_resumido"):
        out["hecho_denunciado_resumido"] = _build_hecho_denunciado_resumido(
            literal,
            out.get("tipo_infraccion") or ""
        )

    # Fuente única de verdad para producción
    out["familia_resuelta"] = tipo
    out["template_usado"] = tipo
    out["hecho_para_recurso"] = _canonical_hecho_imputado(tipo, out.get("hecho_imputado") or hecho) or out.get("hecho_imputado")
    out["estrategia_legal"] = _build_legal_strategy(
        out,
        tipo,
        subtipo or "",
        evidence_gaps,
        attack_routes,
        confidence,
    )

    # Revisión final de calidad del hecho y coherencia con la familia.
    out = _apply_hecho_engine(out)
    if out.get("needs_operator_review"):
        out["presentacion_automatica_recomendada"] = False
        out["resultado_estrategico"] = "revision_operador_recomendada"
        out["motivo_estrategico"] = "El hecho imputado no ha podido aislarse con suficiente limpieza o no es coherente con la familia detectada."

    return out

def _needs_speed_retry(core: Dict[str, Any]) -> bool:
    if not isinstance(core, dict):
        return True

    tipo = (core.get("tipo_infraccion") or "").lower().strip()
    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")

    if tipo == "velocidad":
        return not (isinstance(measured, (int, float)) and isinstance(limit, (int, float)))

    blob = json.dumps(core, ensure_ascii=False).lower()
    signals = any(s in blob for s in ["km/h", "cinemomet", "radar", "exceso de velocidad"])
    if signals:
        return not (isinstance(measured, (int, float)) and isinstance(limit, (int, float)))

    return False




def _looks_like_generated_rtm_document(text: str) -> bool:
    t = _normalize_for_matching(text)
    if not t:
        return False

    strong_markers = [
        "escrito de alegaciones",
        "fundamentos de derecho",
        "s u p l i c a",
        "suplica:",
        "otrosi digo",
        "otrosi digo",
        "extracto literal del boletin",
        "extracto literal del boletín",
        "referencia: expte",
        "identificacion expediente:",
        "identificación expediente:",
        "hecho imputado:",
        "alegacion primera",
        "alegación primera",
        "archivo del expediente",
        "insuficiencia probatoria",
        "motivacion individualizada",
        "motivación individualizada",
    ]

    hits = sum(1 for m in strong_markers if m in t)

    if hits >= 3:
        return True

    # Patrón muy típico de documento generado por RTM
    combo_a = ("escrito de alegaciones" in t and "fundamentos de derecho" in t)
    combo_b = ("suplica:" in t and "otrosi digo" in t)
    combo_c = ("extracto literal del boletin" in t and "hecho imputado:" in t)
    combo_d = ("referencia: expte" in t and "archivo del expediente" in t)

    return combo_a or combo_b or combo_c or combo_d


def _raise_if_generated_resource_text(text: str) -> None:
    if _looks_like_generated_rtm_document(text):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "documento_no_valido_para_analisis",
                "message": "El archivo parece ser un recurso o escrito ya generado, no una denuncia o boletin original.",
            },
        )


def _ensure_raw_fields(core: Dict[str, Any], text_content: str = "") -> Dict[str, Any]:
    out = dict(core or {})

    if text_content and not out.get("raw_text_pdf"):
        out["raw_text_pdf"] = text_content

    vision_raw = out.get("vision_raw_text")
    if isinstance(vision_raw, str) and vision_raw.strip() and not out.get("raw_text_vision"):
        out["raw_text_vision"] = vision_raw.strip()

    if not out.get("raw_text_blob"):
        out["raw_text_blob"] = _flatten_text(out, text_content=text_content)

    return out


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 12MB).")

        sha256 = _sha256_bytes(content)
        mime = file.content_type or (mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream")
        size_bytes = len(content)

        engine = get_engine()

        with engine.begin() as conn:
            case_id = conn.execute(
                text("INSERT INTO cases(status, created_at, updated_at) VALUES ('uploaded', NOW(), NOW()) RETURNING id")
            ).scalar()

            b2_bucket, b2_key = upload_original(str(case_id), content, file.filename, mime)

            conn.execute(
                text(
                    "INSERT INTO documents (case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at) "
                    "VALUES (:case_id, 'original', :b2_bucket, :b2_key, :sha256, :mime, :size_bytes, NOW())"
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "sha256": sha256,
                    "mime": mime,
                    "size_bytes": size_bytes,
                },
            )

            model_used = "mock"
            confidence = 0.1
            extracted_core: Dict[str, Any] = {}
            text_content = ""

            if mime.startswith("image/"):
                extracted_core = extract_from_image_bytes(content, mime, file.filename)
                extracted_core = _ensure_raw_fields(extracted_core, text_content="")
                model_used = "openai_vision"
                confidence = 0.7

            elif mime == "application/pdf":
                text_content = extract_text_from_pdf_bytes(content)
                _raise_if_generated_resource_text(text_content)

                extracted_text: Dict[str, Any] = {}
                extracted_vision: Dict[str, Any] = {}

                if has_enough_text(text_content):
                    extracted_text = extract_from_text(text_content) or {}
                    extracted_text = _ensure_raw_fields(extracted_text, text_content=text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    model_used = "openai_vision"
                    confidence = 0.6

                extracted_vision = extract_from_image_bytes(content, mime, file.filename) or {}
                extracted_vision = _ensure_raw_fields(extracted_vision, text_content="")

                blob_text = _flatten_text(extracted_text, text_content=text_content) if extracted_text else (text_content or "")
                triaged_text = _enrich_with_triage(extracted_text or {}, blob_text)

                blob_vision = _flatten_text(extracted_vision, text_content="")
                triaged_vision = _enrich_with_triage(extracted_vision or {}, blob_vision)

                extracted_core = _merge_extracted(triaged_text, triaged_vision)
                extracted_core = _ensure_raw_fields(extracted_core, text_content=text_content)

                if extracted_text and not _needs_speed_retry(extracted_core):
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    model_used = "openai_vision+text"
                    confidence = 0.75

            elif mime in DOCX_MIMES:
                text_content = extract_text_from_docx_bytes(content)
                _raise_if_generated_resource_text(text_content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content) or {}
                    extracted_core = _ensure_raw_fields(extracted_core, text_content=text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = {
                        "observaciones": "DOCX sin texto suficiente.",
                        "raw_text_pdf": text_content or "",
                    }

            else:
                extracted_core = {"observaciones": "Tipo de archivo no soportado."}

            blob = _flatten_text(extracted_core, text_content=text_content)
            extracted_core = _enrich_with_triage(extracted_core, blob)
            extracted_core = _ensure_raw_fields(extracted_core, text_content=text_content)

            wrapper = {
                "filename": file.filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage": {"bucket": b2_bucket, "key": b2_key},
                "extracted": extracted_core,
            }

            conn.execute(
                text(
                    "INSERT INTO extractions (case_id, extracted_json, confidence, model, created_at) "
                    "VALUES (:case_id, CAST(:json AS JSONB), :confidence, :model, NOW())"
                ),
                {
                    "case_id": case_id,
                    "json": json.dumps(wrapper, ensure_ascii=False),
                    "confidence": confidence,
                    "model": model_used,
                },
            )

            conn.execute(
                text(
                    "INSERT INTO events(case_id, type, payload, created_at) "
                    "VALUES (:case_id, 'analyze_ok', CAST(:payload AS JSONB), NOW())"
                ),
                {
                    "case_id": case_id,
                    "payload": json.dumps(
                        {
                            "model": model_used,
                            "confidence": confidence,
                            "tipo_infraccion": extracted_core.get("tipo_infraccion"),
                            "jurisdiccion": extracted_core.get("jurisdiccion"),
                        }
                    ),
                },
            )

            conn.execute(
                text("UPDATE cases SET status='analyzed', updated_at=NOW() WHERE id=:case_id"),
                {"case_id": case_id},
            )

        return {
            "ok": True,
            "message": "Análisis completo generado.",
            "case_id": str(case_id),
            "extracted": wrapper,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /analyze: {e}")
