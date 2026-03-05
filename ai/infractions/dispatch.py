"""
RTM — Infractions Dispatcher (determinista) — v3 (todo junto)

Orden:
1) Semáforo
2) Velocidad (hard-lock)
3) Móvil
4) Auriculares (Art.18.2)
5) Atención/Negligente (Art.3.1 / 18.1)
6) Condiciones vehículo (Art.12/15)
"""

from __future__ import annotations
import re
from typing import Any, Dict, Optional

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import is_movil_context, build_movil_strong_template
from ai.infractions.distracciones import is_auriculares_context, build_auriculares_strong_template
from ai.infractions.atencion import is_atencion_context, build_atencion_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template


def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


def build_raw_blob(core: Dict[str, Any], draft_body: str = "") -> str:
    core = core or {}
    parts = [
        _text(core.get("raw_text_pdf")),
        _text(core.get("raw_text_vision")),
        _text(core.get("raw_text_blob")),
        _text(core.get("hecho_imputado")),
        _text(draft_body),
    ]
    return " ".join([p for p in parts if p]).lower()


def is_semaforo_context_robust(core: Dict[str, Any], draft_body: str = "") -> bool:
    blob = build_raw_blob(core, draft_body=draft_body)
    sema_signals = [
        "semáforo", "semaforo", "fase roja", "luz roja",
        "cruce en rojo", "cruce con fase roja",
        "t/s roja", "ts roja",
        "señal luminosa roja", "senal luminosa roja",
        "línea de detención", "linea de detencion",
        "no respeta la luz roja", "no respetar la luz roja",
        "rebase la linea de detencion", "rebasar la linea de detencion",
    ]
    if any(s in blob for s in sema_signals):
        return True
    if re.search(r"\bart\.?\s*146\b", blob) or re.search(r"\bart[ií]culo\s*146\b", blob) or re.search(r"\b146\s*[\.,]\s*1\b", blob):
        return True
    tipo = str((core or {}).get("tipo_infraccion") or "").lower().strip()
    return tipo == "semaforo"


def is_velocity_context(core: Dict[str, Any], draft_body: str = "") -> bool:
    core = core or {}
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
    velocity_signals = ["exceso de velocidad", "radar", "cinemómetro", "cinemometro", "km/h"]
    return any(k in blob for k in velocity_signals)


\1# 🔒 Guard anti-velocidad: evita confundir 'antena homologada' del cinemómetro con homologación del vehículo.
\1if is_velocity_context(core, draft_body=draft_body):
\1    return False
\1blob = build_raw_blob(core, draft_body=draft_body)
    signals = [
        "condiciones reglamentarias",
        "veh r.d. 2822/98", "rd 2822/98", "r.d. 2822/98",
        "art. 12", "artículo 12", "articulo 12",
        "art. 15", "artículo 15", "articulo 15",
        "itv", "inspección técnica", "inspeccion tecnica", "caducad",
        "neumático", "neumatico", "banda de rodadura", "dibujo", "desgastad", "liso",
        "reforma", "modificación", "modificacion", "homolog",
        "alumbrado", "señalización óptica", "senalizacion optica", "luz trasera", "luces traseras",
        "deslumbr", "reflect", "reflej", "pulid", "como un espejo",
    ]
    if any(s in blob for s in signals):
        return True
    tipo = str((core or {}).get("tipo_infraccion") or "").lower().strip()
    return tipo in ["condiciones_vehiculo", "condiciones", "vehiculo", "vehículo"]


def dispatch_deterministic_template(core: Dict[str, Any], draft_body: str = "") -> Optional[Dict[str, str]]:
    core = core or {}

    # 1) Semáforo
    if is_semaforo_context_robust(core, draft_body=draft_body):
        return build_semaforo_strong_template(core)

    # 2) Velocidad (hard-lock): si es velocidad, devolvemos None para que generate.py use su pipeline específico de velocidad
    if is_velocity_context(core, draft_body=draft_body):
        return None

    # 3) Móvil
    if is_movil_context(core, draft_body or ""):
        return build_movil_strong_template(core)

    # 4) Auriculares
    if is_auriculares_context(core, draft_body or ""):
        return build_auriculares_strong_template(core)

    # 5) Atención / negligente
    if is_atencion_context(core, draft_body or ""):
        return build_atencion_strong_template(core, body=draft_body or "")

    # 6) Condiciones del vehículo (solo si NO es velocidad)
    if is_condiciones_vehiculo_context(core, draft_body=draft_body):
        tpl = build_condiciones_vehiculo_strong_template(core)
        if isinstance(tpl, dict) and tpl.get("asunto") and tpl.get("cuerpo"):
            return {"asunto": tpl["asunto"], "cuerpo": tpl["cuerpo"]}

    return None
    return None
