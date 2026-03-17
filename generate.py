import json
import re
import unicodedata
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

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
    # Regla orientativa de visualización: >100 km/h => 5%; en caso contrario 5 km/h.
    # Para radares tipo Multanova/antena esto encaja con el ejemplo esperado por el usuario.
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

    # Solo si el foco viene vacío o muy pobre, hacemos fallback controlado al OCR completo.
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


def get_hecho_para_recurso(core: Dict[str, Any]) -> str:
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

    tipo = resolve_infraction_type(core)
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
    return txt


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



def _fold_text(value: Any) -> str:
    text = _safe_str(value).lower().strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def _normalized_blob(core: Dict[str, Any]) -> str:
    return _fold_text(json.dumps(core or {}, ensure_ascii=False))


def _focused_infraction_blob(core: Dict[str, Any]) -> str:
    """
    Blob conservador para clasificar la familia.
    Prioriza SOLO el hecho denunciado / resumido / imputado y evita contaminarse
    con OCR global, PDFs reconstruidos o textos internos del expediente.
    """
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

    blob = " ".join(p for p in parts if isinstance(p, str) and p.strip())
    return _fold_text(blob)


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

    # Casos que suelen ser órdenes de agentes / no detenerse
    agent_tokens = [
        "ordenes de los agentes",
        "ordenes del agente",
        "orden del agente",
        "no se para",
        "no detiene el vehiculo",
        "no detenerse",
        "no obedecer",
        "agente",
        "agentes",
        "policia",
        "alto",
    ]
    for tok in agent_tokens:
        if tok in blob:
            score += 3

    # Casos de bici/patinete/alumbrado que no deben saltar a semáforo por una roja
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

    # Casos de atención / temeraria / art. 3.1
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
        "no obedecer",
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

    # Regla conservadora:
    # - Semáforo solo con evidencia fuerte
    # - Si hay fuerte señal de agentes / art. 3.1 / bici-patinete-alumbrado, no forzar semáforo
    if positive >= 6 and positive >= blockers + 3:
        return True

    # Caso clarísimo y expreso
    if "cruce con fase roja del semaforo" in blob:
        return True

    return False


