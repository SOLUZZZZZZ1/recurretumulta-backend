from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException
from sqlalchemy import text

from database import get_engine
from b2_storage import download_bytes
from analyze import (
    analyze_existing_case_document,
    _enrich_with_triage,
    _flatten_text,
    _merge_extracted,
)

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None


_ENGINE_NAME = "rtm_intelligence_core_v1"
_EXTRACTOR_VERSION = "traffic_fine_reanalysis_v1_1"
_TRAFFIC_FINE_TYPES = {"fine", "multa", "multas", "sanction", "sancion", "sanción"}


def _append_event(case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
            ),
            {
                "case_id": case_id,
                "type": event_type,
                "payload": json.dumps(payload or {}, ensure_ascii=False),
            },
        )


def _case_meta(case_id: str) -> Dict[str, str]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COALESCE(department,''), COALESCE(case_type,''), "
                "COALESCE(status,''), COALESCE(payment_status,'') "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    return {
        "department": str(row[0] or "").strip().lower(),
        "case_type": str(row[1] or "").strip().lower(),
        "status": str(row[2] or "").strip(),
        "payment_status": str(row[3] or "").strip(),
    }


def _load_original_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, b2_bucket, b2_key, COALESCE(mime,''), "
                "COALESCE(size_bytes,0), created_at "
                "FROM documents "
                "WHERE case_id=:id AND kind='original' "
                "AND b2_bucket IS NOT NULL AND b2_key IS NOT NULL "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"id": case_id},
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "bucket": r[1],
            "key": r[2],
            "mime": str(r[3] or ""),
            "size_bytes": int(r[4] or 0),
            "created_at": str(r[5]),
        }
        for r in rows
    ]


def _sniff_mime(content: bytes, declared_mime: str = "", key: str = "") -> str:
    head = content[:32]
    declared = (declared_mime or "").strip().lower()
    key_l = (key or "").lower()

    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"

    if head.startswith(b"PK\x03\x04") and (
        key_l.endswith(".docx")
        or declared == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if declared and declared != "application/octet-stream":
        return declared

    if Image is not None:
        try:
            with Image.open(io.BytesIO(content)) as img:
                fmt = str(img.format or "").upper()
            return {
                "JPEG": "image/jpeg",
                "JPG": "image/jpeg",
                "PNG": "image/png",
                "TIFF": "image/tiff",
                "TIF": "image/tiff",
                "WEBP": "image/webp",
            }.get(fmt, declared or "application/octet-stream")
        except Exception:
            pass

    return declared or "application/octet-stream"


def _filename_for_mime(index: int, key: str, mime: str) -> str:
    base = os.path.basename(key or "") or f"pagina_{index}"
    ext_map = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tif",
        "image/webp": ".webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }
    wanted = ext_map.get(mime, "")
    _, ext = os.path.splitext(base)
    if not ext or ext.lower() == ".bin":
        return f"pagina_{index}{wanted}"
    return base


def _jpeg_bytes(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
    return out.getvalue()


def _data_url_jpeg(content: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")


def _choose_upright_landscape_image(img) -> Tuple[Any, Dict[str, Any]]:
    """Para imágenes apaisadas compara original + dos giros y pide a visión
    que elija cuál está realmente derecha. Si el selector falla, usa giro horario,
    que corrige el patrón observado en el caso patrón sin tocar el original de B2.
    """
    if img.width <= img.height:
        return img, {"rotation_applied": 0, "orientation_selector": "not_needed"}

    original = img
    clockwise = img.rotate(270, expand=True)
    counterclockwise = img.rotate(90, expand=True)

    meta: Dict[str, Any] = {
        "rotation_applied": 90,
        "orientation_selector": "fallback_clockwise",
        "orientation_confidence": 0.0,
    }

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return clockwise, meta

    try:
        model = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
        a = _jpeg_bytes(original)
        b = _jpeg_bytes(clockwise)
        c = _jpeg_bytes(counterclockwise)
        payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Eres un selector de orientación documental. "
                                "Recibirás tres versiones de la misma página administrativa española. "
                                "Indica cuál está derecha y se puede leer de arriba abajo sin estar invertida. "
                                "No analices el fondo jurídico. Devuelve JSON válido."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Las imágenes aparecen en orden A, B y C. "
                                "Devuelve exactamente: {\"upright\":\"A\"|\"B\"|\"C\",\"confidence\":0..1}."
                            ),
                        },
                        {"type": "input_image", "image_url": _data_url_jpeg(a)},
                        {"type": "input_image", "image_url": _data_url_jpeg(b)},
                        {"type": "input_image", "image_url": _data_url_jpeg(c)},
                    ],
                },
            ],
            "text": {"format": {"type": "json_object"}},
        }
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if r.ok:
            data = r.json()
            output_text = ""
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            output_text += part.get("text", "")
            obj = json.loads(output_text or "{}")
            choice = str(obj.get("upright") or "").strip().upper()
            try:
                confidence = float(obj.get("confidence") or 0)
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))

            if choice == "A":
                return original, {
                    "rotation_applied": 0,
                    "orientation_selector": "openai",
                    "orientation_confidence": confidence,
                }
            if choice == "B":
                return clockwise, {
                    "rotation_applied": 90,
                    "orientation_selector": "openai",
                    "orientation_confidence": confidence,
                }
            if choice == "C":
                return counterclockwise, {
                    "rotation_applied": -90,
                    "orientation_selector": "openai",
                    "orientation_confidence": confidence,
                }
    except Exception as exc:
        meta["orientation_selector_error"] = f"{type(exc).__name__}: {exc}"

    return clockwise, meta

