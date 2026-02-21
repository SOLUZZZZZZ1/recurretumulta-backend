import json
import os
import re
from typing import Any, Dict, Optional, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from ai.velocity_decision import decide_modo_velocidad

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
    if not body:
        return ""
    for line in (body.splitlines() or []):
        l = (line or "").strip()
        if not l:
            continue
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            return l
    return ""


def _is_velocity_context(core: Dict[str, Any], cuerpo: str) -> bool:
    tipo = (core or {}).get("tipo_infraccion") or ""
    if str(tipo).lower().strip() == "velocidad":
        return True
    if (core or {}).get("velocidad_medida_kmh") or (core or {}).get("velocidad_limite_kmh"):
        return True
    bl = (cuerpo or "").lower()
    return any(k in bl for k in ["exceso de velocidad", "km/h", "cinemómetro", "cinemometro", "radar"])


def _speed_margin_value(measured: int) -> float:
    """Margen conservador (fijo/estático) conforme ICT/155/2020.
    - <=100: 5 km/h
    - >100: 5%
    """
    if measured <= 100:
        return 5.0
    return round(measured * 0.05, 2)


def _build_velocity_calc_paragraph(core: Dict[str, Any]) -> str:
    """Párrafo ilustrativo de cálculo. Si faltan datos, devuelve cadena vacía.
    Evita 'exceso negativo': si la velocidad corregida queda por debajo del límite,
    se formula como 'por debajo del límite máximo permitido'.
    """
    try:
        measured = core.get("velocidad_medida_kmh")
        limit = core.get("velocidad_limite_kmh")
        if isinstance(measured, str) and measured.strip().isdigit():
            measured = int(measured.strip())
        if isinstance(limit, str) and limit.strip().isdigit():
            limit = int(limit.strip())
        if not isinstance(measured, int) or not isinstance(limit, int):
            return ""
        margin = _speed_margin_value(measured)
        corrected = max(0.0, float(measured) - float(margin))
        exceso = float(corrected) - float(limit)

        if exceso <= 0:
            return (
                "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
                f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
                f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, "
                "lo que la situaría por debajo del límite máximo permitido. "
                "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
            )

        return (
            "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
            f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
            f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, "
            f"lo que supondría un exceso efectivo aproximado de {exceso:.2f} km/h sobre el límite. "
            "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
        )
    except Exception:
        return ""


def _inject_bucket_paragraph(body: str, decision: Dict[str, Any]) -> str:
    """Inserta párrafo extra según bucket (leve/grave) antes de 'III. SOLICITO'."""
    if not body or not isinstance(decision, dict):
        return body
    if (decision.get("mode") or "") != "probatorio_puro":
        return body

    bucket = decision.get("bucket")
    if bucket not in ("leve", "grave"):
        return body

    if bucket == "leve":
        extra = (
            "A mayor abundamiento, aun en hipótesis de que se tuviera por acreditada la medición, se trataría de un exceso mínimo, "
            "sin constancia de riesgo concreto, por lo que procede extremar las exigencias de motivación y prueba y ponderar la proporcionalidad "
            "de la reacción sancionadora.\n"
        )
    else:  # grave
        extra = (
            "Dada la gravedad potencial atribuida, la exigencia de prueba técnica completa, trazabilidad e integridad/cadena de custodia del dato debe ser máxima, "
            "evitando fórmulas estereotipadas y aportando soporte documental verificable.\n"
        )

    # Insert before III. SOLICITO
    m = re.search(r"^III\.\s*SOLICITO\b", body, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return body + "\n\n" + extra
    return body[:m.start()] + extra + "\n" + body[m.start():]


def _force_velocity_vse1_if_needed(asunto: str, cuerpo: str, core: Dict[str, Any]) -> Tuple[str, str]:
    """Opción B real: IA-first, pero si VELOCIDAD no trae estructura VSE-1, imponemos plantilla fija."""
    # Si ya trae ALEGACIÓN PRIMERA (estructura buena), no tocar
    if re.search(r"^ALEGACI[ÓO]N\s+PRIMERA\b", cuerpo or "", re.IGNORECASE | re.MULTILINE):
        return asunto, cuerpo

    # Solo si es velocidad
    if not _is_velocity_context(core, cuerpo):
        return asunto, cuerpo

    expediente = (core or {}).get("expediente_ref") or (core or {}).get("numero_expediente") or None
    organo = (core or {}).get("organo") or (core or {}).get("organismo") or None
    hecho = (core or {}).get("hecho_imputado") or "EXCESO DE VELOCIDAD."
    if isinstance(hecho, str) and hecho.strip() and "veloc" not in hecho.lower():
        hecho = "EXCESO DE VELOCIDAD."

    calc = _build_velocity_calc_paragraph(core)
    calc = (calc + "\n") if calc else ""

    asunto2 = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo2 = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1. Órgano: {organo if organo else 'No consta acreditado.'}\n"
        f"2) Identificación expediente: {expediente if expediente else 'No consta acreditado.'}\n"
        f"3) Hecho imputado: {hecho.strip()}\n\n"
        "II. ALEGACIONES\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)\n"
        "La validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación\n"
        "documental del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020). No\n"
        "basta una afirmación genérica de verificación: debe aportarse soporte documental verificable.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y\n"
        "   emplazamiento exacto (vía, punto kilométrico y sentido).\n"
        "2) Certificado de verificación metrológica vigente a la fecha del hecho, así como constancia de la\n"
        "   última verificación periódica o, en su caso, tras reparación.\n"
        "3) Captura o fotograma COMPLETO, sin recortes y legible, que permita asociar inequívocamente la\n"
        "   medición al vehículo denunciado.\n"
        "4) Margen aplicado y determinación de la velocidad corregida (velocidad medida vs velocidad\n"
        "   corregida), con motivación técnica suficiente.\n"
        "5) Acreditación de la cadena de custodia del dato (integridad del registro, sistema de\n"
        "   almacenamiento y correspondencia inequívoca con el vehículo).\n"
        "6) Acreditación del límite aplicable y su señalización en el punto exacto (genérica vs específica) y\n"
        "   su coherencia con la ubicación consignada.\n"
        "7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y\n"
        "   tramo sancionador resultante.\n\n"
        f"{calc}"
        "III. SOLICITO\n"
        "1. Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación\n"
        "   técnica suficiente.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.\n"
    ).strip()

    return asunto2, cuerpo2


