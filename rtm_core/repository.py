"""Lectura de expedientes para RTM CORE sin atribuir autoridad jurídica."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text

from rtm_core.readiness import ReviewReadiness, evaluate_review_readiness


@dataclass(frozen=True)
class CaseReviewSnapshot:
    case_id: str
    authorized: bool
    authorized_at: Any
    payment_status: str
    interested_data: Mapping[str, Any]
    department: str
    case_type: str
    category: str
    source_module: str
    customer_comment: str
    contact_email: str
    status: str
    document_kinds: tuple[str, ...]


def load_case_review_snapshot(conn, case_id: str) -> CaseReviewSnapshot:
    row = conn.execute(
        text(
            """
            SELECT
                id,
                COALESCE(authorized, FALSE) AS authorized,
                authorized_at,
                COALESCE(payment_status, '') AS payment_status,
                COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                COALESCE(department, '') AS department,
                COALESCE(case_type, '') AS case_type,
                COALESCE(category, '') AS category,
                COALESCE(source_module, '') AS source_module,
                COALESCE(customer_comment, '') AS customer_comment,
                COALESCE(contact_email, '') AS contact_email,
                COALESCE(status, '') AS status
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")

    mapping = row._mapping
    docs = conn.execute(
        text(
            """
            SELECT COALESCE(kind, '') AS kind
            FROM documents
            WHERE case_id = :id
            ORDER BY created_at ASC
            """
        ),
        {"id": case_id},
    ).fetchall()

    interested_data = mapping["interested_data"]
    if not isinstance(interested_data, dict):
        interested_data = {}

    return CaseReviewSnapshot(
        case_id=str(mapping["id"]),
        authorized=bool(mapping["authorized"]),
        authorized_at=mapping["authorized_at"],
        payment_status=str(mapping["payment_status"] or ""),
        interested_data=interested_data,
        department=str(mapping["department"] or ""),
        case_type=str(mapping["case_type"] or ""),
        category=str(mapping["category"] or ""),
        source_module=str(mapping["source_module"] or ""),
        customer_comment=str(mapping["customer_comment"] or ""),
        contact_email=str(mapping["contact_email"] or ""),
        status=str(mapping["status"] or ""),
        document_kinds=tuple(str(doc[0] or "") for doc in docs),
    )


def build_case_review_readiness(snapshot: CaseReviewSnapshot) -> ReviewReadiness:
    return evaluate_review_readiness(
        case_id=snapshot.case_id,
        interested_data=dict(snapshot.interested_data),
        authorized=snapshot.authorized,
        document_kinds=snapshot.document_kinds,
        department=snapshot.department,
        case_type=snapshot.case_type,
        category=snapshot.category,
        source_module=snapshot.source_module,
        contact_email=snapshot.contact_email,
        customer_comment=snapshot.customer_comment,
    )
