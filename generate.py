import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes
from docx_builder import build_docx
from dgt_templates import build_dgt_alegaciones_text, build_dgt_reposicion_text

router = APIRouter(tags=["generate"])

class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None  # "alegaciones" | "reposicion" | None(auto)

@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    try:
        engine = get_engine()

        with engine.begin() as conn:
            row = conn.execute(
                text("""SELECT extracted_json
                        FROM extractions
                        WHERE case_id = :case_id
                        ORDER BY created_at DESC
                        LIMIT 1"""),
                {"case_id": req.case_id},
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

            extracted_json = row[0]
            wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
            core = wrapper.get("extracted") or {}

            tipo = (req.tipo or "").lower().strip() or None
            if not tipo:
                tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

            if tipo == "reposicion":
                tpl = build_dgt_reposicion_text(core, req.interesado)
                kind = "generated_docx_reposicion"
                filename = "recurso_reposicion_dgt.docx"
            else:
                tpl = build_dgt_alegaciones_text(core, req.interesado)
                kind = "generated_docx_alegaciones"
                filename = "alegaciones_dgt.docx"

            docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
            b2_bucket, b2_key = upload_bytes(
                req.case_id,
                "generated",
                docx_bytes,
                ".docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

            conn.execute(
                text("""INSERT INTO documents(case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at)
                         VALUES (:case_id, :kind, :b2_bucket, :b2_key, NULL, :mime, :size_bytes, NOW())"""),
                {
                    "case_id": req.case_id,
                    "kind": kind,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size_bytes": len(docx_bytes),
                },
            )

            conn.execute(
                text("""INSERT INTO events(case_id, type, payload, created_at)
                         VALUES (:case_id, 'resource_generated', CAST(:payload AS JSONB), NOW())"""),
                {
                    "case_id": req.case_id,
                    "payload": json.dumps({"tipo": tipo, "b2_bucket": b2_bucket, "b2_key": b2_key, "filename": filename}),
                },
            )

            conn.execute(text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"), {"case_id": req.case_id})

        return {
            "ok": True,
            "message": "Documento generado (DOCX) y guardado en Backblaze.",
            "case_id": req.case_id,
            "tipo": tipo,
            "document": {"bucket": b2_bucket, "key": b2_key, "filename": filename},
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /generate/dgt: {e}")