def _velocity_strict_validate(body: str) -> List[str]:
    """SVL-1 VELOCIDAD (simple, compatible con plantilla fija)."""
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
    if "cinemómetro" not in b and "cinemometro" not in b and "radar" not in b:
        missing.append("cinemometro")
    return missing


def _strict_validate_or_raise(conn, case_id: str, core: Dict[str, Any], tpl: Dict[str, str], ai_used: bool) -> None:
    tipo = (core or {}).get("tipo_infraccion") or ""
    body = (tpl or {}).get("cuerpo") or ""
    if (tipo or "").lower() == "velocidad" or _is_velocity_context(core, body):
        missing = _velocity_strict_validate(body)
        if missing:
            raise HTTPException(status_code=422, detail=f"Velocity Strict no cumplido. Faltan/errores: {missing}.")


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

    # auditoría (no rompe si falla)
    decision_mode = "unknown"
    decision: Dict[str, Any] = {"mode": "unknown", "reasons": ["not_computed"]}

    # IA PRIMERO
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

                # Opción B: forzar VSE-1 si es velocidad y el LLM no estructuró bien
                asunto, cuerpo = _force_velocity_vse1_if_needed(asunto, cuerpo, core)

                # Decision sobre el cuerpo ya final
                try:
                    decision = decide_modo_velocidad(core, body=cuerpo, capture_mode="UNKNOWN") or decision
                    decision_mode = (decision.get("mode") or "unknown") if isinstance(decision, dict) else "unknown"
                except Exception:
                    pass

                # Bucket paragraph (leve/grave) antes de SOLICITO
                cuerpo = _inject_bucket_paragraph(cuerpo, decision)

                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True
        except Exception as e:
            ai_error = str(e)
            tpl = None

    # FALLBACK A PLANTILLAS
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)
            filename_base = "alegaciones_dgt"

        # decision también en plantillas (solo auditoría)
        try:
            decision = decide_modo_velocidad(core, body=(tpl.get("cuerpo") or ""), capture_mode="UNKNOWN") or decision
            decision_mode = (decision.get("mode") or decision_mode) if isinstance(decision, dict) else decision_mode
        except Exception:
            pass
    else:
        filename_base = "recurso_reposicion_dgt" if tipo == "reposicion" else "alegaciones_dgt"

    if tipo == "reposicion":
        kind_docx = "generated_docx_reposicion"
        kind_pdf = "generated_pdf_reposicion"
    else:
        kind_docx = "generated_docx_alegaciones"
        kind_pdf = "generated_pdf_alegaciones"

    # FORCE bucket injection on final tpl (último punto seguro antes de validar/generar)
    try:
        if tpl and isinstance(tpl, dict):
            tpl["cuerpo"] = _inject_bucket_paragraph(tpl.get("cuerpo") or "", decision)
    except Exception:
        pass

    # STRICT
    _strict_validate_or_raise(conn, case_id, core, tpl, ai_used)

    # DOCX/PDF
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
                    "velocity_decision_mode": decision_mode,
                    "velocity_decision": decision,
                }
            ),
        },
    )

    conn.execute(text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

    return {
        "ok": True,
        "case_id": case_id,
        "tipo": tipo,
        "filename_base": filename_base,
        "ai_used": ai_used,
        "ai_error": ai_error,
        "override_mode": override_mode,
        "velocity_decision_mode": decision_mode,
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
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, tipo=req.tipo)
    return {"ok": True, "message": "Recurso generado en DOCX y PDF.", **result}
