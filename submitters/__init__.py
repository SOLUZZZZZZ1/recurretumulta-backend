from __future__ import annotations

from typing import Any

from .registro import RegistroSubmitter
from .dgt import DGTSubmitter


def pick_submitter(*, case_id: str, engine) -> Any:
    """
    Selección simple por ahora:
    - Por defecto: Registro General (multi-administración).
    - DGT quedará como especialización cuando exista lógica para detectarlo.
    """
    # TODO: cuando tengamos campos en cases/extractions que indiquen organismo sancionador,
    # elegir aquí el submitter correspondiente.
    return RegistroSubmitter()
