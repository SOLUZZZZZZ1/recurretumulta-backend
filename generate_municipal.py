import json
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf

router = APIRouter(tags=["generate_municipal"])


def _load_latest_core(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")
    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = (wrapper.get("extracted") or {}) if isinstance(wrapper, dict) else {}
    return core or {}


def _raw_blob(core: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado", "organo", "organismo"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts).lower()


def _municipal_kind(core: Dict[str, Any]) -> str:
    blob = _raw_blob(core)
    if any(k in blob for k in ["semaforo", "semáforo", "luz roja", "fase roja", "no respetar la luz roja", "línea de detención", "linea de detencion"]):
        return "municipal_semaforo"
    if "sentido contrario" in blob or "circulacion en sentido contrario" in blob or "circulación en sentido contrario" in blob:
        return "municipal_sentido_contrario"
    return "municipal_generic"


def _tpl_municipal_semaforo(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "NO RESPETAR LA LUZ ROJA (SEMÁFORO)."
    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del Ayuntamiento competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FASE ROJA ACTIVA Y REBASE EFECTIVO\n\n"
        "Debe acreditarse, de forma objetiva y verificable, fase roja activa en el instante exacto y rebase de la línea de detención con el rojo ya activo (no ámbar/transición).\n\n"
        "ALEGACIÓN SEGUNDA — SECUENCIA ÍNTEGRA Y SINCRONIZACIÓN\n\n"
        "Se solicita secuencia íntegra sin recortes (imágenes/vídeo), con marca temporal verificable, que permita comprobar fase, línea de detención y posición del vehículo.\n"
        "Se interesa además acreditación de sincronización horaria y mantenimiento/correcto funcionamiento del sistema municipal en la fecha del hecho.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita expediente íntegro y motivación individualizada con soporte probatorio completo para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa (secuencia, sincronización, identificación del cruce/semáforo).\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_municipal_sentido_contrario(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "CIRCULACIÓN EN SENTIDO CONTRARIO."
    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del Ayuntamiento competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TRAMO EXACTO Y SEÑALIZACIÓN APLICABLE\n\n"
        "Debe precisarse tramo exacto (calle/punto), configuración real de la vía y señalización vertical/horizontal aplicable en el acceso, con visibilidad real (obras/desvíos/obstáculos).\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA OBJETIVA\n\n"
        "Se solicita soporte objetivo verificable (fotografías/vídeo/croquis). Sin soporte verificable, la imputación queda en afirmación genérica.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_municipal_generic(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "Ayuntamiento (no consta)."
    hecho = core.get("hecho_imputado") or "INFRACCIÓN MUNICIPAL."

    asunto = "ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del Ayuntamiento competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — INSUFICIENCIA PROBATORIA Y MOTIVACIÓN\n\n"
        "Se solicita prueba suficiente y motivación individualizada del hecho imputado, con identificación del lugar y soporte verificable para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def generate_municipal_for_case(conn, case_id: str, tipo: Optional[str] = None) -> Dict[str, Any]:
    core = _load_latest_core(conn, case_id)
    mk = _municipal_kind(core)

    if mk == "municipal_semaforo":
        tpl = _tpl_municipal_semaforo(core)
    elif mk == "municipal_sentido_contrario":
        tpl = _tpl_municipal_sentido_contrario(core)
    else:
        tpl = _tpl_municipal_generic(core)

    kind_docx = "generated_docx_alegaciones"
    kind_pdf = "generated_pdf_alegaciones"

    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id, "generated", docx_bytes, ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(case_id, "generated", pdf_bytes, ".pdf", "application/pdf")

    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
        ),
        {"case_id": case_id, "kind": kind_docx, "b2_bucket": b2_bucket, "b2_key": b2_key_docx,
         "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": len(docx_bytes)},
    )
    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
        ),
        {"case_id": case_id, "kind": kind_pdf, "b2_bucket": b2_bucket, "b2_key": b2_key_pdf,
         "mime": "application/pdf", "size_bytes": len(pdf_bytes)},
    )

    conn.execute(
        text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id,'resource_generated_municipal',CAST(:payload AS JSONB),NOW())"),
        {"case_id": case_id, "payload": json.dumps({"final_kind": mk})},
    )
    conn.execute(text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

    return {"ok": True, "case_id": case_id, "final_kind": mk}


class GenerateMunicipalRequest(BaseModel):
    case_id: str
    tipo: Optional[str] = None


@router.post("/generate/municipal")
def generate_municipal(req: GenerateMunicipalRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_municipal_for_case(conn, req.case_id, tipo=req.tipo)
    return {"ok": True, "message": "Recurso municipal generado en DOCX y PDF.", **result}
