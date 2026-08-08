import json
import re
import unicodedata
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from scoring import classify

from database import get_engine
from jurisprudencia_base import obtener_bloques_juridicos

from ai.infractions.semaforo import (
    build_semaforo_strong_template,
    build_semaforo_legal_intelligence,
    build_semaforo_intelligence_template,
    SEMAFORO_LEGAL_INTELLIGENCE_VERSION,
)
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
    build_velocity_legal_intelligence,
    VELOCITY_LEGAL_INTELLIGENCE_VERSION,
)

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from ai.infractions.dispatch import dispatch_deterministic_template

router = APIRouter(tags=["generate"])

_GENERATOR_VERSION = "traffic_generate_v1_7"


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



def _extract_person_fields_from_core(core: Dict[str, Any]) -> Dict[str, str]:
    """
    Extrae datos visibles del multado desde OCR/core:
    - nombre
    - DNI/NIE
    - domicilio
    - CP/localidad/provincia si se puede
    No inventa email ni teléfono.
    """
    core = core or {}

    raw_sources = [
        _safe_str(core.get("full_name")),
        _safe_str(core.get("titular")),
        _safe_str(core.get("nombre_completo")),
        _safe_str(core.get("nombre_multado")),
        _safe_str(core.get("interesado")),
        _safe_str(core.get("raw_text_pdf")),
        _safe_str(core.get("raw_text_vision")),
        _safe_str(core.get("raw_text_blob")),
        _safe_str(core.get("vision_raw_text")),
        json.dumps(core, ensure_ascii=False),
    ]
    blob = "\n".join(x for x in raw_sources if x.strip())
    flat = re.sub(r"\s+", " ", blob).strip()
    upper = flat.upper()

    out: Dict[str, str] = {}

    dni = (
        _safe_str(core.get("dni_nie"))
        or _safe_str(core.get("dni"))
        or _safe_str(core.get("documento_identidad"))
        or _safe_str(core.get("document_identitat_infractor"))
    ).strip()

    if not dni:
        m = re.search(r"\b0?\d{7,8}[A-Z]\b", flat, flags=re.I)
        if m:
            dni = m.group(0).upper()

    if dni:
        out["dni_nie"] = dni.upper()
        out["dni"] = dni.upper()

    full_name = (
        _safe_str(core.get("full_name"))
        or _safe_str(core.get("titular"))
        or _safe_str(core.get("nombre_completo"))
        or _safe_str(core.get("nombre_multado"))
        or _safe_str(core.get("interesado"))
        or _safe_str(core.get("infractor"))
    ).strip()

    # Caso real: DNI seguido de nombre y dirección.
    if not full_name and dni:
        idx = upper.find(dni.upper())
        window = flat[idx + len(dni): idx + len(dni) + 300] if idx >= 0 else ""
        # cortar antes de vías/dirección o códigos
        window_name = re.split(
            r"\b(?:CA|C/|CALLE|CARRER|AV|AVDA|AVENIDA|PASEO|PASSEIG|PLAZA|CL|CTRA)\b",
            window,
            maxsplit=1,
            flags=re.I,
        )[0]
        window_name = re.sub(r"\b\d{2}[-/]\d{2}[-/]\d{4}\b", " ", window_name)
        window_name = re.sub(r"\b\d{6,}\b", " ", window_name)
        window_name = re.sub(r"\b\d+[,.]\d{2}\b", " ", window_name)

        candidates = re.findall(
            r"\b([A-ZÁÉÍÓÚÜÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÜÑ]{2,}){1,4})\b",
            window_name,
            flags=re.I,
        )
        bad = {
            "DATA", "FECHA", "IMPORT", "IMPORTE", "REFERENCIA", "IDENTIFICACION", "IDENTIFICACIÓ",
            "EXPEDIENTE", "DOCUMENT", "IDENTITAT", "INFRACTOR", "LIMIT", "PAGAMENT", "PAGO"
        }
        for cand in candidates:
            words = [w for w in re.sub(r"[^A-ZÁÉÍÓÚÜÑa-záéíóúüñ\s]", " ", cand).split() if w]
            if 2 <= len(words) <= 5 and not any(w.upper() in bad for w in words):
                full_name = " ".join(w[:1].upper() + w[1:].lower() for w in words)
                break

    if full_name:
        out["full_name"] = full_name

    domicilio = (
        _safe_str(core.get("domicilio_notif"))
        or _safe_str(core.get("domicilio"))
        or _safe_str(core.get("direccion"))
        or _safe_str(core.get("domicilio_multado"))
        or _safe_str(core.get("direccion_infractor"))
    ).strip()

    if not domicilio:
        # Caso real: CA ALBA BARATA, 32 08230 MATADEPERA BARCELONA
        patterns = [
            r"\b((?:CA|C/|CALLE|CARRER|AV|AVDA|AVENIDA|PASEO|PASSEIG|PLAZA|CL)\s+[A-ZÁÉÍÓÚÜÑ0-9\s,.-]{5,120}?\b\d{5}\b\s+[A-ZÁÉÍÓÚÜÑ\s.-]{2,80})",
            r"\b([A-ZÁÉÍÓÚÜÑ\s.-]{3,80},?\s*\d{1,4}\s*,?\s*\b\d{5}\b\s+[A-ZÁÉÍÓÚÜÑ\s.-]{2,80})",
        ]
        for pat in patterns:
            m_addr = re.search(pat, flat, flags=re.I)
            if m_addr:
                domicilio = re.sub(r"\s+", " ", m_addr.group(1)).strip().upper()
                break

    if domicilio:
        domicilio = domicilio.strip(" ,.-")
        out["domicilio_notif"] = domicilio
        out["domicilio"] = domicilio

        cp_match = re.search(r"\b(\d{5})\b", domicilio)
        if cp_match:
            out["cp"] = cp_match.group(1)
            after_cp = domicilio[cp_match.end():].strip(" ,.-")
            parts = [p for p in after_cp.split() if p]
            if len(parts) >= 2:
                out["provincia"] = parts[-1]
                out["localidad"] = " ".join(parts[:-1])
            elif len(parts) == 1:
                out["localidad"] = parts[0]

    return out


def _enrich_core_with_person_fields(core: Dict[str, Any]) -> Dict[str, Any]:
    core = dict(core or {})
    patch = _extract_person_fields_from_core(core)
    for k, v in patch.items():
        if v and not _safe_str(core.get(k)).strip():
            core[k] = v

    if not _safe_str(core.get("domicilio")).strip() and _safe_str(core.get("domicilio_notif")).strip():
        core["domicilio"] = core.get("domicilio_notif")
    if not _safe_str(core.get("dni")).strip() and _safe_str(core.get("dni_nie")).strip():
        core["dni"] = core.get("dni_nie")

    return core


def _strip_duplicate_extractos(body: str) -> str:
    """
    Deja un solo 'Extracto literal del boletín', priorizando el más completo.
    """
    txt = _safe_str(body)
    pat = r'Extracto literal del bolet[ií]n:\s*\n[“"]([^”"]+)[”"]\s*\n*'
    matches = list(re.finditer(pat, txt, flags=re.I))
    if len(matches) <= 1:
        return txt

    values = [m.group(1).strip() for m in matches]
    # priorizar el más específico/completo
    chosen = sorted(values, key=lambda s: (("no intermitente" in s.lower()), len(s)), reverse=True)[0]
    first_done = False

    def repl(m):
        nonlocal first_done
        if not first_done:
            first_done = True
            return f'Extracto literal del boletín:\n“{chosen}”\n\n'
        return ""

    return re.sub(pat, repl, txt, flags=re.I)


def _strip_duplicate_alegaciones(body: str) -> str:
    """
    Elimina bloques repetidos evidentes:
    - Nulidad repetida
    - Insuficiencia probatoria repetida genérica
    Mantiene las alegaciones numeradas fuertes.
    """
    txt = _safe_str(body)

    block_patterns = [
        r"ALEGACIÓN\s+—\s+NULIDAD DE PLENO DERECHO\s*\n\nCon carácter principal,[\s\S]*?(?=\n\nALEGACIÓN|\n\nFUNDAMENTOS|\Z)",
        r"ALEGACIÓN\s+—\s+nulidad de pleno derecho\s*\n\nCon carácter principal,[\s\S]*?(?=\n\nALEGACIÓN|\n\nFUNDAMENTOS|\Z)",
        r"ALEGACIÓN\s+—\s+INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS\s*\n\n(?:•[^\n]+\n?){1,8}",
    ]

    for pat in block_patterns:
        matches = list(re.finditer(pat, txt, flags=re.I))
        if len(matches) > 1:
            # conservar la versión más larga
            blocks = [(m.start(), m.end(), m.group(0)) for m in matches]
            keep = max(blocks, key=lambda x: len(x[2]))
            new = []
            last = 0
            for b in blocks:
                new.append(txt[last:b[0]])
                if b == keep:
                    new.append(b[2])
                last = b[1]
            new.append(txt[last:])
            txt = "".join(new)

    txt = re.sub(r"\n{4,}", "\n\n\n", txt)
    return txt.strip() + "\n"


def _strip_duplicate_final_sections(body: str) -> str:
    """
    Evita duplicar FUNDAMENTOS / SUPLICA / OTROSÍ.
    """
    txt = _safe_str(body).strip()
    if not txt:
        return txt

    # Mantener solo el primer bloque de fundamentos hasta el final, pero si hay un segundo,
    # quitar desde el segundo fundamento hacia abajo.
    fund_matches = list(re.finditer(r"\n+FUNDAMENTOS DE DERECHO\n+", txt, flags=re.I))
    if len(fund_matches) > 1:
        txt = txt[:fund_matches[1].start()].rstrip()

    sup_matches = list(re.finditer(r"\n+S\s*U\s*P\s*L\s*I\s*C\s*A\s*:\n+", txt, flags=re.I))
    if len(sup_matches) > 1:
        txt = txt[:sup_matches[1].start()].rstrip()

    otrosi_matches = list(re.finditer(r"\n+OTROS[IÍ]\s+DIGO\n+", txt, flags=re.I))
    if len(otrosi_matches) > 1:
        txt = txt[:otrosi_matches[1].start()].rstrip()

    return txt.strip() + "\n"


def _clean_final_resource_body(body: str) -> str:
    txt = _safe_str(body)
    txt = _strip_duplicate_extractos(txt)
    txt = _strip_duplicate_alegaciones(txt)
    txt = _strip_duplicate_final_sections(txt)
    txt = re.sub(r"\n{4,}", "\n\n\n", txt)
    return txt.strip() + "\n"


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


