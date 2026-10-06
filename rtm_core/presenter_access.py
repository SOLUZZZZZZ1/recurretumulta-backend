"""Read-only Presenter availability for the authenticated OPS operator."""
from __future__ import annotations

from rtm_presenter_policy import (
    PRESENTER_DOCUMENT_READ_PERMISSION,
    load_presenter_runtime_configuration,
)
from rtm_presenter_service import SqlPresenterRepository


def presenter_available_for_scope(conn, *, case_id: str, scope) -> bool:
    """Use the same accepted case assignment as Presenter; never grant access."""
    if (getattr(scope, "individual_session", False) is not True
            or PRESENTER_DOCUMENT_READ_PERMISSION not in getattr(scope, "permissions", ())):
        return False
    try:
        load_presenter_runtime_configuration(require_enabled=True)
        # A missing A1-S schema must not abort the surrounding OPS transaction.
        with conn.begin_nested():
            return SqlPresenterRepository().has_active_synthetic_case_access(
                conn, case_id=case_id, operator_id=scope.operator_id,
            ) is True
    except Exception:
        return False
