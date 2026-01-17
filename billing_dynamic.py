import os
from typing import Any, Dict

import stripe
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/billing", tags=["billing-dynamic"])

class CheckoutIn(BaseModel):
    case_id: str

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _require_env(name: str) -> str:
    v = _env(name)
    if not v:
        raise HTTPException(status_code=500, detail=f"Falta variable de entorno: {name}")
    return v

def _frontend_base() -> str:
    return _env("FRONTEND_BASE_URL", "https://www.recurretumulta.eu").rstrip("/")

def _count_docs(case_id: str) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        r = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE case_id = :id"),
            {"id": case_id},
        ).fetchone()
        return int(r[0] or 0) if r else 0

def _calc(case_id: str) -> Dict[str, Any]:
    total_docs = _count_docs(case_id)
    docs_extra = max(0, total_docs - 1)

    # Importes en céntimos para evitar floats
    base_cents = int(_env("PRICE_BASE_DGT_CENTS", "2990") or "2990")
    extra_cents = int(_env("PRICE_EXTRA_DOC_CENTS", "500") or "500")

    total_cents = base_cents + docs_extra * extra_cents
    return {
        "case_id": case_id,
        "docs_total": total_docs,
        "docs_extra": docs_extra,
        "base_cents": base_cents,
        "extra_cents": extra_cents,
        "total_cents": total_cents,
    }

@router.get("/quote-dgt/{case_id}")
def quote_dgt(case_id: str) -> Dict[str, Any]:
    """Devuelve desglose de precio (solo lectura) para mostrar antes del pago."""
    cid = (case_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="case_id requerido")
    q = _calc(cid)
    return {"ok": True, **q}

@router.post("/checkout-dgt")
def checkout_dgt(payload: CheckoutIn) -> Dict[str, Any]:
    """
    Stripe Checkout (DGT) con precio dinámico por nº de documentos:
    - Base (incluye 1 doc): STRIPE_PRICE_ID_DGT x1
    - Extra: STRIPE_PRICE_DOCUMENTO_EXTRA x max(0, docs_total - 1)
    """
    case_id = (payload.case_id or "").strip()
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id requerido")

    stripe.api_key = _require_env("STRIPE_SECRET_KEY")
    price_base = _require_env("STRIPE_PRICE_ID_DGT")
    price_extra = _require_env("STRIPE_PRICE_DOCUMENTO_EXTRA")

    q = _calc(case_id)
    docs_extra = q["docs_extra"]

    line_items = [{"price": price_base, "quantity": 1}]
    if docs_extra > 0:
        line_items.append({"price": price_extra, "quantity": docs_extra})

    base = _frontend_base()
    success_url = f"{base}/#/pago-ok?case={case_id}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base}/#/pago-cancel?case={case_id}"

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=case_id,
            metadata={
                "case_id": case_id,
                "product_code": "DGT_RECURSO_PRESENTADO",
                "docs_total": str(q["docs_total"]),
                "docs_extra": str(docs_extra),
                "price_base_cents": str(q["base_cents"]),
                "price_extra_cents": str(q["extra_cents"]),
                "price_total_cents": str(q["total_cents"]),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {e}")

    return {"ok": True, "checkout_url": session.url, **q}
