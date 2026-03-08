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
    """
    Construye un blob textual amplio y útil para triage.
    Incluye tanto campos estructurados como OCR / literales.
    """
    parts: List[str] = []

    if isinstance(extracted_core, dict):
        preferred_keys = [
            "organismo",
            "expediente_ref",
            "tipo_sancion",
            "hecho_denunciado_literal",
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
                if isinstance(v, (str, int, float, bool)):
                    sv = _safe_str(v).strip()
                    if sv:
                        parts.append(f"{k}: {sv}")
                else:
                    try:
                        sv = str(v).strip()
                        if sv:
                            parts.append(f"{k}: {sv}")
                    except Exception:
                        pass
                used.add(k)

        for k, v in extracted_core.items():
            if k in used or v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                sv = _safe_str(v).strip()
                if sv:
                    parts.append(f"{k}: {sv}")
            else:
                try:
                    sv = str(v).strip()
                    if sv:
                        parts.append(f"{k}: {sv}")
                except Exception:
                    pass

    if text_content:
        parts.append(text_content)

    return "\n".join(parts)


def _merge_extracted(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Conserva valores no vacíos de primary y rellena con secondary.
    """
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
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n+", "\n", t)
    return t.strip()


ADMIN_FIELDS = [
    "importe multa",
    "importe con reduccion",
    "importe con reducción",
    "fecha limite",
    "fecha límite",
    "lugar de denuncia",
    "puntos a detraer",
    "matricula",
    "matrícula",
    "marca y modelo",
    "marca",
    "modelo",
    "clase vehiculo",
    "clase vehículo",
    "datos del vehic",
    "domicilio",
    "provincia",
    "codigo postal",
    "código postal",
    "identificacion de la multa",
    "identificación de la multa",
    "organo",
    "órgano",
    "expediente",
    "fecha documento",
    "hora",
    "via ",
    "vía ",
    "punto km",
    "sentido",
    "titular",
    "boletin",
    "boletín",
    "agente denunciante",
    "observaciones internas",
    "jefatura",
    "fecha caducidad documento",
    "lugar de pago",
    "referenciado cobro",
    "total principal",
    "bonificacion",
    "bonificación",
    "importe para ingresar",
    "motivo de no notificacion",
    "motivo de no notificación",
]

NARRATIVE_SIGNALS = [
    "conducir",
    "circular",
    "circulando",
    "circulaba",
    "no respetar",
    "no respeta",
    "utilizando",
    "bailando",
    "tocando",
    "golpeando",
    "auricular",
    "auriculares",
    "cascos",
    "luz roja",
    "fase roja",
    "marca longitudinal",
    "adelantamiento",
    "sin mantener",
    "atencion permanente",
    "atención permanente",
    "vehiculo resenado",
    "vehículo reseñado",
    "observado por agente",
    "interceptado",
    "interceptación",
    "interceptacion",
    "menor de",
    "ciclistas",
    "arcen",
    "arcén",
    "en paralelo",
    "conversando",
    "telefono movil",
    "teléfono móvil",
    "telefono",
    "teléfono",
    "movil",
    "móvil",
    "itv",
    "seguro obligatorio",
    "alumbrado",
    "senalizacion optica",
    "señalización óptica",
    "linea continua",
    "línea continua",
    "linea de detencion",
    "línea de detención",
    "uso manual",
    "radar",
    "cinemometro",
    "cinemómetro",
    "velocidad",
    "km/h",
    "cruce con fase roja",
    "semaforo en rojo",
    "semáforo en rojo",
    "cruce con luz roja",
]


def _clean_literal_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.strip()

    t = re.sub(r"^\s*hecho denunciado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*hecho imputado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*hecho que se notifica\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*hecho infringido\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*5[abc]\s*", "", t, flags=re.IGNORECASE)

    t = re.sub(r"\s+/\s+", " / ", t)
    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    return t


def _is_probably_admin_line(line: str) -> bool:
    l = _normalize_for_matching(line)
    return any(x in l for x in ADMIN_FIELDS)


def _looks_like_narrative_line(line: str) -> bool:
    l = _normalize_for_matching(line)
    return any(k in l for k in NARRATIVE_SIGNALS)


def _extract_literal_from_blob(raw_text: str) -> str:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ""

    original_text = raw_text.replace("\r", "\n")
    normalized_text = _normalize_for_matching(original_text)

    patterns = [
        r"hecho denunciado\s*[:\-]?\s*",
        r"hecho imputado\s*[:\-]?\s*",
        r"hecho que se notifica\s*[:\-]?\s*",
        r"hecho infringido\s*[:\-]?\s*",
    ]
    m = None
    for pat in patterns:
        m = re.search(pat, normalized_text, flags=re.IGNORECASE)
        if m:
            break

    if not m:
        return ""

    start = m.end()
    tail = original_text[start:].strip()
    if not tail:
        return ""

    lines = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    if not lines:
        return ""

    collected: List[str] = []
    started = False

    for ln in lines:
        low = _normalize_for_matching(ln)

        if _is_probably_admin_line(ln):
            if started:
                break
            continue

        if re.match(r"^\s*5[abc]\b", low):
            started = True
            cleaned = re.sub(r"^\s*5[abc]\s*", "", ln, flags=re.IGNORECASE).strip()
            if cleaned:
                collected.append(cleaned)
            continue

        if not started:
            if _looks_like_narrative_line(ln):
                started = True
                collected.append(ln)
            else:
                continue
        else:
            collected.append(ln)

        if len(" ".join(collected)) > 900:
            break

    if not collected:
        return ""

    out = _clean_literal_text(" / ".join(collected))

    if len(out) < 35:
        second_pass: List[str] = []
        for ln in lines:
            if _is_probably_admin_line(ln):
                if second_pass:
                    break
                continue
            second_pass.append(ln)
            if len(" ".join(second_pass)) > 900:
                break
        out2 = _clean_literal_text(" / ".join(second_pass))
        if len(out2) > len(out):
            out = out2

    if len(out) > 700:
        out = out[:700].rsplit(" ", 1)[0].strip() + "…"

    return out.strip()


def _build_hecho_resumido(literal: str, tipo: str = "", fallback: str = "") -> str:
    lit = _clean_literal_text(literal or "")
    if not lit:
        fb = _clean_literal_text(fallback or "")
        return fb[:280].strip() if fb else ""

    resumen = lit

    # limpiar basura residual frecuente
    stop_patterns = [
        r"\borganismo\s*:",
        r"\bexpediente_ref\s*:",
        r"\btipo_sancion\s*:",
        r"\bobservaciones\s*:",
        r"\bvision_raw_text\s*:",
        r"\braw_text_vision\s*:",
        r"\braw_text_blob\s*:",
        r"\btotal principal\s*:",
        r"\bimporte\s*:",
        r"\bfecha documento\s*:",
        r"\bfecha notificacion\s*:",
    ]
    lowered = _normalize_for_matching(resumen)
    cut_positions = []
    for pat in stop_patterns:
        m = re.search(pat, lowered, flags=re.IGNORECASE)
        if m:
            cut_positions.append(m.start())
    if cut_positions:
        resumen = resumen[: min(cut_positions)].strip(" .;,-")

    resumen = re.sub(r"^\s*5[abc]\s*", "", resumen, flags=re.IGNORECASE).strip()

    if len(resumen) <= 280:
        return resumen

    # recorte semántico suave
    pieces = [p.strip(" .;,-") for p in re.split(r"[./;]+", resumen) if p.strip()]
    if pieces:
        acc = ""
        for p in pieces:
            candidate = (acc + ". " + p).strip(". ").strip()
            if len(candidate) > 280:
                break
            acc = candidate
        if acc:
            return acc + "."

    return resumen[:280].rsplit(" ", 1)[0].strip() + "…"


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
        precepts.append("Reglamento General de Circulación")

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

    # Guard robusto: solo extraer velocidad si hay contexto real
    velocity_context = (
        ("radar" in t)
        or ("cinemometro" in t)
        or ("km/h" in t)
        or ("exceso de velocidad" in t)
        or bool(re.search(r"\bcircular\s+a\s+\d{2,3}\s*km\s*/?\s*h\b", t))
        or bool(re.search(r"\bvelocidad\s+medida\b", t))
        or bool(re.search(r"\bvelocidad\s+maxima\b", t))
    )

    measured = None
    limit = None
    conflict = False
    candidates_all: List[int] = []

    if velocity_context:
        for mm in re.finditer(r"\b(\d{2,3})\s*km\s*/?\s*h\b", t):
            try:
                candidates_all.append(int(mm.group(1)))
            except Exception:
                pass

        t_no_deadlines = re.sub(r"fecha\s*l[ií]mite[^\d]{0,40}\d{1,2}/\d{1,2}/\d{2,4}", "", t)

        limit_candidates: List[int] = []
        for mm in re.finditer(
            r"\b(?:limitad[ao]a?|limitada\s+la\s+velocidad|l[ií]mite|limite|velocidad\s+m[aá]xima|velocidad\s+maxima)\b[^\d]{0,80}(\d{2,3})\b",
            t_no_deadlines,
        ):
            try:
                limit_candidates.append(int(mm.group(1)))
            except Exception:
                pass

        for mm in re.finditer(r"\blimitad[ao]a?\s+a\s+(\d{2,3})\s*km\s*/?\s*h\b", t_no_deadlines):
            try:
                limit_candidates.append(int(mm.group(1)))
            except Exception:
                pass

        if limit_candidates:
            lc = [x for x in limit_candidates if 10 <= x <= 200]
            limit = sorted(set(lc))[0] if lc else min(limit_candidates)

        measured_candidates: List[int] = []
        for mm in re.finditer(r"\b(?:circular|circulaba|circulando)\s+a\s+(\d{2,3})\s*km\s*/?\s*h\b", t):
            try:
                measured_candidates.append(int(mm.group(1)))
            except Exception:
                pass

        if not measured_candidates and candidates_all:
            measured_candidates = candidates_all[:]

        measured_candidates = [x for x in measured_candidates if 10 <= x <= 250]
        candidates_all = [x for x in candidates_all if 10 <= x <= 250]

        if measured_candidates:
            if limit is not None:
                above = [x for x in measured_candidates if x >= (limit + 5)]
                measured = max(above) if above else max(measured_candidates)
            else:
                measured = max(measured_candidates)

        uniq = sorted(set(measured_candidates))
        if len(uniq) >= 2 and (max(uniq) - min(uniq)) >= 20:
            conflict = True
        if measured is not None and limit is not None and abs(measured - limit) <= 3:
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
        mr = re.search(r"(multaradar\s*[a-z0-9\-]*)", t)
        if mr:
            radar_model = mr.group(1).strip()
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
        "ajuntament de",
        "concejalia de trafico",
        "policia local",
        "guardia urbana",
    ]
    estatal_signals = [
        "dgt",
        "direccion general de trafico",
        "jefatura provincial de trafico",
        "trafico",
        "guardia civil",
    ]

    if any(s in blob for s in municipal_signals):
        return "municipal"

    if any(s in blob for s in estatal_signals):
        return "estatal"

    return "desconocida"


def _detect_facts_and_type(text_blob: str, core: Optional[Dict[str, Any]] = None) -> Tuple[str, str, List[str]]:
    """
    Devuelve (tipo_infraccion, hecho_imputado_canonico, facts_phrases)
    """
    core = core or {}
    t = _normalize_for_matching(text_blob)
    facts: List[str] = []

    hecho_literal = _normalize_for_matching(_safe_str(core.get("hecho_denunciado_literal")))
    organismo = _normalize_for_matching(_safe_str(core.get("organismo")))
    tipo_sancion = _normalize_for_matching(_safe_str(core.get("tipo_sancion")))

    combined = "\n".join([x for x in [t, hecho_literal, organismo, tipo_sancion] if x]).strip()

    # --------------------------
    # 1) SEMÁFORO — prioridad máxima real
    # --------------------------
    sema_signals = [
        "semaforo",
        "fase roja",
        "cruce en rojo",
        "cruce con fase roja",
        "luz roja del semaforo",
        "no respetar la luz roja",
        "no respeta la luz roja",
        "no respeta luz roja",
        "no respeta la fase roja",
        "no respetar la fase roja",
        "señal luminosa roja",
        "senal luminosa roja",
        "linea de detencion",
        "rebase la linea de detencion",
        "rebasar la linea de detencion",
        "ts roja",
        "t/s roja",
    ]

    if any(s in combined for s in sema_signals):
        facts.append("NO RESPETAR LA LUZ ROJA (SEMÁFORO)")
        return ("semaforo", facts[0], facts)

    if re.search(r"\bart\.?\s*146\b", combined) or re.search(r"\bart[ií]culo\s*146\b", combined) or re.search(r"\b146\s*[\.,]\s*1\b", combined):
        facts.append("NO RESPETAR LA LUZ ROJA (SEMÁFORO)")
        return ("semaforo", facts[0], facts)

    # --------------------------
    # 2) MÓVIL
    # --------------------------
    if "utilizando manualmente" in combined and any(k in combined for k in ["telefono", "movil"]):
        facts.append("USO MANUAL DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    if any(k in combined for k in ["telefono movil", "uso del telefono", "uso manual del movil", "uso manual del telefono"]):
        facts.append("USO DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    # --------------------------
    # 3) AURICULARES
    # --------------------------
    aur_signals = [
        "auricular",
        "auriculares",
        "cascos conectados",
        "cascos o auriculares",
        "reproductores de sonido",
        "aparatos receptores",
        "aparatos reproductores",
    ]
    if any(s in combined for s in aur_signals):
        facts.append("USO DE AURICULARES O CASCOS CONECTADOS")
        return ("auriculares", facts[0], facts)

    # --------------------------
    # 4) VELOCIDAD
    # --------------------------
    velocity_context = (
        ("km/h" in combined)
        and any(k in combined for k in ["velocidad", "radar", "cinemometro", "exceso de velocidad", "limitada a", "velocidad maxima"])
    )

    if velocity_context:
        facts.append("EXCESO DE VELOCIDAD")
        return ("velocidad", facts[0], facts)

    # --------------------------
    # 5) SEGURO
    # --------------------------
    if (
        ("lsoa" in combined)
        or (("r.d. legislativo" in combined or "rd legislativo" in combined) and "8/2004" in combined)
        or ("8/2004" in combined and "responsabilidad civil" in combined)
        or any(s in combined for s in ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehículo no asegurado"])
    ):
        facts.append("CARENCIA DE SEGURO OBLIGATORIO")
        return ("seguro", facts[0], facts)

    # --------------------------
    # 6) ITV
    # --------------------------
    if any(s in combined for s in ["itv", "inspeccion tecnica", "inspeccion tecnica de vehiculos", "caducidad de itv", "itv caducada"]):
        facts.append("ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA")
        return ("itv", facts[0], facts)

    # --------------------------
    # 7) MARCAS VIALES
    # --------------------------
    if any(s in combined for s in ["linea continua", "marca longitudinal continua", "senalizacion horizontal", "marca vial"]):
        facts.append("NO RESPETAR MARCA VIAL")
        return ("marcas_viales", facts[0], facts)

    # --------------------------
    # 8) CARRIL / POSICIÓN EN VÍA
    # --------------------------
    if any(s in combined for s in ["carril distinto del situado mas a la derecha", "posicion en la via", "posición en la vía", "articulo 31", "art. 31"]):
        facts.append("POSICIÓN INCORRECTA EN LA VÍA / USO INDEBIDO DEL CARRIL")
        return ("carril", facts[0], facts)

    # --------------------------
    # 9) CONDICIONES VEHÍCULO
    # --------------------------
    cond_signals = [
        "condiciones reglamentarias",
        "alumbrado",
        "senalizacion optica",
        "señalización óptica",
        "neumatico",
        "neumático",
        "reforma",
        "homolog",
        "luz trasera",
        "deslumbr",
        "reflect",
        "pulido",
    ]
    if any(s in combined for s in cond_signals):
        facts.append("INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO")
        return ("condiciones_vehiculo", facts[0], facts)

    # --------------------------
    # 10) ATENCIÓN / NEGLIGENTE
    # --------------------------
    at_signals = [
        "no mantener la atencion",
        "atencion permanente",
        "conduccion negligente",
        "conducción negligente",
        "distraccion",
        "distracción",
        "bail",
        "palm",
        "golpe",
        "volante",
        "tambor",
        "menor",
        "bebe",
        "bebé",
        "intercept",
        "tramo",
    ]
    if any(s in combined for s in at_signals):
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    return ("otro", "", [])


def _enrich_with_triage(extracted_core: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    out = dict(extracted_core or {})

    tipo, hecho, facts = _detect_facts_and_type(text_blob, extracted_core)
    out["tipo_infraccion"] = tipo
    out["hecho_imputado"] = hecho or None
    out["facts_phrases"] = facts
    out["jurisdiccion"] = _extract_jurisdiction(text_blob, extracted_core)

    existing_literal = _safe_str(out.get("hecho_denunciado_literal")).strip()
    extracted_literal = existing_literal or _extract_literal_from_blob(text_blob)
    if extracted_literal:
        out["hecho_denunciado_literal"] = extracted_literal

    resumen = _build_hecho_resumido(
        extracted_literal or "",
        tipo=tipo,
        fallback=_safe_str(out.get("hecho_imputado") or hecho or ""),
    )
    if resumen:
        out["hecho_denunciado_resumido"] = resumen

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

    return out


def _needs_speed_retry(core: Dict[str, Any]) -> bool:
    """
    True si parece velocidad pero faltan medida/límite.
    """
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

                # Siempre hacemos visión también para PDFs, porque ayuda mucho con boletines escaneados
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