import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


HECHO_HEADERS = [
    "hecho imputado",
    "hecho denunciado",
    "hechos denunciados",
    "hecho que se denuncia",
    "hecho que se notifica",
    "infraccion denunciada",
    "infracción denunciada",
    "motivo de la denuncia",
    "hecho",
    "hechos",
]

VERB_STARTS = [
    "no respetar",
    "no obedecer",
    "no mantener",
    "no utilizar",
    "no hacer uso",
    "no circular",
    "circular",
    "circulaba",
    "conducir",
    "conducia",
    "conducía",
    "utilizar",
    "usar",
    "carecer de",
    "carece de",
    "carecia de",
    "carecía de",
    "rebasar",
    "rebaso",
    "rebasó",
    "franquear",
    "franqueo",
    "franqueó",
    "cruzar",
    "cruzo",
    "cruzó",
    "atravesar",
    "atraveso",
    "atravesó",
    "invadir",
    "invadio",
    "invadió",
    "traspasar",
    "traspaso",
    "traspasó",
    "manipular",
    "manipulaba",
    "sostener",
    "sostenia",
    "sostenía",
    "portar",
    "portaba",
    "llevar",
    "llevaba",
    "marchar",
    "marchaba",
]

STOP_HEADERS = [
    "importe",
    "importe total",
    "puntos",
    "puntos a detraer",
    "fecha limite",
    "fecha límite",
    "fecha de notificacion",
    "fecha de notificación",
    "matricula",
    "matrícula",
    "marca",
    "modelo",
    "vehiculo",
    "vehículo",
    "domicilio",
    "expediente",
    "boletin",
    "boletín",
    "precepto infringido",
    "preceptos detectados",
    "agente denunciante",
    "organismo",
    "firma",
    "forma de pago",
    "formas de pago",
    "telefono",
    "teléfono",
    "fax",
    "correo",
    "observaciones",
    "tipo sancion",
    "tipo_sancion",
    "norma_hint",
    "articulo infringido",
    "artículo infringido",
    "apartado infringido",
]

NOISE_PATTERNS = [
    r"\borganismo\s*:\s*.*",
    r"\bexpediente[_\s]*ref\s*:\s*.*",
    r"\btipo[_\s]*sancion\s*:\s*.*",
    r"\bobservaciones\s*:\s*.*",
    r"\bvision[_\s]*raw[_\s]*text\s*:\s*.*",
    r"\braw[_\s]*text[_\s]*pdf\s*:\s*.*",
    r"\braw[_\s]*text[_\s]*vision\s*:\s*.*",
    r"\braw[_\s]*text[_\s]*blob\s*:\s*.*",
    r"\bfecha[_\s]*documento\s*:\s*.*",
    r"\bfecha[_\s]*notificacion\s*:\s*.*",
    r"\bimporte\s*:\s*.*",
    r"\bjurisdiccion\s*:\s*.*",
    r"\btipo[_\s]*infraccion\s*:\s*.*",
    r"\bfacts[_\s]*phrases\s*:\s*.*",
    r"\bpreceptos[_\s]*detectados\s*:\s*.*",
    r"\barticulo[_\s]*infringido[_\s]*num\s*:\s*.*",
    r"\bapartado[_\s]*infringido[_\s]*num\s*:\s*.*",
    r"\bnorma[_\s]*hint\s*:\s*.*",
]

SAFE_REPLACEMENTS = {
    "semaforo": "semáforo",
    "intermitente del semaforo": "intermitente del semáforo",
    "linea de detencion": "línea de detención",
    "telefono movil": "teléfono móvil",
    "cinturon de seguridad": "cinturón de seguridad",
    "casco de proteccion": "casco de protección",
    "inspeccion tecnica": "inspección técnica",
    "vehiculo": "vehículo",
    "matricula": "matrícula",
    "conduccion": "conducción",
    "atencion": "atención",
    "senal luminosa": "señal luminosa",
    "senalizacion": "señalización",
}

FAMILY_HINTS = {
    "semaforo": [
        "semaforo", "fase roja", "luz roja", "linea de detencion",
        "cruce", "interseccion", "intersección",
    ],
    "movil": [
        "telefono movil", "teléfono móvil", "movil", "móvil",
        "terminal", "pantalla", "whatsapp",
    ],
    "casco": [
        "casco", "proteccion en la cabeza", "protección en la cabeza",
        "casco desabrochado", "sin casco",
    ],
    "seguro": [
        "seguro", "poliza", "póliza", "aseguramiento", "responsabilidad civil",
        "cobertura",
    ],
    "carril": [
        "carril", "calzada", "borde derecho", "lado derecho",
        "izquierdo", "central",
    ],
    "condiciones_vehiculo": [
        "parabrisas", "lunas", "cortinillas", "laminas", "láminas",
        "alumbrado", "luz de freno", "piloto trasero", "senal optica", "señal óptica",
    ],
    "marcas_viales": [
        "linea continua", "línea continua", "marca vial", "marcas viales",
        "marca longitudinal",
    ],
    "auriculares": [
        "auricular", "auriculares", "cascos de audio", "dispositivo de audio",
    ],
    "atencion": [
        "atencion", "atención", "distraccion", "distracción",
        "mirando", "acompanante", "acompañante",
    ],
    "velocidad": [
        "km/h", "velocidad", "radar", "cinemometro", "cinemómetro",
    ],
    "alcohol": [
        "alcohol", "alcoholemia", "etilometro", "etilómetro", "mg/l",
    ],
}


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def _normalize_for_search(text: str) -> str:
    txt = _safe_str(text).replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    txt = txt.lower()
    txt = _strip_accents(txt)
    return txt