def _is_rtm_validated_extraction(core: Dict[str, Any]) -> bool:
    """Indica si los DATOS DOCUMENTALES del reanálisis deben prevalecer sobre cases.

    Importante:
    ready_for_generate expresa si existe autorización para pasar al generador
    jurídico, NO si los hechos documentales ya están validados.

    En SEMÁFORO V19+, la extracción se deja deliberadamente con
    ready_for_generate=false hasta que exista especialista jurídico. Aun así,
    expediente/matrícula/organismo/fecha/lugar/hecho ya son hechos documentales
    validados y NO deben ser pisados por valores antiguos de la tabla cases.
    """
    core = core or {}

    version = _safe_str(core.get("extractor_version")).strip()
    if not version.startswith("traffic_fine_reanalysis_"):
        return False

    unresolved = core.get("unresolved_critical_fields") or []
    if unresolved:
        return False

    specialist = _safe_str(
        core.get("specialist_dispatch")
        or core.get("familia_resuelta")
        or core.get("tipo_infraccion")
    ).lower().strip()

    if specialist == "semaforo":
        secondary_version = _safe_str(
            core.get("semaforo_secondary_facts_version")
        ).strip()

        required_document_fields = [
            "expediente_ref",
            "organismo",
            "matricula",
            "fecha_infraccion",
            "lugar_infraccion",
            "hecho_imputado",
        ]
        missing_current = [
            key
            for key in required_document_fields
            if core.get(key) in (None, "", [], {})
        ]

        return bool(
            secondary_version.startswith("semaforo_secondary_v1_")
            and not missing_current
        )

    # Comportamiento histórico para velocidad y resto de especialistas ya
    # habilitados: ready_for_generate sigue actuando como guard.
    if core.get("ready_for_generate") is False:
        return False

    missing = (
        (core.get("critical_fields_validation") or {}).get("missing_required")
        or []
    )
    return not bool(missing)


def _explicit_tramo_radar_signal(blob: str) -> bool:
    """No confundir 'tramo limitado a 90' con un cinemómetro de tramo."""
    low = _safe_str(blob).lower()
    patterns = [
        r"radar\s+de\s+tramo",
        r"cinem[oó]metro\s+de\s+tramo",
        r"control\s+de\s+velocidad\s+por\s+tramo",
        r"sistema\s+de\s+control\s+de\s+velocidad\s+por\s+tramo",
        r"velocidad\s+media\s+en\s+el\s+tramo",
        r"punto\s+inicial.*punto\s+final.*medici[oó]n",
    ]
    return any(re.search(p, low, flags=re.S) for p in patterns)


def _radar_model_from_core(core: Dict[str, Any]) -> str:
    for key in ("radar_modelo_hint", "radar_modelo", "cinemometro_modelo", "modelo_cinemometro"):
        value = _safe_str(core.get(key)).strip()
        if value and value.lower() not in ("cinemometro", "cinemómetro", "no especificado", "desconocido"):
            return value
    return ""


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
    model_hint = _radar_model_from_core(core)
    antenna = _safe_str(core.get("radar_antena")).strip()

    profile = {
        "kind": "cinemometro_no_especificado",
        "label": model_hint or "cinemómetro (modelo no consignado en la copia)",
        "installation_mode": "",
        "installation_mode_known": False,
        "attack_focus": (
            "Debe aportarse la identificación completa del equipo, su certificado metrológico vigente, "
            "la modalidad concreta de funcionamiento y la prueba técnica que vincule la medición con el vehículo denunciado."
        ),
    }

    # El modelo se respeta tal como aparece en la extracción validada. No se sustituye por otro fabricante.
    model_low = model_hint.lower()
    if any(k in model_low for k in ["multaradar", "multiradar", "multanova"]):
        label = model_hint
        if antenna and antenna not in label:
            label = f"{label}, antena {antenna}"
        profile.update({
            "kind": "cinemometro_modelo_identificado",
            "label": label,
            "attack_focus": (
                f"Tratándose del cinemómetro {model_hint}, debe acreditarse la correspondencia exacta entre el equipo, "
                "la antena o unidad de captación, el certificado metrológico vigente, la imagen original y la medición atribuida. "
                "También debe constar si operaba como instalación fija, estática o móvil, pues esa modalidad condiciona el régimen metrológico aplicable."
            ),
        })
        return profile

    if "pegasus" in blob or "helicoptero" in blob or "helicóptero" in blob:
        profile.update({
            "kind": "pegasus",
            "label": model_hint or "sistema aéreo Pegasus",
            "installation_mode": "aereo",
            "installation_mode_known": True,
            "attack_focus": (
                "Tratándose de medición aérea, debe acreditarse de forma especialmente rigurosa la identificación del sistema, "
                "la secuencia completa de captación y la trazabilidad técnica de la medición."
            ),
        })
        return profile

    if _explicit_tramo_radar_signal(blob):
        profile.update({
            "kind": "radar_tramo",
            "label": model_hint or "sistema de control de velocidad por tramo",
            "installation_mode": "tramo",
            "installation_mode_known": True,
            "attack_focus": (
                "En controles de velocidad por tramo debe acreditarse el punto inicial y final de medición, "
                "la sincronización temporal del sistema, la identificación del vehículo en ambos puntos y la integridad del cálculo."
            ),
        })
        return profile

    if any(k in blob for k in ["velolaser", "lasertech", "lti 20/20", "lti20/20", "ultralyte"]):
        profile.update({
            "kind": "velolaser_laser",
            "label": model_hint or ("Velolaser" if "velolaser" in blob else "cinemómetro láser portátil"),
            "attack_focus": (
                "En mediciones con láser portátil debe acreditarse la instalación, alineación, verificación y la concreta "
                "operativa de captación del vehículo denunciado."
            ),
        })
        return profile

    # Solo se fija modalidad si el documento la expresa de forma específica. La mera palabra 'antena' no convierte el radar en fijo.
    if any(k in blob for k in ["radar fijo", "instalación fija", "instalacion fija", "cabina fija", "pórtico fijo", "portico fijo"]):
        profile.update({
            "kind": "radar_fijo",
            "label": model_hint or "cinemómetro en instalación fija",
            "installation_mode": "fija",
            "installation_mode_known": True,
            "attack_focus": (
                "Debe acreditarse la instalación fija concreta, la verificación metrológica vigente y la correspondencia "
                "entre equipo, captura y vehículo denunciado."
            ),
        })
        return profile

    if any(k in blob for k in ["vehículo en movimiento", "vehiculo en movimiento", "radar móvil en movimiento", "radar movil en movimiento"]):
        profile.update({
            "kind": "radar_movil",
            "label": model_hint or "cinemómetro móvil sobre vehículo",
            "installation_mode": "movil_en_movimiento",
            "installation_mode_known": True,
            "attack_focus": (
                "Debe acreditarse la modalidad de medición con el vehículo en movimiento, la verificación metrológica "
                "del equipo y la secuencia completa de captación."
            ),
        })
        return profile

    if any(k in blob for k in ["radar estático", "radar estatico", "vehículo parado", "vehiculo parado", "ubicación estática", "ubicacion estatica"]):
        profile.update({
            "kind": "radar_estatico",
            "label": model_hint or "cinemómetro en ubicación estática",
            "installation_mode": "estatica",
            "installation_mode_known": True,
            "attack_focus": (
                "Debe acreditarse la ubicación estática concreta, la verificación metrológica vigente y la correspondencia "
                "entre equipo, captura y vehículo denunciado."
            ),
        })
        return profile

    return profile

def _velocity_margin_info_from_profile(measured: Optional[float], profile: Dict[str, Any]) -> Dict[str, Any]:
    """No calcula márgenes presuntos.

    El margen depende, entre otros extremos, de la modalidad real de instalación/uso y de la fase metrológica.
    Solo se devolverá un cálculo si el propio core aporta un margen aplicado de forma expresa y verificable.
    """
    return {"margin_value": None, "corrected_speed": None, "margin_label": ""}

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

    # Una extracción Intelligence CORE validada ya ha resuelto los pares críticos mediante
    # lectura focalizada/zoom y trazabilidad. No reabrimos un falso conflicto por otros números
    # presentes en el documento (importe, antena, expediente, fechas, etc.).
    if _is_rtm_validated_extraction(core) and isinstance(measured, (int, float)) and isinstance(limit, (int, float)) and measured > limit:
        unresolved = set(core.get("unresolved_critical_fields") or [])
        if not ({"velocidad_medida_kmh", "velocidad_limite_kmh"} & unresolved):
            conflict = False

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



def _canonical_hecho_semaforo(core: Dict[str, Any]) -> str:
    if _is_strong_semaforo_generation_case(core):
        return "No respetar la luz roja no intermitente de un semáforo"
    return ""


def _looks_like_ocr_header_not_fact(value: str) -> bool:
    low = _safe_str(value).lower()
    bad = [
        "notificació de denúncia",
        "notificacion de denuncia",
        "notificación de denuncia",
        "document identitat infractor",
        "documento identidad infractor",
        "data emissió",
        "fecha de emision",
        "fecha de emisión",
        "expedient / expedient",
        "identificador valor",
    ]
    return any(x in low for x in bad)


def get_hecho_para_recurso(core: Dict[str, Any], forced_tipo: Optional[str] = None) -> str:
    semaforo_hecho = _canonical_hecho_semaforo(core)
    if semaforo_hecho:
        return semaforo_hecho

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

    # Normalización de títulos de alegaciones para evitar mezclas de
    # mayúsculas/minúsculas producidas por el postprocesado markdown.
    title_replacements = [
        (r"ALEGACIÓN\s+—\s*\*\*insuficiencia probatoria\*\*\s+Y\s+VULNERACIÓN\s+DE\s+GARANTÍAS", "ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS"),
        (r"ALEGACIÓN\s+—\s*insuficiencia probatoria\s+Y\s+VULNERACIÓN\s+DE\s+GARANTÍAS", "ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS"),
        (r"ALEGACIÓN\s+—\s*\*\*nulidad de pleno derecho\*\*", "ALEGACIÓN — NULIDAD DE PLENO DERECHO"),
        (r"ALEGACIÓN\s+—\s*nulidad de pleno derecho", "ALEGACIÓN — NULIDAD DE PLENO DERECHO"),
        (r"ALEGACIÓN\s+TERCERA\s+—\s+SOLICITUD\s+DE\s+expediente íntegro\s+Y\s+PRUEBA\s+TÉCNICA", "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA"),
        (r"SOLICITUD\s+DE\s+expediente íntegro\s+Y\s+PRUEBA\s+TÉCNICA", "SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA"),
    ]
    for patt, repl in title_replacements:
        txt = re.sub(patt, repl, txt, flags=re.IGNORECASE)

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
            "fotograma_no_aportado": "Debe comprobarse y, en su caso, aportarse la imagen o fotograma íntegro y legible con individualización inequívoca del vehículo.",
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




