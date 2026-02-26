"""
RTM — Infractions Dispatcher (determinista)

Objetivo:
- Un único punto para decidir si hay plantilla determinista por tipo.
- Evitar duplicación de lógica en engine/generate.
- Detección robusta (especialmente SEMÁFORO con OCR sucio).

Uso típico:
    tpl = dispatch_deterministic_template(core, draft_body="")
    if tpl: asunto, cuerpo = tpl["asunto"], tpl["cuerpo"]
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ai.infractions.movil import is_movil_context, build_movil_strong_template
from ai.infractions.semaforo import build_semaforo_strong_template

# ✅ NUEVO: Condiciones del vehículo (SVL-CV-4 PRO)
# Ajusta el path si en tu repo el módulo está en otra ruta.
from ai.infractions.condiciones_vehiculo_cv4 import build_condiciones_vehiculo_strong_template


# -------------------------
# Utils
# -------------------------

def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


def build_raw_blob(core: Dict[str, Any], draft_body: str = "") -> str:
    """
    Compone un blob con:
    - raw_text_pdf/raw_text_vision/raw_text_blob (si existen)
    - hecho_imputado
    - cuerpo borrador (si se pasa)
    """
    core = core or {}
    parts = [
        _text(core.get("raw_text_pdf")),
        _text(core.get("raw_text_vision")),
        _text(core.get("raw_text_blob")),
        _text(core.get("hecho_imputado")),
        _text(draft_body),
    ]
    return " ".join([p for p in parts if p]).lower()


# -------------------------
# Robust semáforo detection
# -------------------------

def is_semaforo_context_robust(core: Dict[str, Any], draft_body: str = "") -> bool:
    """
    Detecta SEMÁFORO incluso con OCR sucio.
    Prioriza señales directas de rojo / semáforo y el artículo 146.
    """
    blob = build_raw_blob(core, draft_body=draft_body)

    sema_signals = [
        "semáforo", "semaforo",
        "fase roja",
        "luz roja",
        "cruce en rojo", "cruce con fase roja",
        "t/s roja", "ts roja",
        "señal luminosa roja", "senal luminosa roja",
        "línea de detención", "linea de detencion",
        "no respeta la luz roja", "no respetar la luz roja",
        "rebase la linea de detencion", "rebasar la linea de detencion",
    ]
    if any(s in blob for s in sema_signals):
        return True

    # Artículo típico
    if re.search(r"\bart\.?\s*146\b", blob) or re.search(r"\bart[ií]culo\s*146\b", blob) or re.search(r"\b146\s*[\.,]\s*1\b", blob):
        return True

    # Si analyze ya lo puso bien
    tipo = str((core or {}).get("tipo_infraccion") or "").lower().strip()
    if tipo == "semaforo":
        return True

    return False


# -------------------------
# Velocity context (simple)
# -------------------------

def is_velocity_context(core: Dict[str, Any], draft_body: str = "") -> bool:
    core = core or {}

    # 🔒 Guard absoluto: Art. 18 nunca es velocidad
    try:
        art = int(core.get("articulo_infringido_num"))
    except Exception:
        art = None
    if art == 18:
        return False

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "velocidad":
        return True

    blob = build_raw_blob(core, draft_body=draft_body)

    velocity_signals = [
        "exceso de velocidad",
        "radar",
        "cinemómetro", "cinemometro",
        "km/h"
    ]

    # Solo si hay señales reales en texto
    if any(k in blob for k in velocity_signals):
        return True

    return False