def _normalize_preserve(text: str) -> str:
    txt = _safe_str(text).replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def _merge_sources(payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "vision_raw_text"):
        val = _safe_str(payload.get(key))
        if val.strip():
            parts.append(val)
    unique: List[str] = []
    seen = set()
    for p in parts:
        q = p.strip()
        if q and q not in seen:
            seen.add(q)
            unique.append(q)
    return "\n".join(unique).strip()


def _find_header_start(search_text: str) -> Tuple[Optional[int], str]:
    best_idx = None
    best_header = ""
    for header in HECHO_HEADERS:
        pattern = re.compile(rf"\b{re.escape(_strip_accents(header.lower()))}\b\s*[:\-]?\s*", re.IGNORECASE)
        m = pattern.search(search_text)
        if m:
            idx = m.start()
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_header = header
    return best_idx, best_header


def _find_verb_start(search_text: str) -> Tuple[Optional[int], str]:
    best_idx = None
    best_verb = ""
    for verb in VERB_STARTS:
        pattern = re.compile(rf"\b{re.escape(_strip_accents(verb.lower()))}\b", re.IGNORECASE)
        m = pattern.search(search_text)
        if m:
            idx = m.start()
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_verb = verb
    return best_idx, best_verb


def _find_next_stop(search_text: str, start_idx: int) -> Tuple[Optional[int], str]:
    best_idx = None
    best_stop = ""
    for stop in STOP_HEADERS:
        pattern = re.compile(rf"(?:^|\n)\s*{re.escape(_strip_accents(stop.lower()))}\b\s*[:\-]?", re.IGNORECASE)
        for m in pattern.finditer(search_text):
            if m.start() <= start_idx:
                continue
            idx = m.start()
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_stop = stop
    return best_idx, best_stop


def _slice_candidate(original_text: str, start_idx: int, end_idx: Optional[int]) -> str:
    if start_idx is None:
        return ""
    chunk = original_text[start_idx:] if end_idx is None else original_text[start_idx:end_idx]
    chunk = chunk.strip()
    if len(chunk) > 1400:
        chunk = chunk[:1400]
    return chunk.strip()


def _remove_header_prefix(chunk: str) -> str:
    txt = chunk.lstrip()
    norm = _normalize_for_search(txt)
    for header in HECHO_HEADERS:
        h = _strip_accents(header.lower())
        pattern = re.compile(rf"^{re.escape(h)}\s*[:\-]?\s*", re.IGNORECASE)
        m = pattern.search(norm)
        if m:
            txt = txt[m.end():].lstrip(" :-\n\t")
            break
    return txt.strip()


def _strip_noise_lines(text: str) -> str:
    if not text:
        return ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    kept: List[str] = []
    for ln in lines:
        ln_norm = _normalize_for_search(ln)
        if any(re.match(pat, ln_norm, flags=re.IGNORECASE) for pat in NOISE_PATTERNS):
            continue
        if any(ln_norm.startswith(_strip_accents(stop.lower())) for stop in STOP_HEADERS):
            break
        kept.append(ln)
    return " ".join(kept).strip()


def _trim_after_sentence_end(text: str) -> str:
    txt = text.strip()
    if not txt:
        return ""
    admin_after_point = re.search(
        r"([\.…])\s+(importe|puntos|matricula|matrícula|marca|modelo|domicilio|boletin|boletín|expediente|precepto|agente|organismo|firma|fecha limite|fecha límite)\b",
        _normalize_for_search(txt),
        flags=re.IGNORECASE,
    )
    if admin_after_point:
        idx = admin_after_point.start(1) + 1
        return txt[:idx].strip()
    return txt


def _cleanup_text(text: str) -> str:
    txt = _safe_str(text)
    txt = txt.replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"[|]+", " ", txt)
    txt = re.sub(r"\[\s*ilegible\s*\]", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"[_]{2,}", " ", txt)
    txt = re.sub(r"\s+([,.;:])", r"\1", txt)
    txt = txt.strip(" :-\t")
    txt = re.sub(r'^[\"“”]+|[\"“”]+$', "", txt).strip()
    return txt


