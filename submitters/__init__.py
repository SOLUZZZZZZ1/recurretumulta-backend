from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import text

from destination_resolver import resolve_destination
from .registro import RegistroSubmitter
from .dgt import DGTSubmitter


def _build_case_data(engine, case_id: str) -> Dict[str, Any]:
    """
    Carga datos mínimos del expediente para pasarlos al resolver de destino.
    Mezcla:
      - cases.interested_data
      - cases.organismo / expediente_ref / contact_email
      - último ai_expediente_result.payload.raw_result (si existe)
    """
    case_data: Dict[str, Any] = {}

    with engine.begin() as conn:
        row = conn.execute(
            text(
                '''
                SELECT
                    COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                    organismo,
                    expediente_ref,
                    contact_email
                FROM cases
                WHERE id = :id
                '''
            ),
            {"id": case_id},
        ).fetchone()

        if row:
            interested_data = row[0] if isinstance(row[0], dict) else {}
            case_data.update(interested_data or {})
            if row[1]:
                case_data["organismo"] = row[1]
            if row[2]:
                case_data["expediente_ref"] = row[2]
            if row[3]:
                case_data["contact_email"] = row[3]

        ev = conn.execute(
            text(
                '''
                SELECT payload
                FROM events
                WHERE case_id = :id
                  AND type = 'ai_expediente_result'
                ORDER BY created_at DESC
                LIMIT 1
                '''
            ),
            {"id": case_id},
        ).fetchone()

        if ev and isinstance(ev[0], dict):
            payload = ev[0]
            raw_result = payload.get("raw_result") if isinstance(payload.get("raw_result"), dict) else {}
            delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}

            case_data.update(raw_result or {})
            if delivery:
                case_data["_delivery_hint"] = delivery

    return case_data


def pick_submitter(*, case_id: str, engine) -> Any:
    """
    Selección automática del submitter según destino detectado.
    Reglas iniciales:
    - DGT -> DGTSubmitter
    - resto -> RegistroSubmitter
    """
    case_data = _build_case_data(engine, case_id)
    delivery_hint = case_data.get("_delivery_hint") if isinstance(case_data.get("_delivery_hint"), dict) else {}

    if delivery_hint:
        entity = str(delivery_hint.get("entity") or "").strip().lower()
    else:
        delivery = resolve_destination(case_data)
        entity = str(delivery.get("entity") or "").strip().lower()

    if entity == "dgt":
        return DGTSubmitter()

    return RegistroSubmitter()
