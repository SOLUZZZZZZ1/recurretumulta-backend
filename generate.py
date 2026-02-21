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

    # Si la primera línea contiene "borrador", la eliminamos (case-insensitive)
    lines = body.splitlines()
    if lines and ("borrador" in (lines[0] or "").lower()):
        lines = lines[1:]

    # También eliminamos líneas vacías iniciales
    while lines and not (lines[0] or "").strip():
        lines = lines[1:]

    return "\n".join(lines).strip()


def _first_alegacion_title(body: str) -> str:
    """Devuelve el título de la primera alegación detectada (si existe)."""
    if not body:
        return ""
    # Busca líneas tipo: "ALEGACIÓN 1 — ..." / "ALEGACIÓN PRIMERA — ..."
    for line in (body.splitlines() or []):
        l = (line or "").strip()
        if not l:
            continue
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            return l
    return ""


def _velocity_strict_validate(body: str) -> List[str]:
    """Valida que un borrador de velocidad cumple mínimos VSE-1 (anti-plantilla)."""
    b = (body or "").lower()

    required_any = [
        ("margen", ["margen"]),
        ("velocidad_corregida", ["velocidad corregida", "corregida"]),
        ("metrologia", ["certificado", "verificación", "verificacion", "metrológ", "metrolog"]),
        ("cinemometro", ["cinemómetro", "cinemometro", "radar"]),
        ("captura", ["captura", "fotograma", "imagen"]),
        ("cadena_custodia", ["cadena de custodia", "integridad del registro", "integridad", "correspondencia inequívoca", "correspondencia inequivoca"]),
    ]

    missing: List[str] = []
    for name, needles in required_any:
        if not any(n in b for n in needles):
            missing.append(name)

    # Estructura: debe existir bloque de alegaciones.
    # Aceptamos como estructura válida:
    #  - Encabezado explícito de alegación ("ALEGACIÓN PRIMERA..."), o
    #  - Sección "II. ALEGACIONES" aunque el redactor use numeración 1), 2), etc.
    first = _first_alegacion_title(body).lower()

    if not first:
        if not re.search(r"^II\.\s*ALEGACIONES\b", body or "", re.IGNORECASE | re.MULTILINE):
            missing.append("estructura_alegaciones (no se detecta encabezado de alegaciones)")
    else:
        if any(k in first for k in ["presunción", "presuncion", "inocencia"]):
            missing.append("orden_alegaciones (alegación 1 no puede ser presunción de inocencia en velocidad)")

    return missing


def _ensure_min_section_headers(body: str) -> str:
    """Asegura encabezados mínimos I/II/III si faltan."""
    body = (body or "").strip()
    if not body:
        return body
    b = body

    if not re.search(r"^I\.\s*ANTECEDENTES\b", b, re.IGNORECASE | re.MULTILINE):
        b = "I. ANTECEDENTES\n" + b

    if not re.search(r"^II\.\s*ALEGACIONES\b", b, re.IGNORECASE | re.MULTILINE):
        m = re.search(r"^ALEGACI[ÓO]N\s+PRIMERA\b", b, re.IGNORECASE | re.MULTILINE)
        if m:
            b = b[:m.start()] + "II. ALEGACIONES\n" + b[m.start():]
        else:
            b = b + "\n\nII. ALEGACIONES\n"

    if not re.search(r"^III\.\s*SOLICITO\b", b, re.IGNORECASE | re.MULTILINE):
        if re.search(r"^SOLICITO\b", b, re.IGNORECASE | re.MULTILINE):
            b = re.sub(r"^SOLICITO\b", "III. SOLICITO", b, flags=re.IGNORECASE | re.MULTILINE)
        else:
            b = b + "\n\nIII. SOLICITO\n"

    return b.strip()


