# files.py — enlaces firmados (presigned) para Backblaze B2, con control de pago
import os
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from b2_storage import get_s3_client
from database import get_engine

router = APIRouter(prefix="/files", tags=["files"])

@router.get("/presign")
def presign(
    case_id: str = Query(...),
    bucket: str = Query(...),
    key: str = Query(...),
    expires: int = Query(900, ge=60, le=3600),
):
    """
    Devuelve URL firmada SOLO si:
    - el documento pertenece a ese case_id
    - y el caso está pagado (payment_status='paid') para documentos generados
    """
    try:
        engine = get_engine()

        with engine.begin() as conn:
            # 1) Verificar que el documento existe y pertenece al expediente
            doc = conn.execute(
                text("""
                    SELECT d.kind, c.payment_status
                    FROM documents d
                    JOIN cases c ON c.id = d.case_id
                    WHERE d.case_id = :case_id
                      AND d.b2_bucket = :bucket
                      AND d.b2_key = :key
                    LIMIT 1
                """),
                {"case_id": case_id, "bucket": bucket, "key": key},
            ).fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="Documento no encontrado para este expediente.")

            kind = doc[0] or ""
            payment_status = doc[1] or ""

            # 2) Bloquear descargas de documentos generados si no está pagado
            if kind.startswith("generated_") and payment_status != "paid":
                raise HTTPException(status_code=402, detail="Pago requerido para descargar el recurso.")

        # 3) Generar URL firmada
        s3 = get_s3_client()
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=int(expires),
        )
        return {"ok": True, "url": url}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error presign: {e}")
