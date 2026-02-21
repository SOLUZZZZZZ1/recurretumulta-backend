import json
import os
import re
from typing import Any, Dict, Optional, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import build_dgt_alegaciones_text, build_dgt_reposicion_text

router = APIRouter(tags=["generate"])

RTM_DGT_GENERATION_MODE = (os.getenv("RTM_DGT_GENERATION_MODE") or "AI_FIRST").strip().upper()


# ==========================
# HELPERS
# ==========================

def _load_interested_data_from_cases(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data, '{}'::jsonb) FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    return (row[0] if row and row[0] else {}) or {}


def _merge_interesado(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    primary = primary or {}
    fallback = fallback or {}
    out = dict(fallback)
    for k, v in primary.items():
        if v not in (None, ""):
            out[k] = v
    return out


def _missing_interested_fields(interesado: Dict[str, Any]) -> List[str]:
    interesado = interesado or {}
    missing: List[str] = []
    for k in ("nombre", "dni_nie", "domicilio_notif"):
        v = interesado.get(k)
        if not v or not str(v).strip():
            missing.append(k)
    return missing


def _load_case_flags(conn, case_id: str) -> Dict[str, bool]:
    row = conn.execute(
        text("SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    return {
        "test_mode": bool(row[0]) if row else False,
        "override_deadlines": bool(row[1]) if row else False,
    }


def _strip_borrador_prefix_from_body(body: str) -> str:
    body = (body or "").lstrip()
    if not body:
        return body

    lines = body.splitlines()
    if lines and ("borrador" in (lines[0] or "").lower()):
        lines = lines[1:]

    while lines and not (lines[0] or "").strip():
        lines = lines[1:]

    return "\n".join(lines).strip()


def _first_alegacion_title(body: str) -> str:
    """Devuelve el título de la primera alegación detectada (si existe)."""
    if not body:
        return ""
    for line in (body.splitlines() or []):
        l = (line or "").strip()
        if not l:
            continue
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            return l
    return ""


def _velocity_strict_validate(body: str) -> List[str]:
    """Strict Validation Layer (SVL-1) — VELOCIDAD (estable y simple)

    Reglas mínimas (sin auto-repair):
    1) Estructura de alegaciones: 'ALEGACIÓN*' o 'II. ALEGACIONES'
    2) Debe aparecer 'margen'
    3) Debe aparecer 'cadena de custodia'
    """
    b = (body or "").lower()
    missing: List[str] = []

    has_alegacion = bool(_first_alegacion_title(body))
    has_section = bool(re.search(r"^II\.\s*ALEGACIONES\b", body or "", re.IGNORECASE | re.MULTILINE))
    if not (has_alegacion or has_section):
        missing.append("estructura_alegaciones (no se detecta encabezado de alegaciones)")

    if "margen" not in b:
        missing.append("margen")

    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")

    first = _first_alegacion_title(body).lower()
    if first and any(k in first for k in ["presunción", "presuncion", "inocencia"]):
        missing.append("orden_alegaciones (alegación 1 no puede ser presunción de inocencia en velocidad)")

    return missing


def _strict_validate_or_raise(conn, case_id: str, core: Dict[str, Any], tpl: Dict[str, str], ai_used: bool) -> None:
    """Capa SVL-1: bloquea generación si el recurso no cumple mínimos por tipo."""
    tipo = (core or {}).get("tipo_infraccion") or ""
    body = (tpl or {}).get("cuerpo") or ""

    if (tipo or "").lower() == "velocidad":
        missing = _velocity_strict_validate(body)
        if missing:
            try:
                conn.execute(
                    text(
                        "INSERT INTO events(case_id, type, payload, created_at) "
                        "VALUES (:case_id,'strict_validation_failed',CAST(:payload AS JSONB),NOW())"
                    ),
                    {"case_id": case_id, "payload": json.dumps({"type": "velocidad", "missing": missing, "ai_used": ai_used})},
                )
            except Exception:
                pass

            raise HTTPException(
                status_code=422,
                detail=f"Velocity Strict no cumplido. Faltan/errores: {missing}. Regenerar borrador (VSE-1) antes de emitir DOCX/PDF.",
            )


# ==========================
# FUNCIÓN PRINCIPAL
# ==========================

def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
    tipo: Optional[str] = None,
) -> Dict[str, Any]:

    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = wrapper.get("extracted") or {}

    interesado_db = _load_interested_data_from_cases(conn, case_id)
    interesado = _merge_interesado(interesado or {}, interesado_db)

    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    tpl: Optional[Dict[str, str]] = None
    ai_used = False
    ai_error: Optional[str] = None

    # ==========================
    # IA PRIMERO
    # ==========================
    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()

            if asunto and cuerpo:
                if override_mode:
                    asunto = "RECURSO (MODO PRUEBA)"
                    cuerpo = _strip_borrador_prefix_from_body(cuerpo)

                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True
        except Exception as e:
            ai_error = str(e)
            tpl = None

    # ==========================
    # FALLBACK A PLANTILLAS
    # ==========================
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)
            filename_base = "alegaciones_dgt"
    else:
        filename_base = "recurso_reposicion_dgt" if tipo == "reposicion" else "alegaciones_dgt"

    if tipo == "reposicion":
        kind_docx = "generated_docx_reposicion"
        kind_pdf = "generated_pdf_reposicion"
    else:
        kind_docx = "generated_docx_alegaciones"
        kind_pdf = "generated_pdf_alegaciones"

    # ==========================
    # STRICT VALIDATION LAYER (SVL-1)
    # ==========================
    _strict_validate_or_raise(conn, case_id, core, tpl, ai_used)

    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(
        case_id,
        "generated",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )

    conn.execute(
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "kind": kind_docx,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_docx,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(docx_bytes),
        },
    )

    conn.execute(
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "kind": kind_pdf,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_pdf,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
    )

    conn.execute(
        text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id,'resource_generated',CAST(:payload AS JSONB),NOW())"),
        {
            "case_id": case_id,
            "payload": json.dumps(
                {
                    "tipo": tipo,
                    "ai_used": ai_used,
                    "ai_error": ai_error,
                    "generation_mode": RTM_DGT_GENERATION_MODE,
                    "override_mode": override_mode,
                    "missing_interested_fields": _missing_interested_fields(interesado),
                }
            ),
        },
    )

    conn.execute(
        text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )

    return {
        "ok": True,
        "case_id": case_id,
        "tipo": tipo,
        "filename_base": filename_base,
        "ai_used": ai_used,
        "ai_error": ai_error,
        "override_mode": override_mode,
    }


# ==========================
# ENDPOINT
# ==========================

class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(
            conn,
            req.case_id,
            interesado=req.interesado,
            tipo=req.tipo,
        )
    return {"ok": True, "message": "Recurso generado en DOCX y PDF.", **result}
