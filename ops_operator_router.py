from datetime import datetime, timezone
import json
import os
from typing import Optional, Any, Dict

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from generate import GenerateRequest, generate_dgt
from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf

router = APIRouter(prefix="/ops/cases", tags=["ops-operator"])


def _utcnow():
    return datetime.now(timezone.utc)


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")
    return token


class ApproveBody(BaseModel):
    note: Optional[str] = None


class ManualBody(BaseModel):
    motivo: str = Field(..., min_length=3)


class NoteBody(BaseModel):
    note: str = Field(..., min_length=1)


class OverrideFamilyBody(BaseModel):
    familia: str = Field(..., min_length=1)
    motivo: str = Field(..., min_length=3)


class OverrideAndRegenerateBody(BaseModel):
    familia: str = Field(..., min_length=1)
    motivo: str = Field(..., min_length=3)


class RewriteHechoBody(BaseModel):
    hecho: str = Field(..., min_length=5)
    motivo: str = Field(..., min_length=3)
    familia: Optional[str] = None


class SubmitDGTBody(BaseModel):
    document_url: Optional[str] = None
    force: bool = False


class SaveAiOverridesBody(BaseModel):
    familia: Optional[str] = None
    hecho: Optional[str] = None
    motivo: str = Field(..., min_length=3)


class FinalResourceBody(BaseModel):
    content: str = Field(..., min_length=1)
    created_by: Optional[str] = None


class SendCompleteBody(BaseModel):
    destination: Optional[str] = None
    channel: str = "ops"
    note: Optional[str] = None


def _case_or_404(conn, case_id: str):
    row = conn.execute(
        text(
            '''
            SELECT id, status, updated_at
            FROM cases
            WHERE id = :id
            '''
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return {
        "id": str(row[0]),
        "status": row[1] or "pending_review",
        "updated_at": row[2],
    }


def _get_status(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row[0] or "pending_review"


def _set_status(conn, case_id: str, status: str):
    conn.execute(
        text(
            '''
            UPDATE cases
            SET status = :status, updated_at = NOW()
            WHERE id = :id
            '''
        ),
        {"id": case_id, "status": status},
    )


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            '''
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            '''
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}),
        },
    )


