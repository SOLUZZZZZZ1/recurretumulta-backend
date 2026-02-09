# ops_automation.py — automatización “sin humanos” (tick/worker)
import json
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import text

from database import get_engine
from b2_storage import download_bytes
from dgt_client import submit_pdf, DGTNotConfigured

# Reutilizamos el generador existente
from generate import GenerateRequest, generate_dgt


def _event(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
        ),
        {"c": case_id, "t": typ, "p": json.dumps(payload)},
    )


def _latest_generated_pdf(conn, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            "SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at "
            "FROM documents "
            "WHERE case_id=:id AND kind LIKE 'generated_pdf%' "
            "ORDER BY created_at DESC "
            "LIMIT 1"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        return None
    return {
        "kind": row[0],
        "bucket": row[1],
        "key": row[2],
        "mime": row[3],
        "size_bytes": int(row[4] or 0),
        "created_at": str(row[5]),
    }


def _has_justificante(conn, case_id: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM documents "
            "WHERE case_id=:id AND kind='justificante_presentacion' "
            "LIMIT 1"
        ),
        {"id": case_id},
    ).fetchone()
    return bool(row)


def _require_paid_and_authorized(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT id, status, payment_status, authorized, COALESCE(test_mode,FALSE), COALESCE(override_deadlines,FALSE) "
            "FROM cases WHERE id=:id"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    if (row[2] or "") != "paid":
        raise HTTPException(status_code=402, detail="Pago requerido")
    if not bool(row[3]):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")
    return {
        "id": str(row[0]),
        "status": row[1] or "",
        "payment_status": row[2] or "",
        "authorized": bool(row[3]),
        "test_mode": bool(row[4]),
        "override_deadlines": bool(row[5]),
    }


def _ensure_generated(conn, case_id: str) -> Dict[str, Any]:
    """Si no hay PDF generado, llama al generador actual (/generate/dgt) y vuelve."""
    pdf_doc = _latest_generated_pdf(conn, case_id)
    if pdf_doc:
        return pdf_doc

    # Cargamos datos de interesado guardados en cases.interested_data (si existe)
    row = conn.execute(text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id=:id"), {"id": case_id}).fetchone()
    interesado = row[0] if row and row[0] else {}

    # Llamamos directamente al handler existente (reutiliza B2 + documents + events)
    req = GenerateRequest(case_id=case_id, interesado=interesado, tipo=None)
    generate_dgt(req)

    # Releer
    pdf_doc = _latest_generated_pdf(conn, case_id)
    if not pdf_doc:
        raise HTTPException(status_code=500, detail="No se pudo generar el PDF.")
    return pdf_doc


def submit_case_fully_automatic(case_id: str) -> Dict[str, Any]:
    """Pipeline sin humanos: generar (si falta) -> presentar -> guardar justificante -> status=submitted.
    Es idempotente: si ya hay justificante, no re-presenta.
    """
    engine = get_engine()
    with engine.begin() as conn:
        meta = _require_paid_and_authorized(conn, case_id)

        if _has_justificante(conn, case_id):
            # Ya presentado
            _event(conn, case_id, "auto_skip_already_submitted", {})
            return {"ok": True, "case_id": case_id, "status": "submitted", "skipped": True}

        pdf_doc = _ensure_generated(conn, case_id)

        # Descargar PDF bytes desde B2
        pdf_bytes = download_bytes(pdf_doc["bucket"], pdf_doc["key"])

        try:
            resp = submit_pdf(case_id, pdf_bytes, metadata={"generated_kind": pdf_doc["kind"]})
        except DGTNotConfigured as e:
            _event(conn, case_id, "dgt_not_configured", {"error": str(e)})
            # Dejamos el caso en ready_to_submit para reintento automático futuro
            raise HTTPException(status_code=501, detail=str(e))
        except NotImplementedError as e:
            _event(conn, case_id, "dgt_not_implemented", {"error": str(e)})
            raise HTTPException(status_code=501, detail=str(e))
        except Exception as e:
            _event(conn, case_id, "dgt_submit_failed", {"error": str(e)})
            raise HTTPException(status_code=502, detail=f"Fallo al presentar en DGT: {e}")

        registro = (resp.get("registro") or "").strip()
        csv = (resp.get("csv") or None)
        justificante_pdf = resp.get("justificante_pdf") or b""
        if not justificante_pdf:
            raise HTTPException(status_code=502, detail="DGT no devolvió justificante_pdf.")

        # Guardar justificante en B2 + documents
        from b2_storage import upload_bytes
        b2_bucket, b2_key = upload_bytes(case_id, "justificantes", justificante_pdf, ".pdf", "application/pdf")

        conn.execute(
            text(
                "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
                "VALUES (:id,'justificante_presentacion',:b,:k,'application/pdf',:s,NOW())"
            ),
            {"id": case_id, "b": b2_bucket, "k": b2_key, "s": len(justificante_pdf)},
        )

        conn.execute(
            text("UPDATE cases SET status='submitted', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        _event(conn, case_id, "dgt_submitted", {"registro": registro, "csv": csv, "justificante": {"bucket": b2_bucket, "key": b2_key}})

        return {"ok": True, "case_id": case_id, "status": "submitted", "registro": registro, "csv": csv}


def tick(limit: int = 25) -> Dict[str, Any]:
    """Procesa en lote casos listos para presentar.
    Diseñado para ser llamado por un cron cada 2-5 minutos.
    """
    engine = get_engine()
    picked = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id FROM cases "
                "WHERE status='ready_to_submit' AND payment_status='paid' AND authorized=TRUE "
                "ORDER BY created_at ASC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        ).fetchall()
        picked = [str(r[0]) for r in rows]

    ok = 0
    failed = 0
    results = []
    for cid in picked:
        try:
            res = submit_case_fully_automatic(cid)
            ok += 1
            results.append({"case_id": cid, "ok": True, "result": res})
        except Exception as e:
            failed += 1
            results.append({"case_id": cid, "ok": False, "error": str(e)})

    return {"ok": True, "picked": len(picked), "submitted": ok, "failed": failed, "results": results}