def _score_infraction_from_core(core: Dict[str, Any]) -> Dict[str, int]:
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
    }

    def add(tipo: str, signals, points: int) -> None:
        for s in signals:
            if s in blob:
                scores[tipo] += points

    add(
        "velocidad",
        [
            "km/h",
            "radar",
            "cinemometro",
            "exceso de velocidad",
            "limitada la velocidad a",
            "multanova",
            "velolaser",
            "pegasus",
            "tramo",
            "velocidad medida",
            "velocidad detectada",
            "velocidad maxima",
            "velocidad maxima permitida",
            "superar velocidad",
        ],
        3,
    )

    scores["semaforo"] += _semaforo_positive_signals(blob)
    scores["semaforo"] -= _semaforo_blockers(blob)

    if any(s in blob for s in ["luz roja", "fase roja", "semaforo", "senal luminosa roja", "linea de detencion"]):
        scores["velocidad"] = 0

    add(
        "movil",
        [
            "telefono movil",
            "teléfono móvil",
            "telefono",
            "teléfono",
            "movil",
            "móvil",
            "telefono movil durante la conduccion",
            "teléfono móvil durante la conducción",
            "manipular telefono movil durante la conduccion",
            "manipular teléfono móvil durante la conducción",
            "manipular telefono movil",
            "manipular teléfono móvil",
            "manipular telefono",
            "manipular teléfono",
            "telefono movil con la mano",
            "teléfono móvil con la mano",
            "dispositivo movil",
            "dispositivo móvil",
            "dispositivo movil con la mano",
            "dispositivo móvil con la mano",
            "con la mano",
            "sujetar el telefono movil",
            "sujetar el teléfono móvil",
            "pantalla del telefono",
            "pantalla del teléfono",
            "interactuar con la pantalla",
            "interactuar con la pantalla del telefono",
            "interactuar con la pantalla del teléfono",
            "uso manual",
            "manipulando el movil",
            "manipulando el móvil",
            "sujetando con la mano el dispositivo",
            "utilizar dispositivo movil",
            "utilizar dispositivo móvil",
            "terminal movil",
            "terminal móvil",
            "terminal telefonico",
            "terminal telefónico",
            "terminal portatil",
            "terminal portátil",
            "uso del telefono movil",
            "uso del teléfono móvil",
            "dispositivo de comunicacion manual",
            "dispositivo de comunicación manual",
            "dispositivo electronico portatil",
            "dispositivo electrónico portátil",
            "dispositivo electronico",
            "dispositivo electrónico",
            "manipular dispositivo electronico en marcha",
            "manipular dispositivo electrónico en marcha",
            "uso de dispositivo durante la conduccion",
            "uso de dispositivo durante la conducción",
            "aparato de telecomunicaciones",
            "pantalla de terminal",
            "pantalla digital",
            "dispositivo portatil",
            "dispositivo portátil",
            "whatsapp",
            "llamada telefónica",
            "llamada telefonica",
        ],
        3,
    )

    add(
        "auriculares",
        [
            "auricular",
            "auriculares",
            "cascos conectados",
            "reproductores de sonido",
            "porta auricular",
            "bluetooth",
            "intercomunicador",
            "aparatos receptores",
            "aparatos reproductores",
            "dispositivo de audio",
            "reproductor de musica",
            "reproductor de música",
            "oido izquierdo",
            "oido derecho",
        ],
        3,
    )

    add(
        "cinturon",
        [
            "cinturon de seguridad",
            "sin cinturon",
            "sin cinturón",
            "correctamente abrochado",
            "no utilizar el cinturon",
            "no utilizar el cinturón",
            "sin llevar abrochado el cinturon",
            "ocupante del vehiculo sin cinturon",
            "ocupante del vehículo sin cinturón",
        ],
        3,
    )

    add(
        "casco",
        [
            "sin casco",
            "no llevar casco",
            "casco de proteccion",
            "casco de protección",
            "casco homologado",
            "casco no homologado",
            "casco desabrochado",
            "casco mal abrochado",
            "casco incorrectamente abrochado",
            "llevar el casco incorrectamente abrochado",
        ],
        3,
    )

    add(
        "atencion",
        [
            "atencion permanente",
            "atención permanente",
            "no mantener la atencion permanente",
            "no mantener la atención permanente",
            "no mantener la atencion permanente a la conduccion",
            "no mantener la atención permanente a la conducción",
            "sin la atencion necesaria",
            "sin la atención necesaria",
            "conducir sin la atencion necesaria",
            "conducir sin la atención necesaria",
            "falta de atencion",
            "falta de atención",
            "diligencia debida",
            "debida atencion",
            "debida atención",
            "atencion a la via",
            "atención a la vía",
            "conducta distraida",
            "conducta distraída",
            "atencion suficiente al trafico",
            "atención suficiente al tráfico",
            "sin atencion suficiente",
            "sin atención suficiente",
            "conducir sin atencion suficiente",
            "conducir sin atención suficiente",
            "atencion al volante",
            "atención al volante",
            "comprometen la atencion al volante",
            "comprometen la atención al volante",
            "disminuyen la atencion",
            "disminuyen la atención",
            "interior del vehiculo",
            "interior del vehículo",
            "limitan el control del vehiculo",
            "limitan el control del vehículo",
            "conduccion negligente",
            "conducir de forma negligente",
            "distraccion",
            "temeraria",
            "conducir de forma temeraria",
            "sin la diligencia necesaria",
            "manipulan objetos",
            "soltar ambas manos",
            "ambas manos del volante",
            "bailando",
            "mordia las uñas",
            "mordia las unas",
            "morderse las uñas",
            "morderse las unas",
            "se muerde las uñas",
            "se muerde las unas",
            "mientras se muerde las uñas",
            "mientras se muerde las unas",
            "conducir mientras se muerde las uñas",
            "conducir mientras se muerde las unas",
            "conducir mientras se mordia las uñas",
            "conducir mientras se mordia las unas",
            "mirando repetidamente al acompañante",
            "mirando repetidamente al acompanante",
            "mirando repetidamente",
            "come y manipula objetos",
            "comiendo y manipulando objetos",
            "sin la atencion necesaria",
            "fumando",
            "comiendo",
            "bebiendo",
            "sin mirar la carretera",
            "mirando al acompanante",
            "mirando al copiloto",
            "mirando hacia el interior del vehiculo",
            "manipulando objetos",
            "manipulando comida",
            "manipulando bebida",
            "libertad de movimientos",
            "no se para",
            "ordenes de los agentes",
            "orden del agente",
            "no respeta las ordenes",
            "no respeta las ordenes de los agentes",
            "no obedece",
            "agente",
            "agentes",
            "policia",
            "articulo 3",
            "art. 3",
            "riesgo",
            "peligro",
        ],
        3,
    )

    add("marcas_viales", ["linea continua", "marca vial", "marca longitudinal continua"], 3)
    add("seguro", ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "8/2004", "fiva"], 3)
    add("itv", ["itv", "inspeccion tecnica", "itv caducada"], 3)

    add(
        "alcohol",
        [
            "alcohol",
            "tasa de alcohol",
            "alcoholemia",
            "etilometro",
            "etilometro evidencial",
            "aire espirado",
            "mg/l",
            "miligramos por litro",
            "control de alcoholemia",
            "prueba de alcoholemia",
            "resultado positivo",
            "prueba de deteccion alcoholica",
            "prueba de deteccion de alcohol",
        ],
        5,
    )

    add(
        "condiciones_vehiculo",
        [
            "condiciones reglamentarias",
            "luces no reglamentarias",
            "alumbrado",
            "senalizacion optica",
            "dispositivo luminoso",
            "panel luminoso",
            "homolog",
            "reflectante",
            "luz roja intermitente",
            "luz trasera roja",
            "destellos",
            "intermitente",
            "catadioptrico",
            "deslumbramiento",
            "deslumbrar",
            "neumatico",
            "neumático",
            "neumaticos",
            "neumáticos",
            "neumaticos en mal estado",
            "neumáticos en mal estado",
            "neumatico en mal estado",
            "neumático en mal estado",
            "neumatico liso",
            "neumático liso",
            "circular con neumatico liso",
            "circular con neumático liso",
            "neumaticos lisos",
            "neumáticos lisos",
            "elementos del vehiculo en deficiente estado",
            "elementos del vehículo en deficiente estado",
            "deficiencias tecnicas en el vehiculo",
            "deficiencias técnicas en el vehículo",
            "alteracion de elementos luminosos",
            "alteración de elementos luminosos",
            "vehiculo con defectos mecanicos",
            "vehículo con defectos mecánicos",
            "ruedas en mal estado",
            "fallo en sistema de iluminacion",
            "fallo en sistema de iluminación",
            "elementos de seguridad defectuosos",
            "neumaticicos en mal estado",
            "banda de rodadura",
            "parte trasera pulida",
            "reflejandose como un espejo",
            "como un espejo",
            "dibujo inferior",
            "1,6 mm",
            "neumatico liso",
            "neumaticos lisos",
            "neumatico en mal estado",
            "no autorizado",
            "modificacion no autorizada",
        ],
        3,
    )

    add(
        "carril",
        [
            "carril distinto del situado mas a la derecha",
            "carril distinto del sitio mas a la derecha",
            "carril distinto",
            "carril derecho",
            "carril izquierdo",
            "calzada de varios carriles",
            "calzada con mas de un carril",
            "sentido de la marcha",
            "carril central",
            "posicion en la via",
            "posición en la vía",
            "posicion en la calzada",
            "posición en la calzada",
            "posicion correcta en la calzada",
            "posición correcta en la calzada",
            "no respetar la posicion correcta en la calzada",
            "no respetar la posición correcta en la calzada",
            "sin adelantar",
            "adelantar por la derecha",
        ],
        4,
    )

    # Bloqueadores cruzados
    if _looks_like_bike_light_case(core):
        scores["semaforo"] -= 6
        scores["condiciones_vehiculo"] += 4

    if _looks_like_agent_order_case(core):
        scores["semaforo"] -= 6
        scores["atencion"] += 4

    return scores


