from __future__ import annotations

import hashlib
import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

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
except Exception:  # pragma: no cover - entorno sin Pillow
    Image = None
    ImageOps = None


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
    """Detecta el tipo real por firma binaria antes de confiar en MIME/extensión."""
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

    # DOCX es un ZIP. Solo lo tratamos como DOCX con indicio suficiente para no
    # confundir otros ZIP que pudieran aparecer en el expediente.
    if head.startswith(b"PK\x03\x04") and (
        key_l.endswith(".docx")
        or declared == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if declared and declared != "application/octet-stream":
        return declared

    # Último intento: Pillow puede reconocer formatos de imagen aunque el MIME
    # y la extensión se hayan perdido al almacenar el original.
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

    # Si el key es .bin o no tiene extensión útil, generamos un nombre coherente
    # solo para el análisis. El original de B2 no se modifica.
    root, ext = os.path.splitext(base)
    if not ext or ext.lower() == ".bin":
        return f"pagina_{index}{wanted}"
    return base


def _normalize_image_for_analysis(content: bytes, mime: str) -> Tuple[bytes, str]:
    """Crea una copia de análisis JPEG sin tocar el original guardado en B2.

    Corrige orientación EXIF y permite convertir TIFF/TIF u otros formatos que
    pueden no ser aceptados directamente por el proveedor de visión.
    """
    if not (mime or "").startswith("image/"):
        return content, mime
    if Image is None:
        return content, mime

    try:
        with Image.open(io.BytesIO(content)) as img:
            if ImageOps is not None:
                img = ImageOps.exif_transpose(img)
            if getattr(img, "n_frames", 1) > 1:
                # El flujo actual modela cada fichero como una página. Para TIFF
                # multipágina usamos la primera y dejamos el caso marcado en evento.
                try:
                    img.seek(0)
                except Exception:
                    pass
            img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=95, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:
        # Si no podemos normalizar, conservamos bytes/MIME detectados y dejamos
        # que el analizador especializado decida si puede procesarlos.
        return content, mime


def _page_text(core: Dict[str, Any]) -> str:
    for key in (
        "raw_text_blob",
        "vision_raw_text",
        "raw_text_vision",
        "raw_text_pdf",
    ):
        value = core.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _flatten_text(core or {}, text_content="")


def _consolidate_extraction(
    case_id: str,
    analyzed_pages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Inserta UNA extracción final que representa el documento lógico completo."""
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

        # Conservamos primero los valores ya detectados en páginas anteriores y
        # rellenamos huecos con páginas posteriores.
        combined_core = _merge_extracted(combined_core, core)

        text_page = _page_text(core)
        if text_page:
            raw_parts.append(
                f"===== PÁGINA {page['page_index']} =====\n{text_page}"
            )

        if page.get("document_id"):
            source_ids.append(str(page["document_id"]))
        if page.get("key"):
            source_keys.append(str(page["key"]))

    combined_blob = "\n\n".join(raw_parts).strip()
    if combined_blob:
        combined_core["raw_text_blob"] = combined_blob
        combined_core["vision_raw_text"] = combined_blob[:16000]

    # Esta segunda pasada determinista permite que la familia/hecho se resuelvan
    # usando conjuntamente el contenido de las dos (o más) páginas.
    combined_core = _enrich_with_triage(combined_core, combined_blob or _flatten_text(combined_core))
    combined_core["document_role"] = "primary_case_document"
    combined_core["document_group_type"] = "traffic_fine"
    combined_core["document_page_count"] = len(analyzed_pages)
    combined_core["source_document_ids"] = source_ids

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
                "model": "rtm_intelligence_core_v1+traffic_fine",
            },
        )

    return wrapper


def reanalyze_traffic_fine_case(case_id: str) -> Dict[str, Any]:
    """Reanaliza los originales YA almacenados de un expediente traffic/fine.

    No crea caso nuevo, no cobra, no modifica los originales de B2 y no elimina
    extracciones previas. Inserta al final una extracción consolidada que será la
    que consuma generate.py (consulta la extracción más reciente).
    """
    meta = _case_meta(case_id)
    if meta["department"] != "traffic" or meta["case_type"] not in _TRAFFIC_FINE_TYPES:
        raise HTTPException(
            status_code=409,
            detail=(
                "El reanálisis especializado v1 solo está habilitado para "
                "expedientes de Tráfico / Multa."
            ),
        )

    documents = _load_original_documents(case_id)
    if not documents:
        raise HTTPException(status_code=404, detail="El expediente no tiene documentos originales")

    _append_event(
        case_id,
        "case_reanalysis_started",
        {
            "engine": "rtm_intelligence_core_v1",
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
            analysis_content, analysis_mime = _normalize_image_for_analysis(content, mime_detected)
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
                    "wrapper": wrapper,
                    "confidence": 0.75,
                }
            )

        if not analyzed_pages:
            raise HTTPException(status_code=422, detail="Ningún original pudo ser reanalizado")

        consolidated = _consolidate_extraction(case_id, analyzed_pages)
        core = consolidated.get("extracted") or {}

        _append_event(
            case_id,
            "case_reanalysis_completed",
            {
                "ok": True,
                "engine": "rtm_intelligence_core_v1",
                "specialist": "traffic_fine",
                "pages_analyzed": len(analyzed_pages),
                "tipo_infraccion": core.get("tipo_infraccion"),
                "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
                "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
                "ready_for_generate": True,
            },
        )

        return {
            "ok": True,
            "case_id": case_id,
            "specialist": "traffic_fine",
            "pages_analyzed": len(analyzed_pages),
            "tipo_infraccion": core.get("tipo_infraccion"),
            "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
            "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
            "ready_for_generate": True,
            "message": "Reanálisis completado. La extracción consolidada ya está lista para Generate.",
        }

    except HTTPException as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": str(exc.detail), "status_code": exc.status_code},
        )
        raise
    except Exception as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": f"{type(exc).__name__}: {exc}"},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error reanalizando expediente: {type(exc).__name__}: {exc}",
        )