def _load_interesado(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row or not row[0]:
        return {}
    data = row[0]
    return data if isinstance(data, dict) else {}


def _save_ai_overrides_in_interested_data(
    conn,
    case_id: str,
    *,
    familia: Optional[str] = None,
    hecho: Optional[str] = None,
    motivo: Optional[str] = None,
):
    current = _load_interesado(conn, case_id)
    current = dict(current or {})

    ai_overrides = dict(current.get("ai_overrides") or {})
    if familia is not None:
        ai_overrides["familia"] = familia
        current["manual_family"] = familia
    if hecho is not None:
        ai_overrides["hecho"] = hecho
        current["manual_hecho_denunciado"] = hecho
    if motivo is not None:
        ai_overrides["motivo"] = motivo
        current["manual_hecho_motivo"] = motivo

    ai_overrides["saved_at"] = _utcnow().isoformat()
    current["ai_overrides"] = ai_overrides

    conn.execute(
        text(
            '''
            UPDATE cases
            SET interested_data = CAST(:data AS JSONB),
                updated_at = NOW()
            WHERE id = :id
            '''
        ),
        {"id": case_id, "data": json.dumps(current)},
    )

    return ai_overrides




def _stringify_generate_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _prepare_interesado_for_generate(interesado: Dict[str, Any]) -> Dict[str, str]:
    raw = dict(interesado or {})
    prepared: Dict[str, str] = {}
    for key, value in raw.items():
        key_str = str(key).strip()
        if not key_str:
            continue
        val_str = _stringify_generate_value(value)
        if val_str is not None:
            prepared[key_str] = val_str
    return prepared


def _load_ai_overrides(conn, case_id: str) -> Dict[str, Any]:
    interesado = _load_interesado(conn, case_id)
    overrides = dict((interesado or {}).get("ai_overrides") or {})

    familia = overrides.get("familia") or (interesado or {}).get("manual_family")
    hecho = overrides.get("hecho") or (interesado or {}).get("manual_hecho_denunciado")
    motivo = overrides.get("motivo") or (interesado or {}).get("manual_hecho_motivo")
    saved_at = overrides.get("saved_at")

    return {
        "familia": familia,
        "hecho": hecho,
        "motivo": motivo,
        "saved_at": saved_at,
    }



def _next_final_resource_version(conn, case_id: str) -> int:
    row = conn.execute(
        text("SELECT COALESCE(MAX(version), 0) + 1 FROM ops_final_resources WHERE case_id = :id"),
        {"id": case_id},
    ).fetchone()
    return int(row[0] or 1)


def _latest_final_resource(conn, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            '''
            SELECT id, content, version, is_final, created_by, created_at, updated_at
            FROM ops_final_resources
            WHERE case_id = :id
            ORDER BY version DESC, updated_at DESC
            LIMIT 1
            '''
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        return None
    return {
        "id": str(row[0]),
        "content": row[1] or "",
        "version": int(row[2] or 1),
        "is_final": bool(row[3]),
        "created_by": row[4] or "",
        "created_at": row[5],
        "updated_at": row[6],
    }


@router.get("/{case_id}/final-resource")
def get_final_resource(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        resource = _latest_final_resource(conn, case_id)
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": resource,
    }


@router.post("/{case_id}/final-resource")
def save_final_resource_draft(
    case_id: str,
    body: FinalResourceBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="El recurso no puede estar vacío")

    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        version = _next_final_resource_version(conn, case_id)

        row = conn.execute(
            text(
                '''
                INSERT INTO ops_final_resources(case_id, content, version, is_final, created_by, created_at, updated_at)
                VALUES (:case_id, :content, :version, FALSE, :created_by, NOW(), NOW())
                RETURNING id, created_at, updated_at
                '''
            ),
            {
                "case_id": case_id,
                "content": content,
                "version": version,
                "created_by": (body.created_by or "operator").strip() or "operator",
            },
        ).fetchone()

        _append_event(
            conn,
            case_id,
            "ops_final_resource_draft_saved",
            {
                "resource_id": str(row[0]),
                "version": version,
                "chars": len(content),
                "created_by": (body.created_by or "operator").strip() or "operator",
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": {
            "id": str(row[0]),
            "content": content,
            "version": version,
            "is_final": False,
            "created_by": (body.created_by or "operator").strip() or "operator",
            "created_at": row[1],
            "updated_at": row[2],
        },
    }


@router.post("/{case_id}/finalize-resource")
def finalize_resource(
    case_id: str,
    body: FinalResourceBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="El recurso final no puede estar vacío")

    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        version = _next_final_resource_version(conn, case_id)

        conn.execute(
            text("UPDATE ops_final_resources SET is_final = FALSE, updated_at = NOW() WHERE case_id = :id"),
            {"id": case_id},
        )

        row = conn.execute(
            text(
                '''
                INSERT INTO ops_final_resources(case_id, content, version, is_final, created_by, created_at, updated_at)
                VALUES (:case_id, :content, :version, TRUE, :created_by, NOW(), NOW())
                RETURNING id, created_at, updated_at
                '''
            ),
            {
                "case_id": case_id,
                "content": content,
                "version": version,
                "created_by": (body.created_by or "operator").strip() or "operator",
            },
        ).fetchone()

        created_by = (body.created_by or "operator").strip() or "operator"

        txt_bytes = content.encode("utf-8")
        docx_bytes = build_docx("", content)
        pdf_bytes = build_pdf("", content)

        b2_bucket, b2_key_txt = upload_bytes(
            case_id,
            "final_resources",
            txt_bytes,
            ".txt",
            "text/plain; charset=utf-8",
        )
        _, b2_key_docx = upload_bytes(
            case_id,
            "final_resources",
            docx_bytes,
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        _, b2_key_pdf = upload_bytes(
            case_id,
            "final_resources",
            pdf_bytes,
            ".pdf",
            "application/pdf",
        )

        documents = [
            {
                "kind": "final_resource_text",
                "bucket": b2_bucket,
                "key": b2_key_txt,
                "mime": "text/plain; charset=utf-8",
                "size_bytes": len(txt_bytes),
            },
            {
                "kind": "final_resource_docx",
                "bucket": b2_bucket,
                "key": b2_key_docx,
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": len(docx_bytes),
            },
            {
                "kind": "final_resource_pdf",
                "bucket": b2_bucket,
                "key": b2_key_pdf,
                "mime": "application/pdf",
                "size_bytes": len(pdf_bytes),
            },
        ]

        for doc in documents:
            conn.execute(
                text(
                    '''
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:case_id, :kind, :bucket, :key, :mime, :size_bytes, NOW())
                    '''
                ),
                {
                    "case_id": case_id,
                    "kind": doc["kind"],
                    "bucket": doc["bucket"],
                    "key": doc["key"],
                    "mime": doc["mime"],
                    "size_bytes": doc["size_bytes"],
                },
            )

        _set_status(conn, case_id, "final_ready")
        _append_event(
            conn,
            case_id,
            "ops_final_resource_finalized",
            {
                "resource_id": str(row[0]),
                "version": version,
                "chars": len(content),
                "documents": documents,
                "created_by": created_by,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": {
            "id": str(row[0]),
            "content": content,
            "version": version,
            "is_final": True,
            "created_by": created_by,
            "created_at": row[1],
            "updated_at": row[2],
        },
        "documents": documents,
    }


@router.post("/{case_id}/send-complete")
def send_complete_case_file(
    case_id: str,
    body: SendCompleteBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        resource = _latest_final_resource(conn, case_id)
        if not resource or not resource.get("is_final"):
            raise HTTPException(status_code=409, detail="Antes de enviar hay que guardar una versión final del recurso")

        docs_row = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE case_id = :id"),
            {"id": case_id},
        ).fetchone()
        docs_count = int(docs_row[0] or 0) if docs_row else 0

        _set_status(conn, case_id, "sent")
        _append_event(
            conn,
            case_id,
            "ops_complete_file_sent",
            {
                "resource_id": resource.get("id"),
                "resource_version": resource.get("version"),
                "documents_count": docs_count,
                "destination": body.destination,
                "channel": body.channel or "ops",
                "note": body.note,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource_version": resource.get("version"),
        "documents_count": docs_count,
        "message": "Expediente completo marcado como enviado.",
    }


@router.get("/{case_id}")
def get_case_detail(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        case = _case_or_404(conn, case_id)

        evs = conn.execute(
            text(
                '''
                SELECT payload
                FROM events
                WHERE case_id = :id AND type = 'ai_expediente_result'
                ORDER BY created_at DESC
                LIMIT 1
                '''
            ),
            {"id": case_id},
        ).fetchone()

        payload = evs[0] if evs and evs[0] else {}
        if not isinstance(payload, dict):
            payload = {}

        overrides = _load_ai_overrides(conn, case_id)

        familia = (
            overrides.get("familia")
            or payload.get("familia_resuelta")
            or payload.get("tipo_infraccion")
            or payload.get("classifier_result", {}).get("family")
            or payload.get("familia_detectada")
            or payload.get("familia")
            or payload.get("family")
        )
        confianza = (
            payload.get("tipo_infraccion_confidence")
            or payload.get("classifier_result", {}).get("confidence")
            or payload.get("confianza")
            or payload.get("confidence")
        )
        hecho = (
            overrides.get("hecho")
            or payload.get("hecho_imputado")
            or payload.get("hecho")
            or payload.get("hecho_para_recurso")
            or payload.get("arguments", {}).get("hecho")
            or payload.get("facts")
            or payload.get("detected_facts")
        )

        return {
            "id": case["id"],
            "status": case["status"],
            "familia_detectada": familia,
            "confianza": confianza,
            "hecho": hecho,
            "ai_overrides": overrides,
            "updated_at": case["updated_at"],
        }


@router.get("/{case_id}/ai-overrides")
def get_ai_overrides(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        overrides = _load_ai_overrides(conn, case_id)

    return {"ok": True, "case_id": case_id, "overrides": overrides}


@router.post("/{case_id}/save-ai-overrides")
def save_ai_overrides(
    case_id: str,
    body: SaveAiOverridesBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            hecho=body.hecho,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_ai_override_saved",
            {
                "familia": overrides.get("familia"),
                "hecho": overrides.get("hecho"),
                "motivo": overrides.get("motivo"),
                "saved_at": overrides.get("saved_at"),
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "overrides": overrides,
    }


@router.post("/{case_id}/approve")
def approve_case(
    case_id: str,
    body: ApproveBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "ready_to_submit")
        _append_event(
            conn,
            case_id,
            "operator_approved",
            {"note": body.note, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/manual")
def send_to_manual_review(
    case_id: str,
    body: ManualBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "manual_review")
        _append_event(
            conn,
            case_id,
            "manual_review_required",
            {"motivo": body.motivo, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/note")
def add_operator_note(
    case_id: str,
    body: NoteBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _append_event(
            conn,
            case_id,
            "operator_note",
            {"note": body.note, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/override-family")
def override_family(
    case_id: str,
    body: OverrideFamilyBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status, "overrides": overrides}


@router.post("/{case_id}/override-family-and-regenerate")
def override_family_and_regenerate(
    case_id: str,
    body: OverrideAndRegenerateBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )
        interesado = _load_interesado(conn, case_id)

    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=_prepare_interesado_for_generate(interesado),
            tipo=body.familia,
        )
        generate_dgt(req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error regenerando recurso: {e}")

    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "generated")

        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "classifier_result": {
                    "family": body.familia,
                    "confidence": 1.0,
                },
                "familia_resuelta": body.familia,
                "tipo_infraccion": body.familia,
                "hecho_imputado": _load_ai_overrides(conn, case_id).get("hecho"),
                "arguments": {
                    "hecho": f"Recurso regenerado manualmente. Motivo: {body.motivo}",
                },
                "admissibility": {
                    "admissibility": "REGENERATED",
                },
                "phase": {
                    "recommended_action": {
                        "action": "REVIEW_AND_SUBMIT",
                    }
                },
                "source": "operator_override",
                "at": _utcnow().isoformat(),
            },
        )

        _append_event(
            conn,
            case_id,
            "resource_regenerated",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "at": _utcnow().isoformat(),
                "mode": "generate_dgt",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "familia_correcta": body.familia,
    }


@router.post("/{case_id}/rewrite-hecho-and-regenerate")
def rewrite_hecho_and_regenerate(
    case_id: str,
    body: RewriteHechoBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            hecho=body.hecho,
            motivo=body.motivo,
        )

        interesado = _load_interesado(conn, case_id)

        _append_event(
            conn,
            case_id,
            "operator_rewrite_hecho",
            {
                "hecho": body.hecho,
                "motivo": body.motivo,
                "familia": body.familia,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )

    interesado = dict(interesado or {})
    interesado["manual_hecho_denunciado"] = body.hecho
    interesado["manual_hecho_motivo"] = body.motivo
    if body.familia:
        interesado["manual_family"] = body.familia

    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=_prepare_interesado_for_generate(interesado),
            tipo=body.familia,
        )
        generate_dgt(req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error regenerando desde hecho corregido: {e}")

    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "generated")

        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "classifier_result": {
                    "family": body.familia or "manual_hecho",
                    "confidence": 1.0,
                },
                "familia_resuelta": body.familia or _load_ai_overrides(conn, case_id).get("familia"),
                "tipo_infraccion": body.familia or _load_ai_overrides(conn, case_id).get("familia"),
                "hecho_imputado": body.hecho,
                "arguments": {
                    "hecho": body.hecho,
                },
                "admissibility": {
                    "admissibility": "REGENERATED",
                },
                "phase": {
                    "recommended_action": {
                        "action": "REVIEW_AND_SUBMIT",
                    }
                },
                "source": "manual_hecho",
                "at": _utcnow().isoformat(),
            },
        )

        _append_event(
            conn,
            case_id,
            "resource_regenerated_from_hecho",
            {
                "hecho": body.hecho,
                "motivo": body.motivo,
                "familia": body.familia,
                "at": _utcnow().isoformat(),
                "mode": "generate_dgt",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "hecho_final": body.hecho,
        "familia_forzada": body.familia,
    }


@router.post("/{case_id}/submit")
def submit_to_dgt(
    case_id: str,
    body: SubmitDGTBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        current_status = _get_status(conn, case_id)

        if current_status != "ready_to_submit" and not body.force:
            raise HTTPException(
                status_code=400,
                detail="El expediente debe estar en ready_to_submit antes de enviarse a DGT",
            )

        dgt_id = f"DGT-{case_id}-{int(datetime.now().timestamp())}"
        submitted_at = _utcnow()

        _set_status(conn, case_id, "submitted")

        try:
            conn.execute(
                text("UPDATE cases SET submitted_at = NOW() WHERE id = :id"),
                {"id": case_id},
            )
        except Exception:
            pass

        try:
            conn.execute(
                text("UPDATE cases SET dgt_id = :dgt_id WHERE id = :id"),
                {"id": case_id, "dgt_id": dgt_id},
            )
        except Exception:
            pass

        try:
            conn.execute(
                text("UPDATE cases SET dgt_submission_id = :dgt_id WHERE id = :id"),
                {"id": case_id, "dgt_id": dgt_id},
            )
        except Exception:
            pass

        _append_event(
            conn,
            case_id,
            "submitted_to_dgt",
            {
                "document_url": body.document_url,
                "dgt_id": dgt_id,
                "submitted_at": submitted_at.isoformat(),
                "mode": "stub",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "dgt_id": dgt_id,
        "submitted_at": submitted_at.isoformat(),
        "mode": "stub",
    }