def resolve_infraction_type(core: Dict[str, Any]) -> str:
    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    if tipo and tipo not in ("otro", "unknown", "desconocido", "generic"):
        return tipo

    # Producción blindada: primero clasificar con el hecho limpio.
    blob = _focused_infraction_blob(core)
    full_blob = _normalized_blob(core)

    if not blob.strip():
        blob = full_blob

    # ===============================
    # BLINDAJE FUERTE POR FAMILIAS
    # ===============================

    # ITV
    if any(s in blob for s in [
        "inspeccion tecnica", "itv",
        "itv caducada", "itv vencida", "itv expirada",
        "sin inspeccion tecnica",
        "no tener vigente la inspeccion tecnica",
        "inspeccion tecnica en vigor",
        "inspeccion tecnica actualizada",
    ]):
        return "itv"

    # SEGURO
    if any(s in blob for s in [
        "seguro obligatorio", "sin seguro", "carecer de seguro",
        "cobertura minima obligatoria",
        "poliza obligatoria",
        "carencia de seguro",
        "sin tener concertado el seguro",
    ]):
        return "seguro"

    # CASCO
    if any(s in blob for s in [
        "casco", "sin casco",
        "casco no abrochado", "casco desabrochado",
        "casco mal ajustado", "casco reglamentario",
        "no hacer uso del casco",
    ]):
        return "casco"

    # AURICULARES
    if any(s in blob for s in [
        "auriculares", "dispositivos acusticos",
        "aparato receptor sonoro",
        "en ambos oidos", "insertados en los oidos",
        "reproductor de sonido", "dispositivo de audio",
    ]):
        return "auriculares"

    # ALCOHOL - prioridad muy alta
    if any(s in blob for s in [
        "alcohol", "alcoholemia", "etilometro",
        "test de alcohol", "resultado positivo",
        "tasa de alcohol", "bajo la influencia de bebidas alcoholicas",
        "aire espirado", "mg/l",
    ]):
        return "alcohol"

    # CONDICIONES VEHICULO
    if any(s in blob for s in [
        "neumatico", "neumaticos",
        "ruedas en mal estado", "ruedas en deficiente estado", "ruedas en deficiente estado de uso",
        "estado de uso", "deficiente estado",
        "componentes mecanicos defectuosos", "defectos mecanicos",
        "deficiencias tecnicas", "deficiencias tecnicas relevantes", "deficiencias",
        "fallo iluminacion", "fallo en el sistema de iluminacion", "sistema de iluminacion",
        "dispositivos luminosos", "dispositivos luminosos no reglamentarios",
        "elementos de seguridad defectuosos", "elementos de seguridad",
        "vehiculo con defectos mecanicos",
    ]):
        return "condiciones_vehiculo"

    # CARRIL
    if any(s in blob for s in [
        "carril", "posicion en la calzada",
        "carril no habilitado", "ocupar carril",
        "configuracion de la calzada",
        "posicion no ajustada", "posicion no ajustada a la configuracion de la calzada",
        "uso indebido del carril", "carril inadecuado",
    ]):
        return "carril"

    # ATENCION
    if any(s in blob for s in [
        "distraccion", "no mantener la atencion",
        "no conservar atencion", "no conservar atencion plena",
        "atencion plena", "conducta distraida",
        "desatencion", "comprometiendo el control del vehiculo",
    ]):
        return "atencion"

    # MOVIL
    if any(s in blob for s in [
        "telefono", "movil",
        "terminal", "dispositivo electronico",
        "pantalla", "aparato de telecomunicaciones",
    ]):
        return "movil"

    # Blindaje duro: si el hecho limpio contiene señales semafóricas, semáforo gana.
    if any(s in blob for s in [
        "luz roja",
        "fase roja",
        "semaforo",
        "semáforo",
        "indicacion luminosa roja",
        "indicación luminosa roja",
        "senal luminosa roja",
        "señal luminosa roja",
        "linea de detencion",
        "línea de detención",
    ]):
        return "semaforo"

    # MARCAS VIALES
    if any(s in blob for s in [
        "linea continua", "marca vial continua", "marca vial longitudinal continua",
        "delimitacion continua", "marca continua",
        "marcas viales prohibidas",
    ]):
        return "marcas_viales"

    # Blindaje duro redundante: si el hecho limpio contiene señales semafóricas, semáforo gana.
    if any(s in blob for s in [
        "luz roja",
        "fase roja",
        "semaforo",
        "semáforo",
        "indicacion luminosa roja",
        "indicación luminosa roja",
        "senal luminosa roja",
        "señal luminosa roja",
        "linea de detencion",
        "línea de detención",
    ]):
        return "semaforo"

    # Casos expresos que no deben saltar a semáforo por ruido contextual
    if _looks_like_bike_light_case(core):
        scores = _score_infraction_from_core(core)
        if scores.get("condiciones_vehiculo", 0) > 0:
            return "condiciones_vehiculo"

    if _looks_like_agent_order_case(core):
        scores = _score_infraction_from_core(core)
        if scores.get("atencion", 0) > 0:
            return "atencion"

    # Semáforo solo con evidencia fuerte y margen suficiente
    if _looks_like_semaforo(core):
        return "semaforo"

    # Si el hecho limpio ya apunta a semáforo, no dejamos que OCR con km/h
    # arrastre el caso hacia velocidad.
    if any(s in blob for s in ["fase roja", "luz roja", "semaforo", "senal luminosa roja", "linea de detencion"]):
        return "semaforo"

    if any(s in blob for s in ["bicicleta", "ciclistas", "ciclista"]) and any(s in blob for s in ["atencion permanente", "conduccion negligente", "distraccion"]):
        return "atencion"

    if any(s in blob for s in [
        "tasa de alcohol",
        "alcoholemia",
        "etilometro",
        "aire espirado",
        "mg/l",
        "control de alcoholemia",
        "prueba de alcoholemia",
    ]):
        return "alcohol"

    if (
        any(s in blob for s in [
            "maniobra peligrosa",
            "sin justificacion",
            "sin justificación",
            "maniobra incorrecta",
        ])
        and not any(s in blob for s in [
            "atencion", "atención", "distraccion", "distracción",
            "mirando", "interior del vehiculo", "interior del vehículo",
            "libertad de movimientos", "mordia las uñas", "mordia las unas",
            "morderse las uñas", "morderse las unas"
        ])
    ):
        return "generic"

    if any(s in blob for s in [
        "no mantener la atencion",
        "no mantener la atención",
        "atencion permanente",
        "atención permanente",
        "no mantener la atencion permanente a la conduccion",
        "no mantener la atención permanente a la conducción",
        "conduccion negligente",
        "conducir de forma negligente",
        "sin la diligencia necesaria",
        "sin la atencion necesaria",
        "sin la atención necesaria",
        "conducir sin la atencion necesaria",
        "conducir sin la atención necesaria",
        "falta de atencion",
        "falta de atención",
        "diligencia debida",
        "debida atencion",
        "debida atención",
        "atencion a la via",
        "atención a la vía",
        "conducta distraida",
        "conducta distraída",
        "atencion suficiente al trafico",
        "atención suficiente al tráfico",
        "atencion al volante",
        "atención al volante",
        "comprometen la atencion",
        "comprometen la atención",
        "disminuyen la atencion",
        "disminuyen la atención",
        "interior del vehiculo",
        "interior del vehículo",
        "limitan el control del vehiculo",
        "limitan el control del vehículo",
        "libertad de movimientos",
        "mordia las uñas",
        "mordia las unas",
        "morderse las uñas",
        "morderse las unas",
        "se muerde las uñas",
        "se muerde las unas",
        "mientras se muerde las uñas",
        "mientras se muerde las unas",
        "conducir mientras se muerde las uñas",
        "conducir mientras se muerde las unas",
        "conducir mientras se mordia las uñas",
        "conducir mientras se mordia las unas",
        "bailando",
        "soltar ambas manos",
        "mirando repetidamente al acompañante",
        "mirando repetidamente al acompanante",
        "come y manipula objetos",
        "fumando",
        "comiendo",
        "bebiendo",
        "sin mirar la carretera",
        "mirando al acompanante",
        "mirando al copiloto",
        "mirando hacia el interior del vehiculo",
        "manipulando objetos",
        "manipulando comida",
        "manipulando bebida",
    ]):
        return "atencion"

    if (
        any(s in blob for s in [
            "roja", "luz roja", "fase roja", "semaforo", "semáforo",
            "interseccion", "intersección", "cruce",
            "indicacion luminosa roja", "indicación luminosa roja",
            "señal luminosa en fase roja", "reguladora del trafico", "reguladora del tráfico",
            "dispositivo luminoso en rojo"
        ])
        and not any(s in blob for s in [
            "trasera intermitente", "parte trasera", "destellos", "dispositivo luminoso no autorizado"
        ])
    ):
        return "semaforo"

    if any(s in blob for s in [
        "telefono movil",
        "teléfono móvil",
        "movil",
        "móvil",
        "manipular telefono movil",
        "manipular teléfono móvil",
        "sujetar el telefono movil",
        "sujetar el teléfono móvil",
        "pantalla del telefono",
        "pantalla del teléfono",
        "whatsapp",
        "terminal movil",
        "terminal móvil",
        "terminal telefonico",
        "terminal telefónico",
        "terminal portatil",
        "terminal portátil",
        "dispositivo de comunicacion manual",
        "dispositivo de comunicación manual",
        "dispositivo electronico portatil",
        "dispositivo electrónico portátil",
        "aparato de telecomunicaciones",
        "pantalla de terminal",
        "pantalla digital",
        "dispositivo portatil",
        "dispositivo portátil",
        "dispositivo electronico",
        "dispositivo electrónico",
        "manipular dispositivo electronico en marcha",
        "manipular dispositivo electrónico en marcha",
        "uso de dispositivo durante la conduccion",
        "uso de dispositivo durante la conducción",
    ]):
        return "movil"

    if any(s in blob for s in [
        "neumatico",
        "neumático",
        "neumaticos",
        "neumáticos",
        "neumatico liso",
        "neumático liso",
        "neumaticos en mal estado",
        "neumáticos en mal estado",
        "banda de rodadura",
        "dibujo inferior",
        "1,6 mm",
        "elementos del vehiculo en deficiente estado",
        "elementos del vehículo en deficiente estado",
        "deficiencias tecnicas en el vehiculo",
        "deficiencias técnicas en el vehículo",
        "alteracion de elementos luminosos",
        "alteración de elementos luminosos",
        "vehiculo con defectos mecanicos",
        "vehículo con defectos mecánicos",
        "ruedas en mal estado",
        "fallo en sistema de iluminacion",
        "fallo en sistema de iluminación",
        "elementos de seguridad defectuosos",
    ]):
        return "condiciones_vehiculo"

    if any(s in blob for s in [
        "bajo la influencia de bebidas alcoholicas",
        "bajo la influencia de bebidas alcohólicas",
        "tasa de alcohol",
        "alcoholemia",
        "test de alcohol",
        "presencia de alcohol en sangre",
        "efectos del alcohol",
    ]):
        return "alcohol"

    if any(s in blob for s in [
        "tasa superior a la permitida",
        "conducir con tasa superior a la permitida",
    ]):
        return "alcohol"

    if any(s in blob for s in [
        "casco reglamentario",
        "no hacer uso del casco reglamentario",
        "casco mal ajustado",
        "casco incorrectamente ajustado",
    ]):
        return "casco"

    if any(s in blob for s in [
        "uso de dispositivos de audio",
        "dispositivos de audio en marcha",
        "dispositivo de audio",
        "dispositivos de audio en ambos oidos",
        "dispositivos de audio en ambos oídos",
        "utilizar dispositivos de audio en ambos oidos",
        "utilizar dispositivos de audio en ambos oídos",
    ]):
        return "auriculares"

    if any(s in blob for s in [
        "inspeccion tecnica en vigor",
        "inspección técnica en vigor",
        "inspeccion tecnica caducada",
        "inspección técnica caducada",
        "sin inspeccion tecnica",
        "sin inspección técnica",
    ]):
        return "itv"

    if any(s in blob for s in [
        "carencia de seguro",
        "carencia de seguro del vehiculo",
        "carencia de seguro del vehículo",
    ]):
        return "seguro"

    if any(s in blob for s in [
        "marcas viales prohibidas",
        "zona de marcas viales prohibidas",
        "invadir zona de marcas viales",
    ]):
        return "marcas_viales"

    if any(s in blob for s in [
        "carril derecho",
        "carril izquierdo",
        "carril central",
        "carril incorrecto",
        "circular por carril incorrecto",
        "posicion correcta en la calzada",
        "posición correcta en la calzada",
        "posicion en la calzada",
        "posición en la calzada",
        "posicion en calzada",
        "posición en calzada",
        "posicion incorrecta en la calzada",
        "posición incorrecta en la calzada",
        "no respetar posicion en calzada",
        "no respetar posición en calzada",
    ]):
        return "carril"

    scores = _score_infraction_from_core(core)
    best = max(scores.items(), key=lambda kv: kv[1])
    sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0

    # Semáforo solo si realmente gana con margen claro
    if best[0] == "semaforo":
        if best[1] >= 6 and best[1] >= second_score + 3:
            return "semaforo"
        return "generic"

    if best[1] > 0:
        return best[0]
    return "generic"


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
    block = _build_strategic_reinforcement_block(core, tipo, assessment)

    if not block.strip():
        return txt

    marker = "II. ALEGACIONES\n\n"
    if marker in txt:
        return txt.replace(marker, marker + block + "\n\n", 1)

    marker_alt = "I. ALEGACIONES\n\n"
    if marker_alt in txt:
        return txt.replace(marker_alt, marker_alt + block + "\n\n", 1)

    return txt


