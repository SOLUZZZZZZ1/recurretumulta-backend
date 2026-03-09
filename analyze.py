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
    "hecho imputado",
    "hecho infringido",
    "hecho infractor",
]

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


def _is_admin_line(line: str) -> bool:
    l = _normalize_for_matching(line)
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
        ]
        if any(s in low for s in admin_poison):
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

    return out.strip()

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
    for src in literal_sources:
        literal = _extract_hecho_denunciado_literal_from_text(src)
        if literal and len(literal) >= 20:
            break

    tipo_hint = _safe_str(core.get("tipo_infraccion"))
    resumido = _build_hecho_denunciado_resumido(literal, tipo_hint) if literal else ""

    return {
        "hecho_denunciado_literal": literal or None,
        "hecho_denunciado_resumido": resumido or None,
    }


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

    velocity_context = (
        ("radar" in t)
        or ("cinemometro" in t)
        or ("km/h" in t)
        or ("exceso de velocidad" in t)
        or bool(re.search(r"\bcircular\s+a\s+\d{2,3}\s*km\s*/?\s*h\b", t))
        or bool(re.search(r"\bcirculaba\s+a\s+\d{2,3}\s*km\s*/?\s*h\b", t))
        or bool(re.search(r"\bvelocidad\s+medida\b", t))
        or bool(re.search(r"\bvelocidad\s+maxima\b", t))
    )

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
    if velocity_context:
        mr = re.search(r"(multanova\s*[a-z0-9\-]*)", t)
        if mr:
            radar_model = mr.group(1).strip()
        elif "multaradar" in t:
            mr2 = re.search(r"(multaradar\s*[a-z0-9\-]*)", t)
            radar_model = mr2.group(1).strip() if mr2 else "multaradar"
        elif "cinem" in t:
            radar_model = "cinemometro (no especificado)"

    out = {
        "velocidad_medida_kmh": measured,
        "velocidad_limite_kmh": limit,
        "sancion_importe_eur": fine_eur,
        "puntos_detraccion": points,
        "radar_modelo_hint": radar_model,
    }

    if velocity_context and candidates_all:
        out["velocidad_kmh_candidatos"] = sorted(set(candidates_all))

    if velocity_context and conflict:
        out["velocidad_conflicto_detectado"] = True

    return out


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

    Orden anti-cruces:
      1) condiciones_vehiculo
      2) auriculares
      3) movil
      4) semaforo
      5) velocidad
      6) seguro
      7) itv
      8) marcas_viales
      9) carril
      10) atencion
      11) otro
    """
    core = core or {}
    facts: List[str] = []

    t = _normalize_for_matching(text_blob)
    hecho_literal = _normalize_for_matching(_safe_str(core.get("hecho_denunciado_literal")))
    hecho_resumido = _normalize_for_matching(_safe_str(core.get("hecho_denunciado_resumido")))
    organismo = _normalize_for_matching(_safe_str(core.get("organismo")))
    tipo_sancion = _normalize_for_matching(_safe_str(core.get("tipo_sancion")))

    combined = "
".join([x for x in [t, hecho_literal, hecho_resumido, organismo, tipo_sancion] if x]).strip()".join([x for x in [t, hecho_literal, hecho_resumido, organismo, tipo_sancion] if x]).strip()

    vehicle_light_context = any(
        s in combined
        for s in [
            "alumbrado",
            "senalizacion optica",
            "senalizacion",
            "luz trasera",
            "parte trasera",
            "destellos",
            "anexo i",
            "reglamentacion del anexo",
            "dispositivos de alumbrado",
            "dispositivos de senalizacion",
            "no cumplan las exigencias",
        ]
    )

    velocity_context = (
        ("km/h" in combined)
        and any(
            s in combined
            for s in [
                "velocidad",
                "radar",
                "cinemometro",
                "exceso de velocidad",
                "limitada a",
                "siendo limitada la velocidad a",
                "teniendo limitada la velocidad a",
                "velocidad maxima",
                "velocidad registrada",
                "velocidad fotografica",
                "superar el limite de velocidad",
                "circular a",
                "circulaba a",
            ]
        )
    )

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
            "oido derecho",
        ]
    )

    movil_context = any(
        s in combined
        for s in [
            "telefono movil",
            "uso manual del movil",
            "uso manual del telefono",
            "utilizando manualmente",
            "sujetando con la mano el dispositivo",
            "manipulando el movil",
            "interactuando con la pantalla",
        ]
    )

    semaforo_context = any(
        s in combined
        for s in [
            "semaforo",
            "fase roja",
            "luz roja del semaforo",
            "cruce con fase roja",
            "cruce en rojo",
            "senal luminosa roja",
            "linea de detencion",
            "rebase la linea de detencion",
            "rebasar la linea de detencion",
            "semaforo en rojo",
            "paso en rojo",
            "articulo 146",
            "art. 146",
        ]
    ) or (("roja" in combined and "cruce" in combined) or ("roja" in combined and "detencion" in combined))

    # 1) CONDICIONES DEL VEHÍCULO — antes que semáforo
    if vehicle_light_context:
        facts.append("INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO")
        return ("condiciones_vehiculo", facts[0], facts)

    # 2) AURICULARES — antes que móvil
    if auriculares_context:
        facts.append("USO DE AURICULARES O CASCOS CONECTADOS")
        return ("auriculares", facts[0], facts)

    # 3) MÓVIL
    if movil_context:
        facts.append("USO MANUAL DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    # 4) SEMÁFORO — nunca si hay contexto de velocidad o alumbrado
    if semaforo_context and not velocity_context and not vehicle_light_context:
        facts.append("NO RESPETAR LA LUZ ROJA (SEMÁFORO)")
        return ("semaforo", facts[0], facts)

    # 5) VELOCIDAD
    if velocity_context:
        facts.append("EXCESO DE VELOCIDAD")
        return ("velocidad", facts[0], facts)

    # 6) SEGURO
    if (
        ("lsoa" in combined)
        or (("r.d. legislativo" in combined or "rd legislativo" in combined) and "8/2004" in combined)
        or ("8/2004" in combined and "responsabilidad civil" in combined)
        or any(s in combined for s in ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehiculo carece de seguro"])
    ):
        facts.append("CARENCIA DE SEGURO OBLIGATORIO")
        return ("seguro", facts[0], facts)

    # 7) ITV
    if any(s in combined for s in ["itv", "inspeccion tecnica", "inspeccion tecnica de vehiculos", "itv caducada", "caducidad de itv"]):
        facts.append("ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA")
        return ("itv", facts[0], facts)

    # 8) MARCAS VIALES
    if any(
        s in combined
        for s in [
            "linea continua",
            "marca longitudinal continua",
            "marca vial",
            "senalizacion horizontal",
            "no respetar una marca longitudinal continua",
            "adelantamiento",
            "articulo 167",
            "art. 167",
        ]
    ):
        facts.append("NO RESPETAR MARCA VIAL")
        return ("marcas_viales", facts[0], facts)

    # 9) CARRIL / POSICIÓN EN VÍA
    if any(s in combined for s in ["carril distinto del situado mas a la derecha", "posicion en la via", "articulo 31", "art. 31"]):
        facts.append("POSICIÓN INCORRECTA EN LA VÍA / USO INDEBIDO DEL CARRIL")
        return ("carril", facts[0], facts)

    # 10) ATENCIÓN / CONDUCCIÓN NEGLIGENTE
    if any(
        s in combined
        for s in [
            "no mantener la atencion",
            "atencion permanente",
            "conduccion negligente",
            "distraccion",
            "bail",
            "palm",
            "golpe",
            "volante",
            "tambor",
            "menor",
            "bebe",
            "intercept",
            "mordia las unas",
            "libertad de movimientos",
            "ciclistas",
            "circular de a tres",
            "conversando con ellos",
        ]
    ):
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    return ("otro", "", [])




HECHO_CANONICO = {
    "velocidad": "EXCESO DE VELOCIDAD",
    "semaforo": "NO RESPETAR LA LUZ ROJA (SEMÁFORO)",
    "movil": "USO MANUAL DEL TELÉFONO MÓVIL",
    "auriculares": "USO DE AURICULARES O CASCOS CONECTADOS",
    "marcas_viales": "NO RESPETAR MARCA VIAL",
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


def _enrich_with_triage(extracted_core: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    out = dict(extracted_core or {})

    hecho_fields = _extract_preferred_hecho_fields(text_blob, out)
    for k, v in hecho_fields.items():
        if v:
            out[k] = v

    tipo, hecho, facts = _detect_facts_and_type(text_blob, out)
    out["tipo_infraccion"] = tipo
    out["hecho_imputado"] = _canonical_hecho_imputado(tipo, hecho) or None
    out["facts_phrases"] = facts
    out["jurisdiccion"] = _extract_jurisdiction(text_blob, out)

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
        out["hecho_denunciado_resumido"] = _build_hecho_denunciado_resumido(literal, out.get("tipo_infraccion") or "")

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
