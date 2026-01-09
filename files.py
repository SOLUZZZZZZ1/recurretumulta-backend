# files.py — enlaces firmados (presigned) para Backblaze B2
from fastapi import APIRouter, HTTPException, Query
from b2_storage import get_s3_client

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/presign")
def presign(
    bucket: str = Query(...),
    key: str = Query(...),
    expires: int = Query(900, ge=60, le=3600),  # 15 min por defecto
):
    try:
        s3 = get_s3_client()

        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=int(expires),
        )

        return {"ok": True, "url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error presign: {e}")
