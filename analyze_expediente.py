# analyze_expediente.py — subida múltiple (hasta 5) + creación de expediente
import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, UploadFile, File
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

router = APIRouter(tags=["analyze"])

MAX_FILES = 5


def _safe_filename(name: str) -> str:
    return (name or "documento").replace("\\", "_").replace("/", "_")[:120]


@router.post("/analyze/expediente")
async def analyze_expediente(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """
    MVP multi-documento:
    - Crea un case_id
    - Sube hasta 5 archivos a B2 (folder: original)
    - Inserta documents(kind='original') para cada archivo
    - Inserta event 'expediente_uploaded' con lista de documentos
    """
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FILES} documentos por expediente.")

    engine = get_engine()

    # 1) Crear caso
    with engine.begin() as conn:
        row = conn.execute(
            text("INSERT INTO cases(status, updated_at) VALUES ('uploaded', NOW()) RETURNING id")
        ).fetchone()
        case_id = str(row[0])

    uploaded_docs = []

    # 2) Subir y registrar documents
    for idx, uf in enumerate(files, start=1):
        filename = _safe_filename(uf.filename or f"documento_{idx}")
        data = await uf.read()
        if not data:
            continue

        ext = ".bin"
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
            if len(ext) > 8:
                ext = ".bin"

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "original",
            data,
            ext,
            (uf.content_type or "application/octet-stream"),
        )

        uploaded_docs.append({
            "idx": idx,
            "filename": filename,
            "bucket": b2_bucket,
            "key": b2_key,
            "mime": uf.content_type or "application/octet-stream",
            "size_bytes": len(data),
        })

        with engine.begin() as conn:
            conn.execute(
                text(
                    """INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                       VALUES (:case_id, 'original', :b2_bucket, :b2_key, :mime, :size_bytes, NOW())"""
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "mime": uf.content_type or "application/octet-stream",
                    "size_bytes": len(data),
                },
            )

    # 3) Evento + update case
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (:case_id, 'expediente_uploaded', CAST(:payload AS JSONB), NOW())"""
            ),
            {"case_id": case_id, "payload": json.dumps({"documents": uploaded_docs})},
        )
        conn.execute(
            text("UPDATE cases SET status='uploaded', updated_at=NOW() WHERE id=:case_id"),
            {"case_id": case_id},
        )

    return {
        "ok": True,
        "case_id": case_id,
        "documents": uploaded_docs,
        "message": "Expediente creado. Ya puedes continuar al resumen.",
    }
