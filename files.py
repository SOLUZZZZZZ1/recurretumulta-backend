# files.py — enlaces firmados (presigned) para Backblaze B2, con control de pago
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response
from sqlalchemy import text

from b2_storage import get_s3_client, validate_b2_object_coordinate
from database import get_engine
from public_case_access import require_case_access_token

router = APIRouter(prefix="/files", tags=["files"])


_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Vary": "X-RTM-Case-Token",
    "X-Content-Type-Options": "nosniff",
}


def _private_http_exception(exc: HTTPException) -> HTTPException:
    headers = dict(exc.headers or {})
    headers.update(_NO_STORE_HEADERS)
    return HTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
        headers=headers,
    )


@router.get("/presign")
def presign(
    response: Response,
    case_id: str = Query(...),
    document_id: str = Query(...),
    expires: int = Query(300, ge=60, le=300),
    x_case_token: str | None = Header(
        default=None,
        alias="X-RTM-Case-Token",
    ),
):
    """
    Devuelve URL firmada SOLO si:
    - la capability autoriza ese case_id
    - document_id pertenece a ese expediente
    - no es una revisión externa custodiada por Presenter
    - y el caso está pagado (payment_status='paid') para documentos generados

    Bucket y key se resuelven exclusivamente en servidor.
    """
    response.headers.update(_NO_STORE_HEADERS)
    try:
        case_id = require_case_access_token(case_id, x_case_token)
        try:
            document_id = str(UUID(document_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(
                status_code=404,
                detail="Documento no encontrado para este expediente.",
            ) from exc

        engine = get_engine()

        with engine.begin() as conn:
            # Verificar el scope y resolver las coordenadas solo en servidor.
            doc = conn.execute(
                text("""
                    SELECT d.kind, c.payment_status, d.b2_bucket, d.b2_key
                    FROM documents d
                    JOIN cases c ON c.id = d.case_id
                    WHERE d.case_id = :case_id
                      AND d.id = CAST(:document_id AS UUID)
                      AND COALESCE(d.kind, '') <> 'external_revision'
                    LIMIT 1
                """),
                {"case_id": case_id, "document_id": document_id},
            ).fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="Documento no encontrado para este expediente.")

            kind = doc[0] or ""
            payment_status = doc[1] or ""
            bucket = doc[2]
            key = doc[3]

            if not bucket or not key:
                raise HTTPException(
                    status_code=409,
                    detail="El documento no tiene custodia disponible.",
                )
            bucket, key = validate_b2_object_coordinate(
                bucket,
                key,
                case_id=case_id,
            )

            # Bloquear descargas de documentos generados si no está pagado.
            if kind.startswith("generated_") and payment_status != "paid":
                raise HTTPException(status_code=402, detail="Pago requerido para descargar el recurso.")

        # La URL es efímera y nunca expone las coordenadas en la petición pública.
        filename = "".join(
            ch if ch.isalnum() or ch in "._-" else "_"
            for ch in str(key).rsplit("/", 1)[-1]
        ).strip("._")[:120] or "documento.bin"
        s3 = get_s3_client()
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=int(expires),
        )
        return {"ok": True, "url": url}

    except HTTPException as exc:
        raise _private_http_exception(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo preparar el acceso protegido al documento.",
            headers=_NO_STORE_HEADERS,
        ) from exc