def _build_fundamentos_derecho(tipo: str = "", core: Dict[str, Any] = None) -> str:
    tipo_key = _safe_str(tipo).lower().strip()

    tipo_map = {
        "semaforo": "la infracción semafórica imputada",
        "municipal_semaforo": "la infracción semafórica imputada",
        "velocidad": "la infracción por exceso de velocidad imputada",
        "movil": "la infracción por uso manual de dispositivo móvil imputada",
        "auriculares": "la infracción por uso de auriculares o dispositivos análogos imputada",
        "cinturon": "la infracción relativa al cinturón de seguridad imputada",
        "casco": "la infracción relativa al casco de protección imputada",
        "atencion": "la infracción por falta de atención permanente a la conducción imputada",
        "atencion_bicicleta": "la infracción por falta de atención en la circulación imputada",
        "marcas_viales": "la infracción relativa a marcas viales imputada",
        "seguro": "la infracción relativa al aseguramiento obligatorio imputada",
        "itv": "la infracción relativa a la ITV imputada",
        "condiciones_vehiculo": "la infracción relativa a las condiciones del vehículo imputada",
        "carril": "la infracción relativa a la posición o uso del carril imputada",
        "alcohol": "la infracción relativa a la tasa de alcohol o prueba de alcoholemia imputada",
        "generic": "la infracción administrativa imputada",
    }

    fundamento_especifico_map = {
        "semaforo": (
            "TERCERO.– En las infracciones semafóricas, la Administración debe acreditar de forma "
            "clara la existencia de fase roja activa en el instante exacto del hecho y, en su caso, "
            "el rebase efectivo de la línea de detención o del punto de parada reglamentario. "
            "No basta una referencia genérica a luz roja o señal luminosa si no se concreta con "
            "precisión la secuencia temporal y la forma de constatación."
        ),
        "municipal_semaforo": (
            "TERCERO.– En las infracciones semafóricas de ámbito municipal, la Administración debe "
            "acreditar de forma clara la existencia de fase roja activa en el instante exacto del hecho "
            "y, en su caso, el rebase efectivo de la línea de detención o del punto de parada "
            "reglamentario, con identificación suficiente del cruce y del sistema de captación."
        ),
        "velocidad": (
            "TERCERO.– En las infracciones por exceso de velocidad, la validez de la imputación exige "
            "la correcta identificación del vehículo, de la velocidad medida, del límite aplicable y del "
            "sistema de captación empleado, así como la acreditación técnica suficiente del medio de medición "
            "y de la regularidad de la operación de control."
        ),
        "movil": (
            "TERCERO.– En las infracciones por uso manual de dispositivo móvil, debe acreditarse la "
            "manipulación efectiva del terminal durante la conducción. La mera referencia genérica al "
            "teléfono o a su presencia no basta si no se describe una acción concreta, perceptible y "
            "jurídicamente subsumible en el tipo aplicado."
        ),
        "auriculares": (
            "TERCERO.– En las infracciones por auriculares o dispositivos análogos, debe acreditarse "
            "el uso efectivo de auriculares, cascos conectados o aparatos receptores o reproductores "
            "de sonido durante la conducción, sin que la mera apariencia externa o la simple presencia "
            "de un objeto permita por sí sola tener por integrado el tipo infractor."
        ),
        "cinturon": (
            "TERCERO.– En las infracciones relativas al cinturón de seguridad, la Administración debe "
            "describir con concreción suficiente el incumplimiento observado y las condiciones materiales "
            "de percepción del hecho, pues la afirmación genérica carente de detalle no satisface por sí "
            "sola las exigencias del Derecho sancionador."
        ),
        "casco": (
            "TERCERO.– En las infracciones relativas al casco de protección, debe precisarse si el "
            "incumplimiento consistía en ausencia de casco, casco no homologado, casco desabrochado o "
            "uso incorrecto, con descripción bastante del hecho y de las condiciones de observación."
        ),
        "atencion": (
            "TERCERO.– En las infracciones por falta de atención a la conducción, la Administración "
            "debe concretar qué conducta material revelaría la distracción imputada y en qué medida "
            "afectó realmente al control del vehículo o generó un riesgo vial objetivable. No basta "
            "una descripción llamativa o moralizante desconectada de una afectación real de la conducción."
        ),
        "atencion_bicicleta": (
            "TERCERO.– En las infracciones por falta de atención en la circulación, la Administración "
            "debe concretar qué conducta material revelaría la distracción imputada y en qué medida "
            "afectó realmente al control del vehículo o generó un riesgo vial objetivable."
        ),
        "marcas_viales": (
            "TERCERO.– En las infracciones relativas a marcas viales, debe acreditarse con precisión "
            "la marca concreta afectada, su configuración y la maniobra efectivamente realizada, no "
            "siendo bastante una alusión genérica a la vía o a la circulación."
        ),
        "seguro": (
            "TERCERO.– En las infracciones relativas al seguro obligatorio, la Administración debe "
            "acreditar de forma verificable que en la fecha y hora exactas del hecho no existía cobertura "
            "vigente, con trazabilidad suficiente de la consulta o certificación negativa bastante."
        ),
        "itv": (
            "TERCERO.– En las infracciones relativas a la ITV, la Administración debe acreditar "
            "documentalmente la situación administrativa del vehículo en la fecha del hecho, sin que "
            "baste una referencia imprecisa o carente de soporte verificable."
        ),
        "condiciones_vehiculo": (
            "TERCERO.– En las infracciones relativas a las condiciones del vehículo, la Administración "
            "debe concretar el defecto técnico o reglamentario imputado, el precepto o requisito vulnerado "
            "y el modo objetivo de constatación, especialmente cuando se trate de alumbrado, neumáticos, "
            "dispositivos luminosos, homologación o elementos reflectantes."
        ),
        "carril": (
            "TERCERO.– En las infracciones relativas a la posición o uso del carril, la Administración "
            "debe describir con precisión el carril utilizado, la configuración de la calzada, las "
            "circunstancias del tráfico y la razón por la que la conducta observada sería contraria a la "
            "norma aplicable."
        ),
        "alcohol": (
            "TERCERO.– En las infracciones relativas a alcoholemia o tasa de alcohol, la Administración "
            "debe acreditar con precisión la prueba practicada, el resultado obtenido, el aparato empleado, "
            "la regularidad del procedimiento de medición y, en su caso, la observancia de las garantías "
            "mínimas exigibles para la validez de la prueba."
        ),
        "generic": (
            "TERCERO.– La Administración debe describir con precisión suficiente la conducta imputada y "
            "el precepto aplicado, permitiendo una subsunción jurídica clara y una defensa efectiva."
        ),
    }

    tipo_desc = tipo_map.get(tipo_key, "la infracción administrativa imputada")
    fundamento_tipo = fundamento_especifico_map.get(tipo_key, fundamento_especifico_map["generic"])

    assessment = _assess_legal_strength(core or {}, tipo)
    level = assessment.get("level", "normal")
    flags = set(assessment.get("flags") or [])

    extra_tipicidad = ""
    if "tipicidad_debil" in flags or "hecho_generico" in flags or "boletin_incoherente" in flags:
        extra_tipicidad = (
            " La mera formulación genérica, estereotipada o llamativa del boletín no "
            "satisface por sí sola las exigencias de tipicidad cuando no permite identificar "
            "con precisión la conducta verdaderamente sancionada."
        )

    cuarto_extra = ""
    if level in ("reforzado", "agresivo", "muy_agresivo"):
        cuarto_extra = (
            " No basta una afirmación apodíctica o formularia del agente denunciante cuando "
            "faltan elementos objetivos de corroboración, concreción suficiente de la observación "
            "o datos materiales que permitan contradicción efectiva."
        )

    quinto_extra = ""
    if "sin_prueba_objetiva" in flags or "sin_soporte_tecnico" in flags:
        quinto_extra = (
            " La falta de soporte objetivo, prueba técnica bastante o acreditación material "
            "individualizada del hecho denunciado refuerza la improcedencia de la sanción."
        )

    sexto = ""
    if level in ("agresivo", "muy_agresivo"):
        sexto = (
            "SEXTO.– Cuando el expediente se apoya en una descripción llamativa, moral o "
            "socialmente reprochable, pero no concreta una maniobra peligrosa ni un riesgo vial "
            "objetivable, se desdibuja el verdadero objeto del Derecho sancionador en materia de "
            "tráfico. La potestad sancionadora no puede fundarse en impresiones escandalosas o "
            "valoraciones de contexto, sino en hechos típicos, acreditados y jurídicamente "
            "subsumibles con claridad en el precepto aplicado.\n\n"
        )

    return (
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los principios generales del Derecho "
        "Administrativo sancionador, en particular los principios de legalidad, "
        "tipicidad, presunción de inocencia y carga de la prueba a cargo de la "
        "Administración.\n\n"
        "SEGUNDO.– Conforme al principio de tipicidad, la conducta imputada debe "
        "encajar de forma clara, precisa e inequívoca en el tipo infractor aplicado "
        "por la Administración. La descripción fáctica del boletín o denuncia debe "
        "permitir identificar con claridad la conducta concreta sancionada y su "
        "adecuación jurídica al precepto invocado. En consecuencia, solo puede "
        "mantenerse la sanción cuando quede suficientemente motivada la subsunción "
        f"de los hechos en {tipo_desc}.{extra_tipicidad}\n\n"
        f"{fundamento_tipo}\n\n"
        "CUARTO.– Conforme a reiterada jurisprudencia, la potestad sancionadora "
        "de la Administración exige una motivación suficiente del hecho imputado "
        "y una acreditación probatoria bastante que permita enervar la presunción "
        f"de inocencia del administrado.{cuarto_extra}\n\n"
        "QUINTO.– La ausencia de prueba suficiente, la insuficiente motivación "
        "del expediente o la falta de concreción del hecho imputado determinan "
        f"la improcedencia de la sanción propuesta.{quinto_extra}\n\n"
        f"{sexto}"
    )


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