def _build_jurisprudencia_section(tipo: str = "") -> str:
    """
    Integra doctrina controlada del Tribunal Supremo sin alterar la arquitectura
    determinista actual. Usa la base jurídica interna y la presenta como
    fundamento complementario, sin inventar citas ni sentencias concretas.
    """
    try:
        bloques = obtener_bloques_juridicos(tipo or "")
    except Exception:
        return ""

    partes = [p.strip() for p in _safe_str(bloques).split("\n\n") if p.strip()]
    if not partes:
        return ""

    cuerpo = "\n\n".join(f"• {p}" for p in partes)
    return (
        "JURISPRUDENCIA APLICABLE\n\n"
        "Sin perjuicio de la normativa expresamente citada, resultan de aplicación "
        "los siguientes criterios jurisprudenciales consolidados:\n\n"
        f"{cuerpo}"
    )

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

    jurisprudencia_section = _build_jurisprudencia_section(tipo)
    if jurisprudencia_section:
        fundamentos.append(jurisprudencia_section)

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

    if tipo == "semaforo":
        intel = extra or {}
        precept = (intel.get("document_precept_analysis") or {}) if isinstance(intel, dict) else {}
        precept_request = (
            "5) Que se aclare y motive expresamente la concreta base normativa y subsunción empleada, "
            "teniendo en cuenta la referencia transcrita en la notificación y la regulación vigente en la fecha del hecho.\n\n"
            if precept.get("requires_review")
            else
            "5) Que se motive de forma individualizada la subsunción jurídica de los hechos en el precepto aplicado.\n\n"
        )
        return (
            "S U P L I C A:\n\n"
            "1) Que se tengan por formuladas en tiempo y forma las presentes alegaciones y por propuesta la prueba relacionada.\n\n"
            "2) Que se incorpore y permita el acceso a la evidencia original de imagen o vídeo y a los datos técnicos necesarios para comprobar fecha, hora, integridad y asignación al vehículo.\n\n"
            "3) Que se acredite la fase roja no intermitente y el instante exacto en que el vehículo habría rebasado el semáforo o la línea de detención anterior.\n\n"
            "4) Que se identifique el sistema de captación utilizado y, cuando resulte relevante, su funcionamiento, sincronización y trazabilidad con la denuncia generada.\n\n"
            + precept_request +
            "6) Que, si la prueba incorporada no acredita de forma suficiente el hecho imputado o existe una deficiencia de motivación que impida una defensa efectiva, se acuerde el archivo del expediente.\n\n"
            "7) Subsidiariamente, que cualquier denegación de la prueba propuesta y cualquier decisión desestimatoria respondan de forma expresa, individualizada y motivada a las cuestiones planteadas.\n\n"
            "OTROSÍ DIGO\n\n"
            "Que esta parte solicita acceso a la documentación técnica y probatoria que vaya a constituir fundamento esencial de la resolución y se reserva el ejercicio de los recursos y acciones que correspondan."
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


def _infer_sct_territory(core: Dict[str, Any]) -> str:
    """Busca la unidad territorial del Servei Català de Trànsit sin inferirla del domicilio del recurrente."""
    parts = [
        _safe_str(core.get("organismo")),
        _safe_str(core.get("organo")),
        _safe_str(core.get("raw_text_blob")),
        _safe_str(core.get("vision_raw_text")),
    ]
    for nested_key in ("critical_fields_vision", "critical_fields_zoomed"):
        nested = core.get(nested_key) or {}
        if isinstance(nested, dict):
            parts.append(_safe_str(nested.get("organismo")))
    blob = "\n".join(x for x in parts if x.strip())
    folded = blob.upper()
    patterns = [
        r"SERVEI\s+TERRITORIAL\s+(?:DE|DEL)\s+TR[ÀA]NSIT\s+DE\s+([A-ZÀ-Ü ]{3,30})",
        r"SERVICIO\s+TERRITORIAL\s+DE\s+TR[ÁA]NSITO\s+DE\s+([A-ZÁÉÍÓÚÜÑ ]{3,30})",
    ]
    for pat in patterns:
        m = re.search(pat, folded)
        if m:
            value = re.split(r"[\n,;.]", m.group(1))[0].strip(" -")
            for known in ("BARCELONA", "GIRONA", "LLEIDA", "TARRAGONA"):
                if known in value:
                    return known
    return ""


def _is_sct_organism(value: str) -> bool:
    low = _safe_str(value).lower()
    return ("servei" in low and ("trànsit" in low or "transit" in low)) or "servicio territorial de tránsito" in low or "servicio territorial de transito" in low


def _resolve_header_destination(core: Dict[str, Any]) -> Dict[str, str]:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    organismo_raw = _safe_str(core.get("organismo")).strip()

    organismo_fmt = "............................................"
    provincia_fmt = "............................................"

    provincia_aliases = {
        "barcelona": "BARCELONA", "girona": "GIRONA", "gerona": "GIRONA", "madrid": "MADRID",
        "oviedo": "OVIEDO", "asturias": "ASTURIAS", "valencia": "VALENCIA", "sevilla": "SEVILLA",
        "zaragoza": "ZARAGOZA", "malaga": "MÁLAGA", "málaga": "MÁLAGA", "alicante": "ALICANTE",
        "murcia": "MURCIA", "bilbao": "BILBAO", "vizcaya": "VIZCAYA", "bizkaia": "BIZKAIA",
        "granada": "GRANADA", "cordoba": "CÓRDOBA", "córdoba": "CÓRDOBA", "valladolid": "VALLADOLID",
        "coruña": "A CORUÑA", "a coruña": "A CORUÑA", "pontevedra": "PONTEVEDRA", "tarragona": "TARRAGONA",
        "lleida": "LLEIDA", "lerida": "LLEIDA", "castellon": "CASTELLÓN", "castellón": "CASTELLÓN",
        "badajoz": "BADAJOZ", "cadiz": "CÁDIZ", "cádiz": "CÁDIZ", "huelva": "HUELVA", "jaen": "JAÉN",
        "jaén": "JAÉN", "leon": "LEÓN", "león": "LEÓN", "salamanca": "SALAMANCA", "toledo": "TOLEDO",
        "burgos": "BURGOS", "palma": "PALMA", "mallorca": "MALLORCA",
    }

    if _is_sct_organism(organismo_raw) or ("servei català de trànsit" in blob or "servei catala de transit" in blob):
        territory = _infer_sct_territory(core)
        organismo_fmt = "SERVEI CATALÀ DE TRÀNSIT"
        provincia_fmt = territory or "CATALUNYA"
        return {"organismo_cabecera": organismo_fmt, "provincia_cabecera": provincia_fmt}

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

    return {"organismo_cabecera": organismo_fmt, "provincia_cabecera": provincia_fmt}

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

    # Evitar duplicados: algunas plantillas ya traen FUNDAMENTOS / SUPLICA / OTROSÍ.
    if "FUNDAMENTOS DE DERECHO" not in body.upper():
        body = body.rstrip() + "\n\n\n" + fundamentos
    if "S U P L I C A" not in body.upper() and "SUPLICA" not in body.upper():
        body = body.rstrip() + "\n\n" + suplico

    body = _clean_final_resource_body(body)

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


def _is_strong_semaforo_generation_case(core: Dict[str, Any]) -> bool:
    """
    Blindaje de generación: semáforo SOLO cuando el hecho principal apunta
    claramente a luz roja / semáforo.

    Importante:
    - NO reclasifica como semáforo por simples restos de OCR.
    - NO pisa casos graves como conducción temeraria, 6 puntos, 500 €, etc.
    - Generate debe respetar la familia resuelta por analyze salvo evidencia directa.
    """
    core = core or {}

    focused = "\n".join([
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("hecho_denunciado_resumido")),
        _safe_str(core.get("hecho_imputado_textual")),
        _safe_str(core.get("hecho_imputado")),
        _safe_str(core.get("hecho_para_recurso")),
        _safe_str(core.get("tipo_infraccion")),
        _safe_str(core.get("subtipo_infraccion")),
        _safe_str(core.get("norma_hint")),
    ]).lower()

    if len(focused.strip()) < 20:
        focused = "\n".join([
            _safe_str(core.get("raw_text_pdf")),
            _safe_str(core.get("raw_text_vision")),
            _safe_str(core.get("raw_text_blob")),
            _safe_str(core.get("vision_raw_text")),
        ]).lower()

    norm = (
        focused.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
            .replace("à", "a").replace("è", "e").replace("ì", "i").replace("ò", "o").replace("ù", "u")
            .replace("ï", "i").replace("ü", "u").replace("ç", "c")
    )

    hard_blockers = [
        "temeraria",
        "temerari",
        "conduccion temeraria",
        "conduccio temeraria",
        "conduccio temeraria",
        "conduccion negligente",
        "conduccio negligent",
        "maniobra peligrosa",
        "maniobra perillosa",
        "atencion permanente",
        "atencio permanent",
        "sin mantener la atencion",
        "sense mantenir",
        "6 puntos",
        "punts 6",
        "500,00",
        "500.00",
        "500 €",
        "500 eur",
        "art. 3",
        "articulo 3",
        "article 3",
    ]

    if any(b in norm for b in hard_blockers):
        return False

    has_light_red = any(s in norm for s in [
        "no respetar la luz roja",
        "no respectar la llum vermella",
        "luz roja no intermitente",
        "llum vermella no intermitent",
        "fase roja",
        "fase vermella",
        "semaforo en rojo",
        "semafor en vermell",
        "senal luminosa roja",
        "senyal lluminosa vermella",
    ])

    has_semaphore_context = any(s in norm for s in [
        "semaforo",
        "semafor",
        "linea de detencion",
        "linia de detencio",
        "luz roja",
        "llum vermella",
        "fase roja",
        "fase vermella",
        "art. 143",
        "articulo 143",
        "article 143",
        "art. 146",
        "articulo 146",
        "article 146",
    ])

    if has_light_red and has_semaphore_context:
        return True

    if ("semaforo" in norm or "semafor" in norm) and any(s in norm for s in [
        "linea de detencion",
        "linia de detencio",
        "rebase",
        "rebasar",
        "sobrepasar",
        "creuar",
        "cruce",
        "interseccion",
        "interseccio",
    ]):
        return True

    return False

