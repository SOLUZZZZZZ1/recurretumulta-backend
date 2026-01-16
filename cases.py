import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

# Import interno del engine (Modo Dios)
from ai.expediente_engine import run_expediente_ai

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5


# =========================
# MODELOS
# =========================
class CaseDetailsIn(BaseModel):
    full_name: str = Field(..., description="Nombre y apellidos")
    dni_nie: str = Field(..., description="DNI/NIE")
    domicilio_notif: str = Field(..., description="Domicilio notificaciones")
    email: EmailStr
    telefono: Optional[str] = None


# =========================
# HELPERS
# =========================
def _case_exists(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT id, status, payment_status, authorized, interested_data
            FROM cases
            WHERE id=:id
            """
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")
    return {
        "id": str(row[0]),
        "status": row[1],
        "payment_status": row[2],
        "authorized": bool(row[3]),
        "interested_data": row[4] or {},
    }


def _event(case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
                """
            ),
            {"case_id": case_id, "type": typ, "payload": json.dumps(payload)},
        )


# =========================
# DATOS DEL INTERESADO
# =========================
@router.post("/{case_id}/details")
def save_case_details(case_id: str, data: CaseDetailsIn):
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = CAST(:payload AS JSONB),
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id, "payload": json.dumps(data.dict())},
        )

    _event(case_id, "details_saved", {"fields": list(data.dict().keys())})
    return {"ok": True}


@router.post("/{case_id}/authorize")
def authorize_case(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        if not meta["interested_data"]:
            raise HTTPException(status_code=400, detail="Faltan los datos del interesado")

        if meta["authorized"]:
            return {"ok": True, "authorized": True}

        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = NOW(),
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        )

    _event(case_id, "authorized", {"authorized_to": "LA TALAMANQUINA, S.L."})
    return {"ok": True, "authorized": True}


# =========================
# AÑADIR DOCUMENTOS AL EXPEDIENTE
# =========================
@router.post("/{case_id}/append-documents")
async def append_documents(case_id: str, files: List[UploadFile] = File(...)):
    """
    Añade documentos al mismo expediente (case_id).
    - Sube a B2 (folder: original)
    - Inserta documents(kind='original')
    - Evento 'expediente_documents_appended'
    """
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_APPEND_FILES} documentos por subida.")

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

    uploaded_docs = []
    for idx, uf in enumerate(files, start=1):
        data = await uf.read()
        if not data:
            continue

        filename = (uf.filename or f"doc_{idx}").replace("/", "_").replace("\\", "_")
        ext = ".bin"
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
            if len(ext) > 8:
                ext = ".bin"

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "original",
            data,
            ext=ext,
            content_type=(uf.content_type or "application/octet-stream"),
        )

        uploaded_docs.append(
            {
                "filename": filename,
                "bucket": b2_bucket,
                "key": b2_key,
                "mime": uf.content_type or "application/octet-stream",
                "size_bytes": len(data),
            }
        )

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:case_id, 'original', :b, :k, :m, :s, NOW())
                    """
                ),
                {
                    "case_id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": uf.content_type or "application/octet-stream",
                    "s": len(data),
                },
            )

    # Estado: volvemos a "uploaded" (hay nuevo material)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status='uploaded', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

    _event(case_id, "expediente_documents_appended", {"documents": uploaded_docs})
    return {"ok": True, "case_id": case_id, "added": uploaded_docs}


# =========================
# REVISIÓN PREVIA (ANTES DE COBRAR)
# =========================
@router.post("/{case_id}/review")
def review_case(case_id: str):
    """
    Ejecuta la revisión (Modo Dios interno) y fija el estado de UX:
    - Si NOT_ADMISSIBLE / esperar_resolucion_final => status = pending_documents
    - Si ADMISSIBLE => status = ready_to_pay
    Guarda el resultado en events.
    """
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

    result = run_expediente_ai(case_id)  # guarda ai_expediente_result en events

    admiss = (result.get("admissibility", {}) or {}).get("admissibility")
    action = (result.get("phase", {}) or {}).get("recommended_action", {}).get("action")

    # Por defecto
    new_status = "uploaded"

    if (admiss or "").upper() == "ADMISSIBLE":
        new_status = "ready_to_pay"
    else:
        # Si recomienda esperar resolución final → pendiente documentación
        if (action or "").lower() in ("esperar_resolucion_final", "wait_final_resolution"):
            new_status = "pending_documents"
        else:
            new_status = "pending_documents"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status=:st, updated_at=NOW() WHERE id=:id"),
            {"st": new_status, "id": case_id},
        )

    _event(case_id, "case_reviewed", {"status": new_status, "admissibility": admiss, "action": action})
    return {"ok": True, "case_id": case_id, "status": new_status, "admissibility": admiss, "action": action}


@router.get("/{case_id}/public-status")
def public_status(case_id: str):
    """
    Estado que consume el frontend SIN mencionar IA:
    - pending_documents: falta documento (no se cobra)
    - ready_to_pay: recurso puede presentarse ahora (se permite pagar)
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, payment_status, authorized
                FROM cases
                WHERE id=:id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

    status = row[0] or "uploaded"
    payment_status = row[1] or ""
    authorized = bool(row[2])

    # Mensajes UX (sin IA)
    if status == "pending_documents":
        msg = "Aún no se puede presentar el recurso. Falta documentación o el acto recurrible."
    elif status == "ready_to_pay":
        msg = "Tu recurso puede presentarse ahora."
    else:
        msg = "Expediente en revisión."

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "payment_status": payment_status,
        "authorized": authorized,
        "message": msg,
    }
