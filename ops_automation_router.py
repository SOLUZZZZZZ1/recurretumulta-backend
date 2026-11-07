# ops_automation_router.py
import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query

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
    if generation_enabled or mail_enabled:
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
        finally:
            engine.dispose()

    if queue_results:
        result["rtm_queues"] = queue_results
    return result