def _repair_velocity_body_minimal(body: str) -> str:
    """Reparación determinista mínima para VELOCIDAD para pasar VSE-1/SVL.
    No inventa hechos: formula 'no consta acreditado' y checklist técnico.
    """
    b = _ensure_min_section_headers(body or "")

    # Si no hay 'ALEGACIÓN PRIMERA', insertamos bloque estándar tras 'II. ALEGACIONES'
    if not re.search(r"^ALEGACI[ÓO]N\s+PRIMERA\b", b, re.IGNORECASE | re.MULTILINE):
        m = re.search(r"^II\.\s*ALEGACIONES\b", b, re.IGNORECASE | re.MULTILINE)
        insert_at = 0
        if m:
            line_end = b.find("\n", m.end())
            insert_at = len(b) if line_end == -1 else line_end + 1

        block = (
            "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)\n\n"
            "La validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación documental "
            "del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020). No basta una afirmación genérica "
            "de verificación: debe aportarse soporte documental verificable.\n\n"
            "No consta acreditado en el expediente:\n\n"
            "1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y emplazamiento exacto (vía, punto kilométrico y sentido).\n"
            "2) Certificado de verificación metrológica vigente a la fecha del hecho, así como constancia de la última verificación periódica o, en su caso, tras reparación.\n"
            "3) Captura o fotograma COMPLETO, sin recortes y legible, que permita asociar inequívocamente la medición al vehículo denunciado.\n"
            "4) Margen aplicado y determinación de la velocidad corregida (velocidad medida vs velocidad corregida), con motivación técnica suficiente.\n"
            "5) Acreditación de la cadena de custodia del dato (integridad del registro, sistema de almacenamiento y correspondencia inequívoca con el vehículo denunciado).\n"
            "6) Acreditación del límite aplicable y su señalización en el punto exacto (genérica vs específica) y su coherencia con la ubicación consignada.\n"
            "7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y tramo sancionador resultante.\n\n"
        )
        b = b[:insert_at] + block + b[insert_at:]

    return b.strip()


def _strict_validate_or_raise(conn, case_id: str, core: Dict[str, Any], tpl: Dict[str, str], ai_used: bool) -> None:
    """Capa SVL-1: bloquea generación si el recurso no cumple mínimos por tipo."""
    tipo = (core or {}).get("tipo_infraccion") or ""
    body = (tpl or {}).get("cuerpo") or ""

    if _is_velocity_context(core, tpl):
        missing = _velocity_strict_validate(body)
        if missing:
            # Auto-repair determinista (1 intento) para evitar falsos 422 por formato
            repaired = _repair_velocity_body_minimal(body)
            missing2 = _velocity_strict_validate(repaired)
            if not missing2:
                tpl["cuerpo"] = repaired
                return
            missing = missing2
            # Log de evento para OPS/auditoría
            try:
                conn.execute(
                    text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id,'strict_validation_failed',CAST(:payload AS JSONB),NOW())"),
                    {"case_id": case_id, "payload": json.dumps({"type": "velocidad", "missing": missing, "ai_used": ai_used})},
                )
            except Exception:
                pass
            raise HTTPException(
                status_code=422,
                detail=f"Velocity Strict no cumplido. Faltan/errores: {missing}. Regenerar borrador (VSE-1) antes de emitir DOCX/PDF.",
            )




def _is_velocity_context(core: Dict[str, Any], tpl: Dict[str, str]) -> bool:
    """Detecta contexto de velocidad incluso si core.tipo_infraccion no viene bien."""
    tipo = (core or {}).get("tipo_infraccion") or ""
    if str(tipo).lower().strip() == "velocidad":
        return True
    # señales en core
    if (core or {}).get("velocidad_medida_kmh") or (core or {}).get("velocidad_limite_kmh"):
        return True
    body = (tpl or {}).get("cuerpo") or ""
    bl = body.lower()
    if "exceso de velocidad" in bl or "km/h" in bl or "cinemómetro" in bl or "cinemometro" in bl or "radar" in bl:
        return True
    return False


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

    # Merge interesado desde DB si viene vacío/parcial
    interesado_db = _load_interested_data_from_cases(conn, case_id)
    interesado = _merge_interesado(interesado or {}, interesado_db)

    # Flags override
    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    # Tipo por defecto
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

    # Kinds (compatibles con OPS/automation)
    if tipo == "reposicion":
        kind_docx = "generated_docx_reposicion"
        kind_pdf = "generated_pdf_reposicion"
    else:
        kind_docx = "generated_docx_alegaciones"
        kind_pdf = "generated_pdf_alegaciones"

    # ==========================
    # STRICT VALIDATION LAYER (SVL-1)
    # ==========================
    # Bloquea la emisión si el borrador no cumple mínimos por tipo (especialmente VELOCIDAD).
    _strict_validate_or_raise(conn, case_id, core, tpl, ai_used)


    # Generar DOCX/PDF
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

    # Persistir documents (DOCX)
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

    # Persistir documents (PDF)
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

    # Evento
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