def _normalize_image_for_analysis(content: bytes, mime: str) -> Tuple[bytes, str, Dict[str, Any]]:
    """Crea una copia JPEG para análisis, sin modificar el original de B2.

    - corrige EXIF;
    - convierte TIFF/PNG/WebP a JPEG;
    - si una foto de página llega girada 90°, la pone en vertical antes del OCR.
    """
    meta: Dict[str, Any] = {"rotation_applied": 0, "orientation_selector": "none"}
    if not (mime or "").startswith("image/"):
        return content, mime, meta
    if Image is None:
        return content, mime, meta

    try:
        with Image.open(io.BytesIO(content)) as source:
            img = source.copy()

        if ImageOps is not None:
            img = ImageOps.exif_transpose(img)

        if getattr(img, "n_frames", 1) > 1:
            try:
                img.seek(0)
            except Exception:
                pass

        img = img.convert("RGB")
        img, orientation_meta = _choose_upright_landscape_image(img)
        meta.update(orientation_meta)
        meta["normalized_width"] = int(img.width)
        meta["normalized_height"] = int(img.height)
        return _jpeg_bytes(img), "image/jpeg", meta
    except Exception as exc:
        meta["normalization_error"] = f"{type(exc).__name__}: {exc}"
        return content, mime, meta


def _page_text(core: Dict[str, Any]) -> str:
    for key in ("raw_text_blob", "vision_raw_text", "raw_text_vision", "raw_text_pdf"):
        value = core.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _flatten_text(core or {}, text_content="")