def build_semaforo_pro_template(core: Dict[str, Any]) -> Dict[str, str]:
    """
    Plantilla PRO específica de semáforo. Evita cualquier referencia a radar,
    cinemómetro, margen de velocidad, Multanova o velocidad corregida.
    """
    core = core or {}

    expediente = (
        _safe_str(core.get("expediente_ref"))
        or _safe_str(core.get("numero_expediente"))
        or "[EXPEDIENTE]"
    )

    organo = (
        _safe_str(core.get("organismo"))
        or _safe_str(core.get("organismo_cabecera"))
        or "órgano competente"
    )

    hecho = (
        _safe_str(core.get("hecho_denunciado_literal"))
        or _safe_str(core.get("hecho_denunciado_resumido"))
        or _safe_str(core.get("hecho_imputado"))
        or "No respetar la luz roja no intermitente de un semáforo"
    )

    hecho_norm = hecho.lower()
    if "velocidad" in hecho_norm or not any(x in hecho_norm for x in ["semáforo", "semaforo", "luz roja", "fase roja"]):
        hecho = "No respetar la luz roja no intermitente de un semáforo"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        f"Extracto literal del boletín:\n“{hecho}”\n\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "I. ALEGACIONES\n\n"
        "ALEGACIÓN — NULIDAD DE PLENO DERECHO\n\n"
        "Con carácter principal, esta parte interesa la nulidad de pleno derecho del acto impugnado "
        "cuando el expediente prescinde de elementos esenciales de prueba o de tramitación que impiden "
        "identificar con garantías el hecho realmente sancionado y su adecuado soporte probatorio.\n\n"
        "ALEGACIÓN — INSUFICIENCIA PROBATORIA Y VULNERACIÓN DE GARANTÍAS\n\n"
        "• No consta secuencia completa de imágenes o vídeo que permita verificar el momento exacto del supuesto rebase.\n"
        "• No consta acreditación suficiente de la fase semafórica existente en el instante de los hechos.\n"
        "• No consta acreditación de la posición exacta del vehículo respecto de la línea de detención.\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA OBJETIVA, SECUENCIA COMPLETA Y FASE SEMAFÓRICA\n\n"
        "La imputación consistente en no respetar la luz roja no intermitente de un semáforo exige una prueba objetiva, "
        "completa y verificable del hecho denunciado. No basta una referencia genérica al cruce o al dispositivo de captación, "
        "sino que debe acreditarse de forma suficiente la secuencia completa de los hechos, la fase semafórica existente en "
        "el momento exacto del rebase, la posición del vehículo respecto de la línea de detención y la correspondencia "
        "inequívoca entre la imagen o secuencia aportada y el vehículo denunciado.\n\n"
        "No consta acreditado de forma completa en el expediente:\n"
        "1) Secuencia completa de imágenes o vídeo que permita verificar el momento exacto del supuesto rebase.\n"
        "2) Acreditación de que el semáforo se encontraba efectivamente en fase roja no intermitente.\n"
        "3) Posición exacta del vehículo respecto de la línea de detención.\n"
        "4) Identificación inequívoca del vehículo denunciado.\n"
        "5) Funcionamiento correcto del sistema de captación utilizado.\n"
        "6) Trazabilidad e integridad de la prueba gráfica o videográfica.\n"
        "7) Motivación suficiente sobre la concreta conducta sancionada.\n\n"
        "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n"
        "• Hecho imputado: No respetar la luz roja no intermitente de un semáforo\n"
        "• Sistema de captación: pendiente de acreditación\n"
        "• Prueba gráfica/videográfica completa: no consta aportada de forma íntegra\n"
        "• Fase semafórica: pendiente de acreditación\n"
        "• Posición respecto de la línea de detención: pendiente de acreditación\n\n"
        "A falta de dicha prueba completa, no puede considerarse desvirtuada la presunción de inocencia "
        "ni acreditado con garantías el hecho imputado.\n\n"
        "ALEGACIÓN SEGUNDA — DEFECTOS DE MOTIVACIÓN Y FALTA DE SOPORTE COMPLETO\n\n"
        "La Administración debe motivar de forma individualizada por qué considera acreditado el rebase de la luz roja, "
        "identificando el instante exacto de la infracción, la fase del semáforo, la posición del vehículo y la prueba "
        "gráfica o videográfica en que se sustenta la denuncia. Sin secuencia completa, identificación inequívoca del vehículo "
        "y acreditación del funcionamiento del sistema de captación, no puede enervarse la presunción de inocencia con el rigor "
        "exigible en Derecho sancionador.\n\n"
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo: boletín o denuncia completa, secuencia completa de imágenes "
        "o vídeo, certificación o documentación técnica del sistema de captación utilizado, acreditación de la fase semafórica, "
        "ubicación del dispositivo, acreditación de la línea de detención y motivación detallada de la conducta sancionada.\n\n"
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los artículos 24 y 25 de la Constitución Española, que consagran el derecho a la "
        "presunción de inocencia, la legalidad sancionadora y el principio de tipicidad.\n\n"
        "SEGUNDO.– Conforme a los artículos 53, 63 y concordantes de la Ley 39/2015, de Procedimiento Administrativo Común, "
        "la potestad sancionadora exige la existencia de un procedimiento válido, motivación suficiente y respeto a las garantías "
        "del administrado.\n\n"
        "TERCERO.– Corresponde a la Administración la carga de probar de forma suficiente los hechos constitutivos de la infracción, "
        "sin que puedan bastar presunciones genéricas o referencias incompletas al hecho denunciado.\n\n"
        "CUARTO.– La ausencia de prueba suficiente, la insuficiente motivación del expediente o la falta de concreción del hecho "
        "determinan la improcedencia de la sanción propuesta.\n\n"
        "S U P L I C A:\n\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que, en atención a las alegaciones presentadas y sus fundamentos, se acuerde el ARCHIVO DEL EXPEDIENTE por insuficiencia "
        "probatoria, falta de acreditación suficiente del hecho imputado o ausencia de motivación individualizada.\n"
        "3) Subsidiariamente, para el caso de no estimarse el archivo, que se aporte expediente íntegro y prueba completa para "
        "contradicción efectiva.\n\n"
        "OTROSÍ DIGO\n\n"
        "Que esta parte se reserva expresamente el ejercicio de cuantos recursos administrativos y acciones legales pudieran "
        "corresponder en defensa de sus derechos e intereses legítimos."
    )

    return {
        "asunto": "ESCRITO DE ALEGACIONES — SEMÁFORO EN ROJO",
        "cuerpo": fix_roman_headings(cuerpo),
    }


def _select_template(core: Dict[str, Any], tipo: str, jurisdiccion: str):
    # No forzar semáforo si analyze.py ya resolvió otra familia.
    # Solo usamos el detector de semáforo como fallback cuando el tipo viene vacío/genérico.
    current_tipo = _safe_str(tipo).lower().strip()
    if current_tipo in ("", "generic", "otro", "unknown", "desconocido") and _is_strong_semaforo_generation_case(core):
        tipo = "semaforo"

    if tipo == "semaforo":
        return build_semaforo_intelligence_template(core), "semaforo_intelligence"
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


def _velocity_boundary_paragraph(core: Dict[str, Any], measured: Optional[float], limit: Optional[float]) -> str:
    intelligence = core.get("_velocity_legal_intelligence") if isinstance(core.get("_velocity_legal_intelligence"), dict) else build_velocity_legal_intelligence(core)
    boundary = (intelligence or {}).get("sanction_boundary") or {}
    band = boundary.get("band") or {}
    previous = band.get("previous") or {}
    if not band or not band.get("is_lower_boundary") or not previous:
        return ""
    return (
        f"El cuadro del Anexo IV sitúa la cifra de {band.get('value')} km/h exactamente en el primer valor del tramo de "
        f"{band.get('fine')} euros y {band.get('points')} puntos. El tramo inmediatamente anterior finaliza en "
        f"{previous.get('upper')} km/h y lleva aparejados {previous.get('fine')} euros y {previous.get('points')} puntos. "
        "Esta proximidad al umbral hace especialmente relevante identificar qué magnitud fue utilizada jurídicamente para sancionar, "
        "la modalidad real de funcionamiento del cinemómetro y el tratamiento metrológico efectivamente aplicado."
    )


def _velocity_verification_paragraph(intelligence: Dict[str, Any]) -> str:
    facts = (intelligence or {}).get("facts") or {}
    verification = facts.get("verification") or {}
    vdate = verification.get("date")
    relation = verification.get("relation_to_fact")
    fact_date = facts.get("fact_date")
    if vdate and relation == "after_fact":
        return (
            f"La lectura documental ha identificado una referencia expresa de verificación fechada el {vdate}, posterior a la fecha del hecho ({fact_date}). "
            "Ese dato debe ser contrastado con el certificado íntegro y, en particular, con la verificación que estuviera vigente el día de la medición. "
            "No basta una referencia posterior para acreditar por sí sola la situación metrológica del equipo en la fecha del hecho."
        )
    if vdate:
        return (
            f"La documentación contiene una referencia de verificación fechada el {vdate}. Debe aportarse el certificado íntegro correspondiente, "
            "con identificación del equipo y de sus componentes, alcance, clase de verificación, resultado y período de validez, a fin de comprobar su vigencia en la fecha del hecho."
        )
    return (
        "De la documentación analizada no puede extraerse de forma inequívoca una fecha asociada a la verificación metrológica del cinemómetro. "
        "Ello no permite afirmar que la verificación no exista; exige obtener el certificado que estuviera vigente el día del hecho y comprobar su correspondencia con el equipo y la antena identificados."
    )


def _velocity_capture_paragraph(intelligence: Dict[str, Any]) -> str:
    facts = (intelligence or {}).get("facts") or {}
    if not facts.get("capture_automatic"):
        return ""
    return (
        "La propia notificación indica que la captación fue automática. En consecuencia, la comprobación debe descansar en la integridad de la evidencia técnica: "
        "imagen o secuencia original, datos asociados a la captación, fecha y hora, ubicación, carril u objetivo asignado y trazabilidad entre el registro del cinemómetro y el vehículo denunciado. "
        "La mención a una captación automática no equivale, por sí sola, a tener por acreditada la integridad y correcta asignación de la evidencia."
    )