def _integrate_extract_after_comparecencia(body: str, hecho: str, core: Dict[str, Any] = None) -> str:
    txt = _safe_str(body)
    hecho = _safe_str(hecho).strip()
    core = core or {}
    if not hecho:
        return txt

    if resolve_infraction_type(core) == "velocidad" and (_looks_like_noisy_velocity_text(hecho) or _resolve_velocity_facts(core).get("conflict")):
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


def _upgrade_generated_template(asunto: str, cuerpo: str, tipo: str = "", core: Dict[str, Any] = None) -> Dict[str, str]:
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


def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    if not core.get("hecho_denunciado_literal"):
        literal = extract_hecho_denunciado_literal(core)
        if literal:
            core["hecho_denunciado_literal"] = literal

    tipo = resolve_infraction_type(core)
    scores = _score_infraction_from_core(core)
    jurisdiccion = resolve_jurisdiction(core)

    draft_body = get_hecho_para_recurso(core)
    bicicleta_ctx = _is_bicicleta_context(core)
    dispatched_tpl = None if (tipo == "atencion" and bicicleta_ctx) else dispatch_deterministic_template(core, draft_body=draft_body)

    if isinstance(dispatched_tpl, dict) and dispatched_tpl.get("asunto") and dispatched_tpl.get("cuerpo"):
        tpl = dispatched_tpl
        final_kind = tipo or "deterministic"
    else:
        tpl, final_kind = _select_template(core, tipo, jurisdiccion)

    tpl = ensure_tpl_dict(tpl, core)
    tpl = _upgrade_generated_template(
        tpl.get("asunto") or "",
        tpl.get("cuerpo") or "",
        tipo,
        core,
    )

    cuerpo = tpl.get("cuerpo") or ""
    if tipo == "atencion" and _is_bicicleta_context(core):
        cuerpo = _sanitize_bicicleta_body(cuerpo)

    cuerpo = _inject_tipicidad_material_en_alegaciones(cuerpo, core)
    cuerpo = _inject_strategic_legal_reinforcement(cuerpo, core, tipo)

    hecho = get_hecho_para_recurso(core)
    if hecho and not _looks_like_internal_extract(hecho):
        cuerpo = _integrate_extract_after_comparecencia(cuerpo, hecho, core)

    cuerpo = _fix_alegaciones_numeracion(cuerpo)
    tpl["cuerpo"] = fix_roman_headings(cuerpo)

    # Dejamos el asunto vacío para que el builder no pinte el título antes de la referencia.
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

    return {
        "ok": True,
        "kind": final_kind,
        "asunto": tpl["asunto"],
        "cuerpo": tpl["cuerpo"],
        "docx": {"bucket": b2_bucket, "key": b2_key_docx},
        "pdf": {"bucket": b2_bucket, "key": b2_key_pdf},
        "tipo_infraccion": tipo,
        "jurisdiccion": jurisdiccion,
    }


class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado)
    return {"ok": True, "message": "Recurso generado.", **result}