def _fold(text_value: str) -> str:
    s = unicodedata.normalize("NFKD", str(text_value or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[\t\r]+", " ", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s


def _first_match(patterns: List[str], text_value: str, flags: int = re.I | re.S) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text_value, flags)
        if m:
            value = str(m.group(1) or "").strip(" \t\r\n:;,.—–-")
            if value:
                return value
    return None


def _critical_fields_from_blob(text_blob: str) -> Dict[str, Any]:
    """Segunda capa determinista para campos críticos de una multa de tráfico.

    Solo corrige cuando existe una señal textual explícita (etiqueta o frase del
    hecho). Su función es impedir que un barcode, fecha accesoria o número aislado
    sustituya silenciosamente al dato jurídico principal.
    """
    raw = str(text_blob or "")
    folded = _fold(raw)
    upper = folded.upper()
    out: Dict[str, Any] = {}

    if "SERVEI CATALA DE TRANSIT" in upper:
        out["organismo"] = "Servei Català de Trànsit"

    expediente = _first_match(
        [
            r"NUMERO\s+D[' ]EXPEDIENT\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"NUMERO\s+DE\s+EXPEDIENTE\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"N[ºO]\.?\s*EXPEDIENTE\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"EXPEDIENTE\s+SANCIONADOR\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
        ],
        upper,
    )
    if expediente:
        out["expediente_ref"] = expediente

    matricula = _first_match(
        [
            r"MATRICULA\s*[:#-]?\s*(\d{4}\s*[A-Z]{3})\b",
            r"MATRICULA\s*[:#-]?\s*([A-Z]{1,2}\s*\d{4}\s*[A-Z]{1,2})\b",
        ],
        upper,
    )
    if matricula:
        out["matricula"] = re.sub(r"\s+", " ", matricula).strip()

    # Frase fuerte: "velocitat de 121 km/h ... limitada ... a 90 km/h"
    speed_pair = re.search(
        r"(?:VELOCITAT|VELOCIDAD)\s+(?:MESURADA\s+)?(?:DE|A)?\s*(\d{2,3})\s*KM\s*/?\s*H"
        r".{0,220}?(?:LIMITAD[AOA]*|LIMIT\w*)"
        r".{0,100}?(?:A|DE)\s*(\d{2,3})\s*KM\s*/?\s*H",
        upper,
        flags=re.S,
    )
    if not speed_pair:
        speed_pair = re.search(
            r"(?:CIRCULAR|CIRCULABA|CIRCULANT|CIRCULANDO).{0,100}?"
            r"(?:VELOCITAT|VELOCIDAD).{0,40}?(\d{2,3})\s*KM\s*/?\s*H"
            r".{0,220}?(?:LIMITAD[AOA]*|LIMIT\w*).{0,100}?(\d{2,3})\s*KM\s*/?\s*H",
            upper,
            flags=re.S,
        )
    if speed_pair:
        measured = int(speed_pair.group(1))
        limit = int(speed_pair.group(2))
        if 10 <= measured <= 250 and 10 <= limit <= 200 and measured > limit:
            out["velocidad_medida_kmh"] = measured
            out["velocidad_limite_kmh"] = limit
            out["speed_pair_source"] = "explicit_fact_sentence"

    radar = _first_match(
        [
            r"CINEMOMETR[EO].{0,30}?((?:MULTI?RADAR|MULTARADAR|MULTANOVA)[ -]?[A-Z0-9-]*)",
            r"\b((?:MULTI?RADAR|MULTARADAR|MULTANOVA)[ -]?[A-Z0-9-]*)\b",
        ],
        upper,
    )
    if radar:
        radar_clean = re.sub(r"\s+", " ", radar).strip(" .,-")
        # Forma legible del modelo más frecuente, sin hardcodear el caso.
        radar_clean = re.sub(r"^MULTIRADAR\s*[- ]?\s*C$", "MULTIRADAR C", radar_clean, flags=re.I)
        radar_clean = re.sub(r"^MULTARADAR\s*[- ]?\s*C$", "MULTIRADAR C", radar_clean, flags=re.I)
        out["radar_modelo_hint"] = radar_clean

    antena = _first_match(
        [
            r"N[UÚ]M\.?\s*D[' ]ANTENA\s*[:#-]?\s*(\d{3,10})",
            r"ANTENA\s*(?:NUMERO|N[UÚ]M\.?)?\s*[:#-]?\s*(\d{3,10})",
        ],
        upper,
    )
    if antena:
        out["radar_antena"] = antena

    importe = _first_match(
        [
            r"IMPORT(?:E)?\s+DE\s+LA\s+SANCIO[NÓ]?\s*[:#-]?\s*(\d{2,4}(?:[.,]\d{1,2})?)\s*(?:EUR|€)",
            r"IMPORT\s+DE\s+LA\s+SANCIO\s*[:#-]?\s*(\d{2,4}(?:[.,]\d{1,2})?)\s*(?:EUR|€)",
        ],
        upper,
    )
    if importe:
        try:
            out["sancion_importe_eur"] = float(importe.replace(",", "."))
        except Exception:
            pass

    points = _first_match(
        [
            r"PUNTS\s+A\s+DETREURE\s*[:#-]?\s*(\d)",
            r"PUNTOS\s+A\s+DETRAER\s*[:#-]?\s*(\d)",
            r"DETRACCION\s+DE\s*(\d)\s*PUNTOS",
        ],
        upper,
    )
    if points:
        try:
            out["puntos_detraccion"] = int(points)
        except Exception:
            pass

    via = _first_match(
        [r"VIA\s*/\s*CARRER\s*[:#-]?\s*([A-Z]{1,4}-?\d{1,4})"],
        upper,
    )
    km = _first_match(
        [r"KM\s*/\s*N[UÚ]M\.?\s*[:#-]?\s*(\d{1,4}(?:[.,]\d{1,2})?)"],
        upper,
    )
    if via and km:
        out["lugar_infraccion"] = f"{via}, p.k. {km}"
    elif via:
        out["lugar_infraccion"] = via

    # Fecha/hora: preferimos un valor próximo al bloque de vía/km para evitar
    # confundir fecha de notificación, pago o certificado con fecha del hecho.
    if via:
        idx = upper.find(via.upper())
        window = upper[max(0, idx - 120): idx + 500] if idx >= 0 else upper[:1000]
        m_date = re.search(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b", window)
        m_time = re.search(r"\b([0-2]\d:[0-5]\d)\b", window)
        if m_date:
            out["fecha_infraccion"] = m_date.group(1)
        if m_time:
            out["hora_infraccion"] = m_time.group(1)

    return out


def _same_value(a: Any, b: Any) -> bool:
    if a in (None, "") or b in (None, ""):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    na = re.sub(r"\s+", " ", _fold(str(a))).strip().upper()
    nb = re.sub(r"\s+", " ", _fold(str(b))).strip().upper()
    return na == nb


def _apply_critical_fields(core: Dict[str, Any], text_blob: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    out = dict(core or {})
    deterministic = _critical_fields_from_blob(text_blob)
    conflicts: List[Dict[str, Any]] = []

    override_fields = {
        "organismo",
        "expediente_ref",
        "matricula",
        "velocidad_medida_kmh",
        "velocidad_limite_kmh",
        "radar_modelo_hint",
        "radar_antena",
        "sancion_importe_eur",
        "puntos_detraccion",
        "lugar_infraccion",
        "fecha_infraccion",
        "hora_infraccion",
    }

    for key, value in deterministic.items():
        if key not in override_fields:
            continue
        old = out.get(key)
        if old not in (None, "", [], {}) and not _same_value(old, value):
            conflicts.append({"field": key, "ai_value": old, "deterministic_value": value})
        out[key] = value

    measured = out.get("velocidad_medida_kmh")
    limit = out.get("velocidad_limite_kmh")
    if isinstance(measured, (int, float)) and isinstance(limit, (int, float)) and measured > limit:
        out["tipo_infraccion"] = "velocidad"
        out["familia_resuelta"] = "velocidad"
        radar = str(out.get("radar_modelo_hint") or "").strip()
        hecho = f"Circular a {int(measured)} km/h en un tramo limitado a {int(limit)} km/h"
        if radar:
            hecho += f", medición efectuada mediante {radar}"
        out["hecho_imputado"] = hecho
        out["hecho_para_recurso"] = hecho
        out["tipo_infraccion_confidence"] = max(float(out.get("tipo_infraccion_confidence") or 0), 0.99)

    out["critical_fields_deterministic"] = deterministic
    out["critical_field_conflicts"] = conflicts
    out["critical_fields_validation"] = {
        "extractor_version": _EXTRACTOR_VERSION,
        "conflicts_detected": len(conflicts),
        "conflicts_resolved_by_explicit_text": bool(conflicts),
    }

    if conflicts:
        out["requires_operator_review"] = True
        reasons = list(out.get("operator_review_reasons") or [])
        if "critical_fields_corrected_from_explicit_document_text" not in reasons:
            reasons.append("critical_fields_corrected_from_explicit_document_text")
        out["operator_review_reasons"] = reasons

    return out, {"deterministic": deterministic, "conflicts": conflicts}


def _consolidate_extraction(case_id: str, analyzed_pages: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not analyzed_pages:
        raise HTTPException(status_code=422, detail="No se pudo analizar ningún documento original")

    combined_core: Dict[str, Any] = {}
    raw_parts: List[str] = []
    source_ids: List[str] = []
    source_keys: List[str] = []

    for page in analyzed_pages:
        wrapper = page.get("wrapper") or {}
        core = wrapper.get("extracted") or {}
        if not isinstance(core, dict):
            core = {}

        combined_core = _merge_extracted(combined_core, core)

        text_page = _page_text(core)
        if text_page:
            raw_parts.append(f"===== PÁGINA {page['page_index']} =====\n{text_page}")

        if page.get("document_id"):
            source_ids.append(str(page["document_id"]))
        if page.get("key"):
            source_keys.append(str(page["key"]))

    combined_blob = "\n\n".join(raw_parts).strip()
    if combined_blob:
        combined_core["raw_text_blob"] = combined_blob
        combined_core["vision_raw_text"] = combined_blob[:16000]

    combined_core = _enrich_with_triage(combined_core, combined_blob or _flatten_text(combined_core))
    combined_core, critical_meta = _apply_critical_fields(combined_core, combined_blob)
    combined_core["document_role"] = "primary_case_document"
    combined_core["document_group_type"] = "traffic_fine"
    combined_core["document_page_count"] = len(analyzed_pages)
    combined_core["source_document_ids"] = source_ids
    combined_core["extractor_version"] = _EXTRACTOR_VERSION

    wrapper = {
        "filename": "multa_consolidada",
        "mime": "application/x-rtm-logical-document",
        "size_bytes": sum(int(p.get("size_bytes") or 0) for p in analyzed_pages),
        "sha256": hashlib.sha256(
            "|".join(str(p.get("sha256") or "") for p in analyzed_pages).encode("utf-8")
        ).hexdigest(),
        "storage": {
            "type": "document_group",
            "source_document_ids": source_ids,
            "source_keys": source_keys,
        },
        "document_group": {
            "role": "primary_case_document",
            "type": "traffic_fine",
            "page_count": len(analyzed_pages),
        },
        "pages": [
            {
                "page_index": p["page_index"],
                "document_id": p.get("document_id"),
                "mime_detected": p.get("mime_detected"),
                "analysis_mime": p.get("analysis_mime"),
                "key": p.get("key"),
                "orientation": p.get("orientation"),
            }
            for p in analyzed_pages
        ],
        "extracted": combined_core,
    }

    confidence_values: List[float] = []
    for p in analyzed_pages:
        try:
            conf = float(p.get("confidence") or 0)
            if conf > 0:
                confidence_values.append(conf)
        except Exception:
            pass
    confidence = max(confidence_values) if confidence_values else 0.75
    if critical_meta.get("deterministic", {}).get("speed_pair_source") == "explicit_fact_sentence":
        confidence = max(confidence, 0.90)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO extractions(case_id, extracted_json, confidence, model, created_at) "
                "VALUES (:id, CAST(:payload AS JSONB), :confidence, :model, NOW())"
            ),
            {
                "id": case_id,
                "payload": json.dumps(wrapper, ensure_ascii=False),
                "confidence": confidence,
                "model": f"{_ENGINE_NAME}+traffic_fine+v1_1",
            },
        )

    return wrapper, critical_meta


def reanalyze_traffic_fine_case(case_id: str) -> Dict[str, Any]:
    """Reanaliza los originales YA almacenados de un expediente traffic/fine.

    No crea un caso nuevo, no cobra y no modifica los originales de B2.
    Inserta una extracción consolidada que será la última consumida por Generate.
    """
    meta = _case_meta(case_id)
    if meta["department"] != "traffic" or meta["case_type"] not in _TRAFFIC_FINE_TYPES:
        raise HTTPException(
            status_code=409,
            detail="El reanálisis especializado v1 solo está habilitado para expedientes de Tráfico / Multa.",
        )

    documents = _load_original_documents(case_id)
    if not documents:
        raise HTTPException(status_code=404, detail="El expediente no tiene documentos originales")

    _append_event(
        case_id,
        "case_reanalysis_started",
        {
            "engine": _ENGINE_NAME,
            "extractor_version": _EXTRACTOR_VERSION,
            "specialist": "traffic_fine",
            "original_documents": len(documents),
        },
    )

    analyzed_pages: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    try:
        for index, doc in enumerate(documents, start=1):
            content = download_bytes(doc["bucket"], doc["key"])
            if not content:
                _append_event(
                    case_id,
                    "case_reanalysis_document_skipped",
                    {"document_id": doc["id"], "reason": "empty_b2_object"},
                )
                continue

            sha256 = hashlib.sha256(content).hexdigest()
            if sha256 in seen_sha256:
                _append_event(
                    case_id,
                    "case_reanalysis_document_skipped",
                    {"document_id": doc["id"], "reason": "duplicate_sha256", "sha256": sha256},
                )
                continue
            seen_sha256.add(sha256)

            mime_detected = _sniff_mime(content, doc.get("mime") or "", doc.get("key") or "")
            analysis_content, analysis_mime, orientation_meta = _normalize_image_for_analysis(content, mime_detected)
            analysis_filename = _filename_for_mime(index, doc.get("key") or "", analysis_mime)

            _append_event(
                case_id,
                "case_reanalysis_document_detected",
                {
                    "document_id": doc["id"],
                    "page_index": index,
                    "declared_mime": doc.get("mime") or "",
                    "detected_mime": mime_detected,
                    "analysis_mime": analysis_mime,
                    "analysis_filename": analysis_filename,
                    "original_key": doc.get("key"),
                    "orientation": orientation_meta,
                },
            )

            result = analyze_existing_case_document(
                case_id=case_id,
                content=analysis_content,
                filename=analysis_filename,
                mime=analysis_mime,
                b2_bucket=doc["bucket"],
                b2_key=doc["key"],
            )
            wrapper = result.get("extracted") or {}

            analyzed_pages.append(
                {
                    "page_index": index,
                    "document_id": doc["id"],
                    "bucket": doc["bucket"],
                    "key": doc["key"],
                    "size_bytes": len(content),
                    "sha256": sha256,
                    "mime_detected": mime_detected,
                    "analysis_mime": analysis_mime,
                    "orientation": orientation_meta,
                    "wrapper": wrapper,
                    "confidence": 0.75,
                }
            )

        if not analyzed_pages:
            raise HTTPException(status_code=422, detail="Ningún original pudo ser reanalizado")

        consolidated, critical_meta = _consolidate_extraction(case_id, analyzed_pages)
        core = consolidated.get("extracted") or {}
        deterministic = critical_meta.get("deterministic") or {}
        conflicts = critical_meta.get("conflicts") or []

        event_payload = {
            "ok": True,
            "engine": _ENGINE_NAME,
            "extractor_version": _EXTRACTOR_VERSION,
            "specialist": "traffic_fine",
            "pages_analyzed": len(analyzed_pages),
            "tipo_infraccion": core.get("tipo_infraccion"),
            "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
            "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
            "expediente_ref": core.get("expediente_ref"),
            "matricula": core.get("matricula"),
            "velocidad_medida_kmh": core.get("velocidad_medida_kmh"),
            "velocidad_limite_kmh": core.get("velocidad_limite_kmh"),
            "radar_modelo_hint": core.get("radar_modelo_hint"),
            "radar_antena": core.get("radar_antena"),
            "sancion_importe_eur": core.get("sancion_importe_eur"),
            "puntos_detraccion": core.get("puntos_detraccion"),
            "lugar_infraccion": core.get("lugar_infraccion"),
            "fecha_infraccion": core.get("fecha_infraccion"),
            "critical_fields_detected": deterministic,
            "critical_conflicts_resolved": conflicts,
            "requires_operator_review": bool(core.get("requires_operator_review")),
            "ready_for_generate": True,
        }
        _append_event(case_id, "case_reanalysis_completed", event_payload)

        return {
            "ok": True,
            "case_id": case_id,
            "specialist": "traffic_fine",
            "pages_analyzed": len(analyzed_pages),
            "tipo_infraccion": core.get("tipo_infraccion"),
            "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
            "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
            "expediente_ref": core.get("expediente_ref"),
            "matricula": core.get("matricula"),
            "velocidad_medida_kmh": core.get("velocidad_medida_kmh"),
            "velocidad_limite_kmh": core.get("velocidad_limite_kmh"),
            "radar_modelo_hint": core.get("radar_modelo_hint"),
            "radar_antena": core.get("radar_antena"),
            "sancion_importe_eur": core.get("sancion_importe_eur"),
            "puntos_detraccion": core.get("puntos_detraccion"),
            "lugar_infraccion": core.get("lugar_infraccion"),
            "fecha_infraccion": core.get("fecha_infraccion"),
            "requires_operator_review": bool(core.get("requires_operator_review")),
            "critical_conflicts_resolved": conflicts,
            "ready_for_generate": True,
            "message": "Reanálisis completado. Extracción consolidada y validada contra texto explícito.",
        }

    except HTTPException as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": str(exc.detail), "status_code": exc.status_code, "extractor_version": _EXTRACTOR_VERSION},
        )
        raise
    except Exception as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": f"{type(exc).__name__}: {exc}", "extractor_version": _EXTRACTOR_VERSION},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error reanalizando expediente: {type(exc).__name__}: {exc}",
        )