def _safe_reconstruct(text: str) -> str:
    if not text:
        return ""
    out = _normalize_for_search(" " + text + " ")

    for src, dst in SAFE_REPLACEMENTS.items():
        src_norm = _strip_accents(src.lower())
        out = re.sub(rf"\b{re.escape(src_norm)}\b", _strip_accents(dst.lower()), out, flags=re.IGNORECASE)

    out = re.sub(r"\bsemaf\w*\b", "semaforo", out, flags=re.IGNORECASE)
    out = re.sub(r"\bintermit\w*\b", "intermitente", out, flags=re.IGNORECASE)
    out = re.sub(r"\bdetenc\w*\b", "detencion", out, flags=re.IGNORECASE)
    out = re.sub(r"\btelefon\w*\s+movil\b", "telefono movil", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()

    # Recupera tildes de forma muy conservadora
    for src, dst in SAFE_REPLACEMENTS.items():
        out = re.sub(rf"\b{re.escape(_strip_accents(src.lower()))}\b", dst.lower(), out, flags=re.IGNORECASE)
    out = out.replace("semaforo", "semáforo").replace("detencion", "detención")
    out = out.replace("telefono movil", "teléfono móvil").replace("vehiculo", "vehículo")
    out = out.replace("matricula", "matrícula").replace("conduccion", "conducción")
    out = out.replace("atencion", "atención").replace("senal", "señal")
    out = out.replace("senalizacion", "señalización")

    if out:
        out = out[0].upper() + out[1:]
    return out


def _family_scores(text: str) -> Dict[str, int]:
    blob = _normalize_for_search(text)
    scores: Dict[str, int] = {k: 0 for k in FAMILY_HINTS.keys()}
    for family, hints in FAMILY_HINTS.items():
        for h in hints:
            if _strip_accents(h.lower()) in blob:
                scores[family] += 1
    return scores


def _assess_confidence(start_header: str, start_verb: str, stop_header: str, raw: str, clean: str) -> Tuple[float, str, Optional[str]]:
    score = 0.0
    reason = ""
    detected_family = None

    if start_header:
        score += 0.35
    elif start_verb:
        score += 0.18
        reason = "sin encabezado fuerte"

    if stop_header:
        score += 0.20
    else:
        reason = (reason + "; " if reason else "") + "sin corte documental claro"

    if clean and len(clean) >= 20:
        score += 0.15
    if clean and len(clean) >= 45:
        score += 0.10
    if raw and len(raw) > len(clean) and len(clean) > 0:
        score += 0.05

    scores = _family_scores(clean)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if ordered and ordered[0][1] > 0:
        detected_family = ordered[0][0]
        score += 0.10
        if len(ordered) > 1 and ordered[0][1] >= ordered[1][1] + 2:
            score += 0.05
        elif len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            reason = (reason + "; " if reason else "") + "familia ambigua"

    if len(clean) > 350:
        reason = (reason + "; " if reason else "") + "hecho posiblemente demasiado largo"
        score -= 0.10
    if len(clean) < 12:
        reason = (reason + "; " if reason else "") + "hecho demasiado corto"
        score -= 0.25

    score = max(0.0, min(0.99, round(score, 2)))
    return score, reason.strip("; "), detected_family


def extract_hecho_imputado(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge_sources(payload)
    original = _normalize_preserve(merged)
    search = _normalize_for_search(original)

    header_idx, header_name = _find_header_start(search)
    verb_idx, verb_name = _find_verb_start(search)

    start_idx = None
    start_detected = ""
    if header_idx is not None:
        start_idx = header_idx
        start_detected = header_name
    elif verb_idx is not None:
        start_idx = verb_idx
        start_detected = verb_name

    if start_idx is None:
        return {
            "hecho_crudo": "",
            "hecho_reconstruido": "",
            "hecho_limpio": "",
            "inicio_detectado": "",
            "fin_detectado": "",
            "confianza": 0.0,
            "motivo_baja_confianza": "no se detectó un inicio fiable",
            "familia_sugerida": None,
        }

    end_idx, end_detected = _find_next_stop(search, start_idx)
    raw_chunk = _slice_candidate(original, start_idx, end_idx)
    raw_chunk = _remove_header_prefix(raw_chunk)
    raw_chunk = _strip_noise_lines(raw_chunk)
    raw_chunk = _trim_after_sentence_end(raw_chunk)
    raw_chunk = _cleanup_text(raw_chunk)

    reconstructed = _safe_reconstruct(raw_chunk)
    clean = _cleanup_text(reconstructed)

    confidence, reason, family = _assess_confidence(
        header_name,
        "" if header_name else verb_name,
        end_detected,
        raw_chunk,
        clean,
    )

    return {
        "hecho_crudo": raw_chunk,
        "hecho_reconstruido": reconstructed,
        "hecho_limpio": clean,
        "inicio_detectado": start_detected,
        "fin_detectado": end_detected,
        "confianza": confidence,
        "motivo_baja_confianza": reason,
        "familia_sugerida": family,
    }