def build_velocity_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "[EXPEDIENTE]"
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."

    intelligence = core.get("_velocity_legal_intelligence") if isinstance(core.get("_velocity_legal_intelligence"), dict) else build_velocity_legal_intelligence(core)
    intel_facts = (intelligence or {}).get("facts") or {}

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
    radar_focus = radar_profile.get("attack_focus") or ""
    antenna = _safe_str(core.get("radar_antena")).strip()
    amount = core.get("sancion_importe_eur")
    points = core.get("puntos_detraccion")
    make_model = _safe_str(intel_facts.get("vehicle_make_model") or core.get("marca_modelo")).strip()

    tech_lines = []
    if measured:
        tech_lines.append(f"• Velocidad medida/consignada: {int(measured)} km/h")
    if limit:
        tech_lines.append(f"• Velocidad límite: {int(limit)} km/h")
    if radar:
        tech_lines.append(f"• Dispositivo de control: {radar}")
    if antenna and antenna not in radar:
        tech_lines.append(f"• Antena / unidad identificada: {antenna}")
    if make_model:
        tech_lines.append(f"• Marca / modelo del vehículo detectado: {make_model}")
    if amount not in (None, ""):
        try:
            amount_txt = f"{float(amount):.2f}".replace(".", ",")
        except Exception:
            amount_txt = _safe_str(amount)
        tech_lines.append(f"• Sanción económica consignada: {amount_txt} €")
    if points not in (None, ""):
        tech_lines.append(f"• Puntos a detraer consignados: {points}")
    if not (intel_facts.get("installation_mode") or {}).get("known"):
        tech_lines.append("• Modalidad de funcionamiento/instalación del cinemómetro: no identificada de forma inequívoca en la documentación analizada")
    article = intel_facts.get("normative_reference") or {}
    if article.get("article"):
        tech_lines.append(f"• Precepto indicado en la notificación: {article.get('norm') or 'norma de tráfico'}, art. {article.get('article')}")
    if conflict:
        tech_lines.append("• Observación: existen discrepancias numéricas que requieren validación con el expediente administrativo íntegro")

    tech_block = ""
    if tech_lines:
        tech_block = "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n" + "\n".join(tech_lines) + "\n\n"

    verification_paragraph = _velocity_verification_paragraph(intelligence)
    capture_paragraph = _velocity_capture_paragraph(intelligence)
    boundary_paragraph = _velocity_boundary_paragraph(core, measured, limit)

    boundary_section = f"{boundary_paragraph}\n\n" if boundary_paragraph else ""
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y TRAZABILIDAD DEL CINEMÓMETRO\n\n"
        "La imputación por exceso de velocidad exige una acreditación técnica completa, verificable y trazable del dispositivo utilizado. "
        "Debe constar la identificación exacta del cinemómetro y de sus componentes relevantes, el control metrológico vigente en la fecha de los hechos, "
        "la modalidad concreta de funcionamiento y la correspondencia entre el equipo, la captación y el vehículo denunciado. "
        f"{radar_focus}\n\n"
        f"{verification_paragraph}\n\n"
        "La Orden ICT/155/2020 distingue, a efectos metrológicos, entre cinemómetros fijos, estáticos y móviles en movimiento y establece errores máximos diferentes según la modalidad y la fase de control. "
        "Por ello, cuando la modalidad real no consta de forma inequívoca, no debe presumirse automáticamente un único margen ni una velocidad corregida concreta.\n\n"
        "La Sentencia del Tribunal Supremo 184/2018, de 17 de abril (ECLI:ES:TS:2018:1387), constituye un criterio jurisprudencial relevante sobre la necesidad de diferenciar entre mediciones desde ubicación fija o estática y mediciones efectuadas con el dispositivo en movimiento.\n\n"
        "Se interesa, en particular, la aportación y comprobación de:\n"
        "1) Identificación completa del cinemómetro, antena/unidad de captación y demás componentes relevantes.\n"
        "2) Certificado de verificación metrológica que estuviera vigente en la fecha del hecho, completo y legible.\n"
        "3) Modalidad concreta de funcionamiento o instalación en el momento de la captación.\n"
        "4) Imagen o secuencia original y datos técnicos asociados que permitan comprobar la asignación inequívoca al vehículo denunciado.\n"
        "5) Indicación de la velocidad captada, de la velocidad jurídicamente utilizada para sancionar y del tratamiento metrológico efectivamente aplicado.\n"
        "6) Trazabilidad entre el equipo, sus componentes, la evidencia registrada, la medición y la denuncia generada.\n\n"
        f"{tech_block}"
        "ALEGACIÓN SEGUNDA — UMBRAL SANCIONADOR Y MOTIVACIÓN DE LA VELOCIDAD JURÍDICAMENTE RELEVANTE\n\n"
        f"{boundary_section}"
    )

    cuerpo += (
        "La documentación debe permitir distinguir entre la lectura obtenida por el instrumento y la magnitud finalmente utilizada para subsumir el hecho en el tramo sancionador. "
        "La Administración debe explicar de forma individualizada la modalidad del cinemómetro, el tratamiento metrológico considerado y el resultado utilizado para imponer la concreta multa y detracción de puntos. "
        "La especial proximidad a un umbral sancionador refuerza la necesidad de una motivación técnica verificable.\n\n"
        "ALEGACIÓN TERCERA — CAPTACIÓN AUTOMÁTICA Y EVIDENCIA ORIGINAL\n\n"
        + (capture_paragraph if capture_paragraph else "Debe aportarse, cuando la denuncia descanse en evidencia técnica registrada, el soporte original y los datos necesarios para comprobar su integridad y asignación al vehículo denunciado.") +
        "\n\nALEGACIÓN CUARTA — PROPOSICIÓN DE PRUEBA Y ACCESO AL EXPEDIENTE ÍNTEGRO\n\n"
        "Al amparo del procedimiento sancionador ordinario, se solicita la incorporación y entrega de copia íntegra de la documentación técnica y probatoria necesaria para verificar los extremos anteriores. "
        "La eventual denegación de prueba pertinente deberá ser expresa y motivada."
    )

    return {"asunto": "ESCRITO DE ALEGACIONES", "cuerpo": cuerpo}

def _split_full_name_for_header(full_name: str) -> Dict[str, str]:
    """
    Divide nombre completo en campos simples para la cabecera DGT.
    No es perfecto, pero evita dejar la cabecera vacía cuando el dato viene
    del formulario de autorización.
    """
    name = re.sub(r"\s+", " ", _safe_str(full_name)).strip()
    if not name:
        return {}
    parts = name.split()
    if len(parts) == 1:
        return {"nombre": parts[0]}
    if len(parts) == 2:
        return {"nombre": parts[0], "apellido1": parts[1]}
    # En España lo más útil para cabecera es asumir los dos últimos como apellidos.
    return {
        "nombre": " ".join(parts[:-2]),
        "apellido1": parts[-2],
        "apellido2": parts[-1],
    }


