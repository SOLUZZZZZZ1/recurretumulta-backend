# ops_automation_router.py
import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text

from ops_automation import tick  # tu archivo actual con tick()

router = APIRouter(prefix="/ops/automation", tags=["ops-automation"])


def _require_operator(x_operator_token: Optional[str]):
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    token = (x_operator_token or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


@router.post("/tick")
def automation_tick(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(25, ge=1, le=200),
):
    _require_operator(x_operator_token)
    result = tick(limit=limit)

    # The existing Render cron already calls this authenticated endpoint every
    # five minutes. Run the separately gated RTM queues here so production does
    # not need another always-on worker service. Each queue remains disabled
    # unless its own explicit runtime flag is enabled.
    queue_results = {}
    generation_enabled = os.getenv("RTM_MANAGEMENT_GENERATION_WORKER_ENABLED") == "1"
    mail_enabled = os.getenv("RTM_CASE_MAIL_WORKER_ENABLED") == "1"
    invoice_enabled = os.getenv("RTM_INVOICE_WORKER_ENABLED") == "1"
    if generation_enabled or mail_enabled or invoice_enabled:
        from database import get_engine

        engine = get_engine()
        try:
            if generation_enabled:
                from rtm_core.management_generation import tick as generation_tick
                try:
                    generation_tick(engine)
                    queue_results["management_generation"] = "processed"
                except Exception:
                    queue_results["management_generation"] = "incomplete"
            if mail_enabled:
                from rtm_core.case_mail_runtime import tick as mail_tick
                try:
                    mail_result = mail_tick(engine)
                    queue_results["case_mail"] = "processed" if mail_result.get("ok") else "incomplete"
                except Exception:
                    queue_results["case_mail"] = "incomplete"
            if invoice_enabled:
                from rtm_core.invoice_provider import tick as invoice_tick
                try:
                    invoice_result=invoice_tick(engine,limit=min(limit,20))
                    queue_results["invoice"]="processed" if invoice_result.get("ok") else "incomplete"
                except Exception:
                    queue_results["invoice"]="incomplete"
        finally:
            engine.dispose()

    if queue_results:
        result["rtm_queues"] = queue_results
    return result


@router.get("/invoices")
def invoice_status(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(100, ge=1, le=500),
):
    """Accounting status only: no fiscal identity or payment credentials."""
    _require_operator(x_operator_token)
    from database import get_engine
    engine=get_engine()
    try:
        with engine.begin() as conn:
            rows=conn.execute(text("""
                SELECT id,case_id,stage,state,amount_cents,currency,stripe_session_id,
                       provider_invoice_number,provider_invoice_url,provider_payment_recorded_at,
                       provider_sent_at,issued_at,paid_at,last_error_code,created_at
                FROM rtm_invoice_requests
                ORDER BY created_at DESC LIMIT :limit
            """),{'limit':limit}).mappings().all()
        return {'ok':True,'invoices':[dict(row) for row in rows]}
    finally:
        engine.dispose()
