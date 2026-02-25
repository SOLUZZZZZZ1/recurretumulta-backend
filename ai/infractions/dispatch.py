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
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "velocidad":
        return True
    if core.get("velocidad_medida_kmh") or core.get("velocidad_limite_kmh"):
        return True

    blob = build_raw_blob(core, draft_body=draft_body)
    return any(k in blob for k in ["km/h", "exceso de velocidad", "radar", "cinemómetro", "cinemometro"])


# -------------------------
# Condiciones del vehículo (Art. 12 / 15)
# -------------------------

def is_condiciones_vehiculo_context(core: Dict[str, Any], draft_body: str = "") -> bool:
    """
    Detector determinista para activar SVL-CV-4.
    Señales:
    - Art. 12 / Art. 15
    - ITV / neumáticos / reformas / alumbrado
    - deslumbramiento por superficies reflectantes/pulidas
    """
    blob = build_raw_blob(core, draft_body=draft_body)

    signals = [
        # Núcleo
        "condiciones reglamentarias",
        "veh r.d. 2822/98", "rd 2822/98", "r.d. 2822/98",
        "art. 12", "artículo 12", "articulo 12",
        "art. 15", "artículo 15", "articulo 15",

        # ITV
        "itv", "inspección técnica", "inspeccion tecnica", "caducad",

        # Neumáticos
        "neumático", "neumatico", "neumáticos", "neumaticos",
        "banda de rodadura", "dibujo", "desgastad", "liso",

        # Reformas/mods
        "reforma", "modificación", "modificacion", "homolog", "no autorizada", "sin autorización", "sin autorizacion",

        # Alumbrado
        "alumbrado", "señalización óptica", "senalizacion optica", "luz trasera", "luces traseras",

        # Reflectante/deslumbramiento
        "deslumbr", "reflect", "reflej", "pulid", "como un espejo",
    ]

    if any(s in blob for s in signals):
        return True

    tipo = str((core or {}).get("tipo_infraccion") or "").lower().strip()
    if tipo in ["condiciones_vehiculo", "condiciones", "vehiculo", "vehículo"]:
        return True

    return False


# -------------------------
# Dispatcher
# -------------------------

def dispatch_deterministic_template(core: Dict[str, Any], draft_body: str = "") -> Optional[Dict[str, str]]:
    """
    Devuelve {"asunto","cuerpo"} si hay plantilla determinista aplicable.
    Orden (Tráfico):
      1) Semáforo robusto
      2) Móvil
      3) Condiciones del vehículo (Art. 12 / 15)
      4) Velocidad (aquí normalmente llamas a tu VSE-1 en generate.py)
    """
    core = core or {}

    # 1) SEMÁFORO
    if is_semaforo_context_robust(core, draft_body=draft_body):
        return build_semaforo_strong_template(core)

    # 2) MÓVIL
    if is_movil_context(core, draft_body or ""):
        return build_movil_strong_template(core)

    # 3) CONDICIONES DEL VEHÍCULO (SVL-CV-4)
    if is_condiciones_vehiculo_context(core, draft_body=draft_body):
        tpl = build_condiciones_vehiculo_strong_template(core)
        # El módulo CV-4 devuelve más campos, pero aquí respetamos la interfaz {"asunto","cuerpo"}.
        if isinstance(tpl, dict) and tpl.get("asunto") and tpl.get("cuerpo"):
            return {"asunto": tpl["asunto"], "cuerpo": tpl["cuerpo"]}

    # 4) VELOCIDAD -> en tu sistema lo resuelves en generate.py con VSE-1 determinista fijo
    # (no devolvemos aquí para no duplicar el VSE-1, pero dejamos el hook)
    if is_velocity_context(core, draft_body=draft_body):
        return None

    return None