def _normalize_interesado_for_resource(interesado: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normaliza los datos del formulario de autorización para que SIEMPRE tengan
    prioridad frente al OCR en la cabecera del recurso.
    """
    src = dict(interesado or {})
    out: Dict[str, Any] = {}

    def first(*keys):
        for k in keys:
            v = src.get(k)
            if v not in (None, "", [], {}):
                return v
        return ""

    full_name = _safe_str(first("full_name", "nombre_completo", "interesado", "titular")).strip()
    if full_name:
        out["full_name"] = full_name
        for k, v in _split_full_name_for_header(full_name).items():
            out.setdefault(k, v)

    dni = _safe_str(first("dni_nie", "dni", "documento_identidad")).strip().upper()
    if dni:
        out["dni_nie"] = dni
        out["dni"] = dni

    matricula = _safe_str(first("matricula", "plate", "vehicle_plate", "matricula_vehiculo")).strip().upper()
    if matricula:
        out["matricula"] = matricula

    domicilio = _safe_str(first("domicilio_notif", "domicilio", "direccion", "address")).strip()
    if domicilio:
        out["domicilio_notif"] = domicilio
        out["domicilio"] = domicilio
        cp = re.search(r"\b(\d{5})\b", domicilio)
        if cp:
            out["cp"] = cp.group(1)
            after = domicilio[cp.end():].strip(" ,.-")
            before = domicilio[:cp.start()].strip(" ,.-")
            # Si tras el CP viene LOCALIDAD + PROVINCIA, usamos el último token como provincia.
            words = [w for w in after.split() if w]
            if len(words) >= 2:
                out["provincia"] = words[-1]
                out["localidad"] = " ".join(words[:-1])
            elif len(words) == 1:
                out["localidad"] = words[0]
            # Evitar que el OCR meta la palabra MATRÍCULA como provincia/localidad.
            for key in ("provincia", "localidad"):
                if _safe_str(out.get(key)).strip().upper() in ("MATRÍCULA", "MATRICULA"):
                    out.pop(key, None)

    email = _safe_str(first("email", "contact_email")).strip()
    if email:
        out["email"] = email
    telefono = _safe_str(first("telefono", "phone", "teléfono")).strip()
    if telefono:
        out["telefono"] = telefono

    organismo = _safe_str(first("organismo", "organo", "órgano")).strip()
    if organismo:
        out["organismo"] = organismo
    expediente = _safe_str(first("expediente_ref", "numero_expediente", "expediente")).strip()
    if expediente:
        out["expediente_ref"] = expediente

    return out


def _load_interesado_from_case_for_generate(conn, case_id: str) -> Dict[str, Any]:
    """
    Carga datos fiables del formulario/case para no depender del OCR.
    """
    try:
        row = conn.execute(
            text(
                """
                SELECT
                    COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                    organismo,
                    expediente_ref,
                    contact_email
                FROM cases
                WHERE id = :id
                """
            ),
            {"id": case_id},
        ).fetchone()
    except Exception:
        return {}

    if not row:
        return {}
    data = row[0] if isinstance(row[0], dict) else {}
    data = dict(data or {})
    if row[1] and not data.get("organismo"):
        data["organismo"] = row[1]
    if row[2] and not data.get("expediente_ref"):
        data["expediente_ref"] = row[2]
    if row[3] and not data.get("email"):
        data["email"] = row[3]
    return data


def _merge_form_data_over_ocr(core: Dict[str, Any], form_data: Dict[str, Any]) -> Dict[str, Any]:
    """Fusiona formulario y extracción respetando la procedencia del dato.

    - Los datos personales del interesado proceden del formulario/autorización.
    - En una extracción RTM validada, los datos del expediente (matrícula, organismo y nº de expediente)
      proceden del documento y NO se pisan con valores antiguos guardados en cases.
    - Para extracciones legacy se conserva el comportamiento histórico.
    """
    merged = dict(core or {})
    clean = _normalize_interesado_for_resource(form_data)
    validated = _is_rtm_validated_extraction(core)

    personal_keys = [
        "full_name", "nombre", "apellido1", "apellido2",
        "dni", "dni_nie", "domicilio", "domicilio_notif", "localidad", "provincia", "cp",
        "email", "telefono",
    ]
    for key in personal_keys:
        if clean.get(key) not in (None, "", [], {}):
            merged[key] = clean[key]

    document_keys = ["matricula", "organismo", "expediente_ref"]
    for key in document_keys:
        value = clean.get(key)
        if value in (None, "", [], {}):
            continue
        if validated and merged.get(key) not in (None, "", [], {}):
            continue
        merged[key] = value

    for key in ("provincia", "localidad"):
        if _safe_str(merged.get(key)).strip().upper() in ("MATRÍCULA", "MATRICULA"):
            merged.pop(key, None)
    domicilio = _safe_str(merged.get("domicilio") or merged.get("domicilio_notif"))
    domicilio = re.sub(r"\bMATR[IÍ]CULA\b.*$", "", domicilio, flags=re.IGNORECASE).strip(" ,.-")
    if domicilio:
        merged["domicilio"] = domicilio
        merged["domicilio_notif"] = domicilio

    return merged

def build_v2_dgt_layout(cuerpo: str, core: Dict[str, Any], interesado: Dict[str, Any]) -> str:
    """Cabecera DGT/municipal/SCT con separación estricta entre datos personales y datos del expediente."""
    core = core or {}
    form = _normalize_interesado_for_resource(interesado or {})
    validated = _is_rtm_validated_extraction(core)

    merged = dict(core)
    protected_document_keys = {"matricula", "organismo", "expediente_ref"}
    for k, v in form.items():
        if v in (None, "", [], {}):
            continue
        if validated and k in protected_document_keys and merged.get(k) not in (None, "", [], {}):
            continue
        merged[k] = v

    def g(k: str, default: str = "") -> str:
        value = merged.get(k)
        if value in (None, "", [], {}):
            value = default
        value = str(value)
        if k in ("domicilio", "domicilio_notif"):
            value = re.sub(r"\bMATR[IÍ]CULA\b.*$", "", value, flags=re.IGNORECASE).strip(" ,.-")
        if k in ("provincia", "localidad") and value.strip().upper() in ("MATRÍCULA", "MATRICULA"):
            return default
        return value

    def _cleanup(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _infer_location_from_organismo() -> str:
        org = _cleanup(g("organismo", "") or g("organismo_cabecera", ""))
        if not org:
            return ""
        m = re.search(r"\b(?:AJUNTAMENT|AYUNTAMIENTO|POLIC[IÍ]A LOCAL|GUARDIA URBANA)\s+(?:DE|D['’])\s+(.+)$", org, flags=re.I)
        if m:
            loc = m.group(1).strip(" .,-")
            loc = re.sub(r"\bMATR[IÍ]CULA\b.*$", "", loc, flags=re.I).strip(" .,-")
            if loc:
                return loc.upper()
        if "terrassa" in org.lower():
            return "TERRASSA"
        return ""

    def _infer_provincia() -> str:
        # En SCT solo usamos territorio si figura en el documento, nunca el domicilio del recurrente.
        if _is_sct_organism(g("organismo", "")):
            return _infer_sct_territory(merged) or ""
        prov = _cleanup(g("provincia", ""))
        if prov and prov.upper() not in ("MATRÍCULA", "MATRICULA"):
            return prov.upper().replace("TRÁFICO DE", "").replace("TRAFICO DE", "").strip(" .,-")
        candidates = [
            _cleanup(g("organismo", "")), _cleanup(g("organismo_cabecera", "")),
            _cleanup(g("destination", "")), _cleanup(g("delivery_destination", "")),
        ]
        for cand in candidates:
            upper = cand.upper()
            for marker in ["JEFATURA PROVINCIAL DE TRÁFICO DE ", "JEFATURA PROVINCIAL DE TRAFICO DE "]:
                if marker in upper:
                    return upper.split(marker, 1)[1].strip(" .,-")
        loc = _infer_location_from_organismo()
        return loc or "........"

    def _build_destination_line() -> str:
        org = _cleanup(g("organismo", "") or g("organismo_cabecera", ""))
        upper = org.upper()
        loc = _infer_location_from_organismo()
        provincia = _infer_provincia()

        if _is_sct_organism(org):
            territory = _infer_sct_territory(merged)
            if territory:
                return f"AL/A LA JEFE/A DEL SERVICIO TERRITORIAL DE TRÁNSITO DE {territory}\nSERVEI CATALÀ DE TRÀNSIT"
            return "AL SERVEI CATALÀ DE TRÀNSIT"
        if "AJUNTAMENT" in upper:
            return f"A L'AJUNTAMENT DE {loc or provincia}"
        if "AYUNTAMIENTO" in upper:
            return f"AL AYUNTAMIENTO DE {loc or provincia}"
        if "POLICÍA LOCAL" in upper or "POLICIA LOCAL" in upper:
            return f"A LA POLICÍA LOCAL DE {loc or provincia}"
        if "GUARDIA URBANA" in upper:
            return f"A LA GUARDIA URBANA DE {loc or provincia}"
        if "DIRECCIÓN GENERAL DE TRÁFICO" in upper or "DIRECCION GENERAL DE TRAFICO" in upper:
            return f"A LA JEFATURA PROVINCIAL DE TRÁFICO DE {provincia}"
        if "JEFATURA PROVINCIAL DE TRÁFICO" in upper or "JEFATURA PROVINCIAL DE TRAFICO" in upper:
            return f"A LA JEFATURA PROVINCIAL DE TRÁFICO DE {provincia}"
        if "MINISTERIO DEL INTERIOR" in upper and "TRAFICO" in upper:
            return f"A LA JEFATURA PROVINCIAL DE TRÁFICO DE {provincia}"
        return f"A LA {org.upper()}" if org else "A LA AUTORIDAD COMPETENTE"

    def _strip_old_header(text: str) -> str:
        txt = str(text or "").replace("\r\n", "\n")
        txt = txt.replace("A la atención del órgano competente,", "")
        txt = txt.replace("A la atención del órgano competente", "")
        markers = ["Extracto literal del boletín:", "Extracto literal del boletin:", "I. ALEGACIONES"]
        for marker in markers:
            idx = txt.find(marker)
            if idx >= 0:
                return txt[idx:].lstrip()
        return txt.strip()

    body = _strip_duplicate_extractos(_strip_old_header(cuerpo))
    destino_line = _build_destination_line()

    header = f"""REFERENCIA: EXPTE. {g("expediente_ref", "........")}

ESCRITO DE ALEGACIONES

{destino_line}

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
DNI/NIE: {g("dni") or g("dni_nie")}

DOMICILIO: {g("domicilio") or g("domicilio_notif")}
LOCALIDAD: {g("localidad")}    PROVINCIA: {g("provincia")}    CP: {g("cp")}

TELÉFONO: {g("telefono")}
EMAIL: {g("email")}

3.- NATURALEZA DEL ESCRITO

[X] ESCRITO DE ALEGACIONES
[ ] RECURSO DE REPOSICIÓN

------------------------------------------------------------"""

    return header.strip() + "\n\n" + body.strip()

def _build_antecedentes_block(core: dict | None = None) -> str:
    """
    Bloque estándar de antecedentes y hecho imputado.
    SOLO mostrar literal OCR real. Nunca hechos canónicos IA.
    """
    core = core or {}

    organismo = str(core.get("organismo") or "Pendiente de identificación").strip()
    expediente = str(core.get("expediente_ref") or "[EXPEDIENTE]").strip()

    # SOLO texto OCR real
    literal_real = (
        str(core.get("hecho_denunciado_literal") or "").strip()
        or str(core.get("hecho_focus_literal") or "").strip()
    )

    # Nunca mostrar hechos canónicos IA como si fueran el boletín real
    canonical_blacklist = [
        "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN",
        "PRESUNTO EXCESO DE VELOCIDAD",
        "NO RESPETAR LA LUZ ROJA",
        "USO MANUAL DEL TELÉFONO MÓVIL",
    ]

    literal_upper = literal_real.upper()

    if (
        not literal_real
        or any(x in literal_upper for x in canonical_blacklist)
    ):
        literal_real = "[LECTURA MANUSCRITA PARCIAL / OCR PENDIENTE DE VALIDACIÓN]"

    return (
        "Extracto literal del boletín:\n"
        f"“{literal_real}”\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organismo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {literal_real}\n"
    )



def _build_fundamentos_derecho_pro(tipo: str = "", extra: dict | None = None) -> str:
    base = (
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los artículos 24 y 25 de la Constitución Española, "
        "que consagran las garantías de defensa, la presunción de inocencia y el principio de legalidad sancionadora.\n\n"
        "SEGUNDO.– Conforme a la Ley 39/2015, el interesado tiene derecho a conocer el expediente, formular alegaciones y proponer prueba, "
        "y la decisión administrativa debe apoyarse en hechos suficientemente acreditados y motivados.\n\n"
        "TERCERO.– En el procedimiento sancionador la Administración debe acreditar de forma suficiente los elementos constitutivos de la infracción "
        "y motivar la concreta subsunción jurídica y la consecuencia sancionadora aplicada."
    )
    if tipo == "velocidad":
        intel = extra or {}
        band = ((intel.get("sanction_boundary") or {}).get("band") or {}) if isinstance(intel, dict) else {}
        boundary_text = ""
        if band and band.get("is_lower_boundary") and band.get("previous"):
            prev = band.get("previous") or {}
            boundary_text = (
                f" Para el límite de {band.get('limit')} km/h analizado, el Anexo IV sitúa hasta {prev.get('upper')} km/h en el tramo de "
                f"{prev.get('fine')} euros y {prev.get('points')} puntos, y desde {band.get('lower')} km/h en el de {band.get('fine')} euros y {band.get('points')} puntos."
            )
        return base + (
            "\n\nCUARTO.– El artículo 95 del texto refundido de la Ley sobre Tráfico, Circulación de Vehículos a Motor y Seguridad Vial reconoce, "
            "en el procedimiento ordinario, el derecho a formular alegaciones y proponer o aportar pruebas; la denegación de la práctica de pruebas pertinentes debe ser motivada.\n\n"
            "QUINTO.– El Anexo IV de la Ley de Tráfico establece el cuadro de sanciones y puntos por exceso de velocidad."
            + boundary_text +
            "\n\nSEXTO.– El Anexo XII de la Orden ICT/155/2020 regula el control metrológico de los cinemómetros, distingue modalidades de instalación/uso y establece una verificación periódica anual, "
            "con errores máximos permitidos diferenciados según la modalidad y la fase de control.\n\n"
            "SÉPTIMO.– La STS 184/2018, de 17 de abril (ECLI:ES:TS:2018:1387), es un criterio jurisprudencial relevante para distinguir la medición desde ubicación fija o estática de la efectuada con el dispositivo en movimiento."
        )
    if tipo == "semaforo":
        intel = extra or {}
        precept = (intel.get("document_precept_analysis") or {}) if isinstance(intel, dict) else {}
        rule = _safe_str(precept.get("legal_rule_at_fact_date")).strip()
        precept_note = ""
        if precept.get("requires_review") and rule:
            precept_note = (
                "\n\nSÉPTIMO.– La notificación contiene una referencia normativa que debe contrastarse con la regulación vigente "
                f"en la fecha del hecho. La regla material identificada para la luz roja se sitúa en {rule}. "
                "La eventual discordancia debe ser objeto de motivación y revisión de la subsunción, sin convertirla por sí sola en nulidad automática."
            )
        return base + (
            "\n\nCUARTO.– El artículo 76.k) del texto refundido de la Ley sobre Tráfico califica como infracción grave no respetar la luz roja de un semáforo; "
            "el artículo 80 fija con carácter general 200 euros para las infracciones graves y el Anexo II asigna 4 puntos a la no detención ante semáforo en rojo.\n\n"
            "QUINTO.– El artículo 94 regula el pago voluntario con reducción del 50 por ciento, cuando proceda.\n\n"
            "SEXTO.– Los artículos 77 y 88 de la Ley 39/2015 exigen valorar la prueba y motivar la resolución sancionadora; "
            "cuando el hecho se conoce mediante medios de captación de imágenes, debe poder verificarse la evidencia que sustenta la imputación."
            + precept_note
        )
    return base + (
        "\n\nCUARTO.– La prueba y motivación han de ser suficientes, concretas e individualizadas para desvirtuar la presunción de inocencia."
    )


def _build_suplica_pro(tipo: str = "", extra: dict | None = None) -> str:
    if tipo == "velocidad":
        intel = extra or {}
        band = ((intel.get("sanction_boundary") or {}).get("band") or {}) if isinstance(intel, dict) else {}
        prev = band.get("previous") or {}
        recal = (
            f"6) Que, si de la prueba técnica resulta que la velocidad jurídicamente sancionable no alcanza {band.get('lower')} km/h, "
            f"se rectifique la calificación y se aplique el tramo que legalmente corresponda; en el supuesto analizado, el tramo anterior finaliza en {prev.get('upper')} km/h, "
            f"con {prev.get('fine')} euros y {prev.get('points')} puntos.\n\n"
            if band and prev else
            "6) Que, si de la prueba técnica resulta una velocidad jurídicamente sancionable inferior a la utilizada para imponer la sanción, se rectifique la calificación y se aplique el tramo legalmente procedente.\n\n"
        )
        return (
            "S U P L I C A:\n\n"
            "1) Que se tengan por formuladas en tiempo y forma las presentes alegaciones y por propuesta la prueba relacionada.\n\n"
            "2) Que se incorpore al expediente el certificado metrológico que estuviera vigente en la fecha de los hechos, completo, legible y referido al equipo y componentes identificados.\n\n"
            "3) Que se identifique la modalidad concreta de funcionamiento del cinemómetro en el momento de la medición y el tratamiento metrológico efectivamente aplicado.\n\n"
            "4) Que se aporte la imagen o secuencia original de la captación y los datos técnicos necesarios para comprobar su integridad, fecha, hora, ubicación y asignación inequívoca al vehículo.\n\n"
            "5) Que se aclare y documente qué velocidad fue captada por el instrumento y cuál fue la velocidad jurídicamente utilizada para sancionar, con explicación del cálculo o corrección aplicada.\n\n"
            + recal +
            "7) Que, si no se acredita de forma suficiente la vigencia metrológica del equipo, la trazabilidad de la captación, la modalidad de funcionamiento o la determinación de la velocidad sancionable, se acuerde el archivo del expediente.\n\n"
            "8) Subsidiariamente, que cualquier denegación de la prueba propuesta y cualquier decisión desestimatoria respondan de forma expresa, individualizada y motivada a las cuestiones planteadas.\n\n"
            "OTROSÍ DIGO\n\n"
            "Que esta parte solicita acceso a la documentación técnica y probatoria que vaya a constituir fundamento esencial de la resolución y se reserva el ejercicio de los recursos y acciones que correspondan."
        )
    return (
        "S U P L I C A:\n\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n\n"
        "2) Que, en atención a las alegaciones presentadas y sus fundamentos, se acuerde el ARCHIVO DEL EXPEDIENTE por insuficiencia probatoria, falta de acreditación suficiente del hecho imputado o ausencia de motivación individualizada.\n\n"
        "3) Subsidiariamente, para el caso de no estimarse el archivo, que se proceda a una correcta recalificación jurídica de los hechos conforme a la prueba realmente acreditada en el expediente.\n\n"
        "4) Subsidiariamente, que se imponga en su caso la sanción mínima legalmente procedente dentro del tipo infractor que finalmente pudiera considerarse aplicable.\n\n"
        "5) Subsidiariamente, que se aporte expediente íntegro y prueba completa para contradicción efectiva.\n\n"
        "OTROSÍ DIGO\n\n"
        "Que esta parte se reserva expresamente el ejercicio de cuantos recursos administrativos y acciones legales pudieran corresponder en defensa de sus derechos e intereses legítimos."
    )


def _upgrade_legacy_suplica_to_pro(text: str, tipo: str = "", extra: dict | None = None) -> str:
    if not text:
        return text
    fundamentos = _build_fundamentos_derecho_pro(tipo=tipo, extra=extra)
    suplica = _build_suplica_pro(tipo=tipo, extra=extra)
    cierre_pro = fundamentos + "\n\n" + suplica

    # En V10 se reemplaza siempre el cierre previo para VELOCIDAD y así evitar que sobreviva una súplica genérica.
    patterns = [
        r"\nFUNDAMENTOS DE DERECHO[\s\S]*$",
        r"\nIII\.\s*SOLICITO[\s\S]*$",
        r"\nIII\.\s*S O L I C I T O[\s\S]*$",
        r"\nSOLICITO[\s\S]*$",
        r"\nS U P L I C A\s*:[\s\S]*$",
        r"\nSUPLICA\s*:[\s\S]*$",
    ]
    for pat in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            return re.sub(pat, "\n\n" + cierre_pro, text, flags=re.IGNORECASE).strip()
    return (text.rstrip() + "\n\n" + cierre_pro).strip()



def _identity_fold(value: Any) -> str:
    txt = unicodedata.normalize("NFKD", _safe_str(value))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^0-9A-Za-z ]+", " ", txt).upper()
    return re.sub(r"\s+", " ", txt).strip()


def _identity_tokens(value: Any) -> set[str]:
    return {x for x in _identity_fold(value).split() if len(x) >= 2}


def _normalize_traffic_location(value: Any) -> str:
    """Normaliza ubicaciones de carretera sin inventar datos.

    Convierte formas ya extraídas como 'AP-7, 204,6' o 'AP-7 / PK 204,6'
    a 'AP-7, p.k. 204,6'. Si no reconoce el patrón, conserva el texto.
    """
    txt = re.sub(r"\s+", " ", _safe_str(value)).strip()
    if not txt:
        return ""

    m = re.match(r"^([A-Z]{1,5}-\d{1,4})\s*[,;/]\s*(?:P\.?\s*K\.?\s*)?(\d{1,4}(?:[.,]\d+)?)$", txt, flags=re.I)
    if m:
        road = m.group(1).upper()
        km = m.group(2).replace(".", ",") if "." in m.group(2) and "," not in m.group(2) else m.group(2)
        return f"{road}, p.k. {km}"

    m = re.match(r"^([A-Z]{1,5}-\d{1,4})\s+P\.?\s*K\.?\s*(\d{1,4}(?:[.,]\d+)?)$", txt, flags=re.I)
    if m:
        return f"{m.group(1).upper()}, p.k. {m.group(2)}"

    return txt


def _speed_identity_check(speed_intelligence: Dict[str, Any], interesado: Dict[str, Any]) -> Dict[str, Any]:
    """Compara identidad documental de la sanción con identidad declarada en RTM.

    No cambia el recurrente ni bloquea la generación del borrador. Solo crea una
    alerta de OPS cuando la evidencia documental es suficientemente confiable.
    """
    facts = (speed_intelligence or {}).get("facts") or {}
    doc_subject = facts.get("document_subject") or {}
    provenance = (speed_intelligence or {}).get("provenance") or {}
    conf_map = provenance.get("secondary_facts_confidence") or {}
    try:
        subject_conf = float(conf_map.get("document_subject") or 0)
    except Exception:
        subject_conf = 0.0

    doc_name = _safe_str(doc_subject.get("full_name")).strip()
    doc_id = re.sub(r"[^0-9A-Za-z]", "", _safe_str(doc_subject.get("id_number"))).upper()

    client_name = _safe_str((interesado or {}).get("full_name")).strip()
    if not client_name:
        client_name = " ".join(
            x for x in [
                _safe_str((interesado or {}).get("nombre")).strip(),
                _safe_str((interesado or {}).get("apellido1")).strip(),
                _safe_str((interesado or {}).get("apellido2")).strip(),
            ] if x
        ).strip()
    client_id = re.sub(
        r"[^0-9A-Za-z]", "",
        _safe_str((interesado or {}).get("dni_nie") or (interesado or {}).get("dni"))
    ).upper()

    reasons = []
    id_match = None
    name_match = None
    name_overlap = None

    if doc_id and client_id and subject_conf >= 0.75:
        id_match = bool(doc_id == client_id)
        if not id_match:
            reasons.append("document_id_differs_from_rtm_client")

    if doc_name and client_name and subject_conf >= 0.85:
        dset = _identity_tokens(doc_name)
        cset = _identity_tokens(client_name)
        if dset and cset:
            overlap = len(dset & cset) / max(1, len(dset | cset))
            name_overlap = round(overlap, 3)
            name_match = bool(overlap >= 0.50)
            if overlap < 0.25:
                reasons.append("document_name_differs_from_rtm_client")

    mismatch = bool(reasons)
    return {
        "status": "mismatch" if mismatch else ("consistent" if (id_match is True or name_match is True) else "not_enough_data"),
        "mismatch": mismatch,
        "reasons": reasons,
        "document_subject": {
            "full_name": doc_name or None,
            "id_number": doc_id or None,
            "confidence": subject_conf,
        },
        "rtm_client": {
            "full_name": client_name or None,
            "id_number": client_id or None,
        },
        "id_match": id_match,
        "name_match": name_match,
        "name_token_overlap": name_overlap,
        "action": (
            "Revisar identidad antes de cualquier presentación. No fusionar automáticamente datos del documento con los del cliente."
            if mismatch else None
        ),
    }


def _apply_speed_identity_check(speed_intelligence: Dict[str, Any], interesado: Dict[str, Any]) -> Dict[str, Any]:
    intel = dict(speed_intelligence or {})
    check = _speed_identity_check(intel, interesado or {})
    intel["identity_check"] = check

    if check.get("mismatch"):
        issues = list(intel.get("issues") or [])
        if not any(x.get("code") == "DOCUMENT_IDENTITY_MISMATCH" for x in issues if isinstance(x, dict)):
            issues.append({
                "code": "DOCUMENT_IDENTITY_MISMATCH",
                "severity": "high",
                "message": (
                    "La identidad que figura en la documentación sancionadora no coincide con la identidad declarada "
                    "para el cliente de RTM. Debe revisarse antes de cualquier presentación."
                ),
            })
        intel["issues"] = issues

        reasons = list(intel.get("operator_review_reasons") or [])
        if "DOCUMENT_IDENTITY_MISMATCH" not in reasons:
            reasons.append("DOCUMENT_IDENTITY_MISMATCH")
        intel["operator_review_reasons"] = reasons
        intel["requires_operator_review"] = True

    return intel


def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None, forced_tipo: Optional[str] = None) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    # Intelligence CORE: una extracción moderna normalmente debe venir lista.
    # Excepción controlada: SEMÁFORO validado mantiene ready_for_generate=false hasta
    # que exista el especialista jurídico; desde V20 Generate puede tomar el relevo
    # si la extracción de semáforo está completa y versionada.
    unresolved = core.get("unresolved_critical_fields") or []
    legacy_missing_required = (
        (core.get("critical_fields_validation") or {}).get("missing_required") or []
    )

    specialist_hint = _safe_str(
        core.get("specialist_dispatch")
        or core.get("familia_resuelta")
        or core.get("tipo_infraccion")
    ).lower().strip()

    # SEMÁFORO: desde V19 la extracción especializada ya valida sus propios
    # campos. No debemos bloquear Generate por un missing_required heredado
    # de una extracción base anterior.
    semaforo_required_fields = [
        "expediente_ref",
        "organismo",
        "matricula",
        "fecha_infraccion",
        "lugar_infraccion",
        "hecho_imputado",
        "sancion_importe_eur",
        "puntos_detraccion",
    ]
    semaforo_missing_required = [
        key
        for key in semaforo_required_fields
        if core.get(key) in (None, "", [], {})
    ]

    semaforo_secondary_version = _safe_str(
        core.get("semaforo_secondary_facts_version")
    ).strip()

    semaforo_specialist_handoff = bool(
        specialist_hint == "semaforo"
        and semaforo_secondary_version.startswith("semaforo_secondary_v1_")
        and not unresolved
        and not semaforo_missing_required
    )

    # Para semáforo validado, manda su validación especializada actual.
    # Para el resto de familias, conservamos el guard histórico.
    effective_missing_required = (
        semaforo_missing_required
        if specialist_hint == "semaforo"
        else legacy_missing_required
    )

    if (
        (core.get("ready_for_generate") is False and not semaforo_specialist_handoff)
        or unresolved
        or effective_missing_required
    ):
        details = []
        if unresolved:
            details.append("conflictos: " + ", ".join(map(str, unresolved)))
        if effective_missing_required:
            details.append(
                "faltan: " + ", ".join(map(str, effective_missing_required))
            )
        suffix = (" (" + "; ".join(details) + ")") if details else ""
        raise HTTPException(
            status_code=409,
            detail=(
                "La extracción requiere validación antes de generar el recurso"
                + suffix
            ),
        )

    # Datos del formulario: prioridad para identidad/contacto; los hechos documentales validados se conservan.
    case_form_data = _load_interesado_from_case_for_generate(conn, case_id)
    if interesado:
        case_form_data.update(dict(interesado or {}))

    core = _enrich_core_with_person_fields(core)
    core = _merge_form_data_over_ocr(core, case_form_data)
    interesado = _normalize_interesado_for_resource(case_form_data)

    if (
        not core.get("hecho_denunciado_literal")
        and not core.get("hecho_para_recurso")
        and not core.get("hecho_imputado")
        and not core.get("hecho_denunciado_resumido")
    ):
        literal = extract_hecho_denunciado_literal(core)
        if literal and not _looks_like_ocr_header_not_fact(literal):
            core["hecho_denunciado_literal"] = literal

    tipo = forced_tipo or _resolved_tipo_from_core(core, fallback="generic")

    # Blindaje: no pisar una familia resuelta por analyze.py.
    # Semáforo solo se aplica como fallback cuando el tipo venga vacío/genérico
    # y el hecho principal contenga señales claras de luz roja/semáforo.
    current_tipo = _safe_str(tipo).lower().strip()
    if current_tipo in ("", "generic", "otro", "unknown", "desconocido") and _is_strong_semaforo_generation_case(core):
        tipo = "semaforo"
        core["tipo_infraccion"] = "semaforo"
        semaforo_hecho = _canonical_hecho_semaforo(core)
        if semaforo_hecho:
            core["hecho_imputado"] = semaforo_hecho
            core["hecho_denunciado_literal"] = semaforo_hecho
            core["hecho_denunciado_resumido"] = semaforo_hecho
            core["hecho_para_recurso"] = semaforo_hecho
    jurisdiccion = resolve_jurisdiction(core)

    speed_intelligence = None
    if tipo == "velocidad":
        speed_intelligence = build_velocity_legal_intelligence(core)
        speed_intelligence = _apply_speed_identity_check(speed_intelligence, interesado or {})
        core["_velocity_legal_intelligence"] = speed_intelligence
        detected_make = _safe_str(((speed_intelligence.get("facts") or {}).get("vehicle_make_model"))).strip()
        if detected_make and not _safe_str(core.get("marca_modelo")).strip():
            core["marca_modelo"] = detected_make

        # Presentación del lugar: conserva el dato validado y normaliza únicamente
        # la notación del punto kilométrico cuando el patrón es inequívoco.
        if _safe_str(core.get("lugar_infraccion")).strip():
            core["lugar_infraccion"] = _normalize_traffic_location(core.get("lugar_infraccion"))

        # Trazabilidad OPS: guarda la lectura jurídica estructurada que alimenta el borrador.
        try:
            conn.execute(
                text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:id, 'velocity_legal_intelligence_result', CAST(:payload AS JSONB), NOW())"),
                {"id": case_id, "payload": json.dumps(speed_intelligence, ensure_ascii=False)},
            )
        except Exception:
            pass

        # Alerta dedicada para OPS. No bloquea la generación del borrador interno,
        # pero deja explícito que jamás debe presentarse sin revisar la identidad.
        identity_check = speed_intelligence.get("identity_check") if isinstance(speed_intelligence, dict) else {}
        if isinstance(identity_check, dict) and identity_check.get("mismatch") is True:
            try:
                conn.execute(
                    text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:id, 'document_identity_mismatch', CAST(:payload AS JSONB), NOW())"),
                    {
                        "id": case_id,
                        "payload": json.dumps({
                            "ok": False,
                            "severity": "high",
                            "message": "La identidad del documento sancionador no coincide con la identidad del cliente RTM. Revisar antes de cualquier presentación.",
                            "identity_check": identity_check,
                            "generator_version": _GENERATOR_VERSION,
                        }, ensure_ascii=False),
                    },
                )
            except Exception:
                pass

    semaforo_intelligence = None
    if tipo == "semaforo":
        semaforo_intelligence = build_semaforo_legal_intelligence(core)
        semaforo_intelligence = _apply_speed_identity_check(semaforo_intelligence, interesado or {})
        core["_semaforo_legal_intelligence"] = semaforo_intelligence

        if not semaforo_intelligence.get("draft_generation_allowed"):
            raise HTTPException(
                status_code=409,
                detail="La inteligencia jurídica de semáforo requiere validar datos antes de generar el borrador."
            )

        # Trazabilidad OPS del especialista jurídico.
        try:
            conn.execute(
                text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:id, 'semaforo_legal_intelligence_result', CAST(:payload AS JSONB), NOW())"),
                {"id": case_id, "payload": json.dumps(semaforo_intelligence, ensure_ascii=False)},
            )
        except Exception:
            pass

        identity_check = (
            semaforo_intelligence.get("identity_check")
            if isinstance(semaforo_intelligence, dict)
            else {}
        )
        if isinstance(identity_check, dict) and identity_check.get("mismatch") is True:
            try:
                conn.execute(
                    text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:id, 'document_identity_mismatch', CAST(:payload AS JSONB), NOW())"),
                    {
                        "id": case_id,
                        "payload": json.dumps({
                            "ok": False,
                            "severity": "high",
                            "message": "La identidad del documento sancionador no coincide con la identidad del cliente RTM. Revisar antes de cualquier presentación.",
                            "identity_check": identity_check,
                            "generator_version": _GENERATOR_VERSION,
                            "specialist": "semaforo",
                        }, ensure_ascii=False),
                    },
                )
            except Exception:
                pass

    bicicleta_ctx = _is_bicicleta_context(core)

    # V5 bloqueada: no redispatch heurístico si ya hay familia resuelta upstream.
    tpl, final_kind = _select_template(core, tipo, jurisdiccion)

    tpl = ensure_tpl_dict(tpl, core)

    cuerpo = tpl.get("cuerpo") or ""
    if tipo == "atencion" and bicicleta_ctx:
        cuerpo = _sanitize_bicicleta_body(cuerpo)

    cuerpo = _inject_tipicidad_material_en_alegaciones(cuerpo, core)
    # Intelligence CORE: en VELOCIDAD validada no inyectamos la estrategia legacy,
    # porque podía introducir nulidad/fotograma/margen como afirmaciones genéricas.
    if not (tipo in ("velocidad", "semaforo") and _is_rtm_validated_extraction(core)):
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
    # Limpieza final después de insertar extracto y refuerzos: evita duplicados
    # de "Extracto literal del boletín" y deja títulos homogéneos.
    cuerpo = _clean_final_resource_body(cuerpo)
    cuerpo = _fix_alegacion_titles(cuerpo)
    tpl["cuerpo"] = fix_roman_headings(cuerpo)

    if tipo == "velocidad":
        tpl["cuerpo"] = tpl["cuerpo"].replace(
            "La imputación por exceso de velocidad exige acreditación técnica completa y verificable.",
            "La imputación por exceso de velocidad exige acreditación técnica completa y verificable. Tal como ha reiterado el Tribunal Supremo, la validez de los medios técnicos de control de velocidad exige una acreditación completa, verificable y trazable del dispositivo utilizado."
        )

    tpl["cuerpo"] = build_v2_dgt_layout(tpl["cuerpo"], core, interesado or {})

    # Antecedentes y hecho imputado SIEMPRE visibles.
    antecedentes_block = _build_antecedentes_block(core)

    if "I. ALEGACIONES" in tpl["cuerpo"] and "I. ANTECEDENTES" not in tpl["cuerpo"]:
        tpl["cuerpo"] = tpl["cuerpo"].replace(
            "I. ALEGACIONES",
            antecedentes_block + "\nI. ALEGACIONES",
            1,
        )
    elif "I. ANTECEDENTES" not in tpl["cuerpo"]:
        tpl["cuerpo"] = antecedentes_block + "\n\n" + tpl["cuerpo"]

    # Cierre PRO obligatorio para todos los recursos:
    # recalificación subsidiaria, sanción mínima y OTROSÍ DIGO.
    tpl["cuerpo"] = _upgrade_legacy_suplica_to_pro(
        tpl["cuerpo"],
        tipo=tipo,
        extra=(speed_intelligence or semaforo_intelligence or {}),
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
            "kind_docx": "recurso_docx",
            "kind_pdf": "recurso_pdf",
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
        "generator_version": _GENERATOR_VERSION,
        "legal_intelligence_version": (
            ((speed_intelligence or semaforo_intelligence or {}).get("version"))
            if isinstance((speed_intelligence or semaforo_intelligence), dict) else None
        ),
        "secondary_facts_version": (
            (((speed_intelligence or semaforo_intelligence or {}).get("provenance") or {}).get("secondary_facts_version"))
            if isinstance((speed_intelligence or semaforo_intelligence), dict) else None
        ),
        "legal_intelligence": (
            speed_intelligence if tipo == "velocidad"
            else semaforo_intelligence if tipo == "semaforo"
            else None
        ),
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
        if upper.startswith("AL/A LA JEFE/A DEL SERVICIO TERRITORIAL DE TRÁNSITO DE "):
            return clean
        if upper.startswith("AL SERVEI CATALÀ DE TRÀNSIT") or upper.startswith("AL SERVEI CATALA DE TRANSIT"):
            return clean
        if upper.startswith("A LA JEFATURA PROVINCIAL DE TRÁFICO DE "):
            return clean
        if upper.startswith("A LA JEFATURA PROVINCIAL DE TRAFICO DE "):
            return clean
        if upper.startswith("A LA DIRECCIÓN GENERAL DE TRÁFICO") or upper.startswith("A LA DIRECCION GENERAL DE TRAFICO"):
            return clean
        if upper.startswith("AL AYUNTAMIENTO DE ") or upper.startswith("A L'AJUNTAMENT DE "):
            return clean
        if upper.startswith("A LA POLICÍA LOCAL DE ") or upper.startswith("A LA POLICIA LOCAL DE "):
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
