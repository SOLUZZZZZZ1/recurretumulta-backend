"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (SVL-ATN-PRO)

Nivel MUY alto:
- Base determinista robusta (siempre disponible).
- + Capa IA opcional (si hay OPENAI_API_KEY) para personalizar con "chicha"
  usando SOLO texto del expediente (raw_text_pdf/raw_text_blob/hecho_imputado),
  sin inventar hechos.

Uso:
- is_atencion_context(core, body="") -> bool
- build_atencion_strong_template(core, body="") -> {"asunto","cuerpo"}

Configuración:
- RTM_ATENCION_AI=1  -> activa personalización IA (si hay clave)
- RTM_ATENCION_AI_MODEL (opcional) -> modelo (por defecto OPENAI_MODEL o gpt-4o)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import os
import re

# Reutilizamos requests como en openai_text.py
import requests


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _blob(core: Dict[str, Any], body: str = "") -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if isinstance(body, str) and body.strip():
        parts.append(body)
    return " \n".join(parts).strip()


def _blob_lower(core: Dict[str, Any], body: str = "") -> str:
    return _blob(core, body=body).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob_lower(core, body=body)

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in ("atencion", "atención", "negligente", "conduccion_negligente", "conducción_negligente"):
        return True

    # Artículo explícito (3 o 18)
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None else None
    except Exception:
        art_i = None

    signals = [
        "conducción negligente", "conduccion negligente",
        "no mantener la atención permanente", "no mantener la atencion permanente",
        "atención permanente", "atencion permanente",
        "distracción", "distraccion",
        "creando una situación de riesgo", "creando una situacion de riesgo",
        "riesgo", "peligro",
    ]

    if art_i in (3, 18) and any(s in b for s in signals):
        return True

    # Señales fuertes aunque no haya artículo
    return any(s in b for s in signals)


def _deterministic_body(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE (RGC)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    b = _blob_lower(core, body=body)

    # Señales específicas del caso (sin inventar)
    tramo = None
    m_km = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*km\b", b)
    if m_km:
        tramo = m_km.group(1).replace(",", ".")
    has_menor = any(x in b for x in ["menor", "dos años", "dos anos", "asiento trasero", "sri", "sistema de retención", "sistema de retencion"])
    has_bailando = any(x in b for x in ["bail", "palmas", "golpeando", "volante", "tambor", "bailando"])

    tramo_line = ""
    if tramo:
        tramo_line = (
            f"Se afirma un seguimiento/tramo de aproximadamente {tramo} km. "
            "Se exige indicar el método de determinación del tramo (punto inicial/final, referencias de vía), "
            "y por qué no se procedió a intervención inmediata si el riesgo era real."
        )

    menor_line = ""
    if has_menor:
        menor_line = (
            "La mención a la presencia de un menor/ocupante no suple la prueba de la conducta imputada. "
            "Si se pretende fundamentar o agravar la imputación en esa circunstancia, debe identificarse el encaje normativo específico "
            "(p. ej., normativa SRI) y la relación causal con el riesgo concreto."
        )

    conducta_line = ""
    if has_bailando:
        conducta_line = (
            "Si se imputan conductas internas (p. ej. 'bailar', 'tocar palmas', 'golpear el volante'), "
            "debe describirse con precisión qué se observó (gestos concretos), durante cuánto tiempo, "
            "y cómo se aseguró la correcta identificación del conductor y la continuidad de la observación."
        )

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRESUNCIÓN DE INOCENCIA, CARGA PROBATORIA Y MOTIVACIÓN\n\n"
        "En el procedimiento sancionador rige la presunción de inocencia y la carga de la prueba corresponde a la Administración. "
        "La imputación debe apoyarse en hechos concretos, verificables y motivación individualizada (no fórmulas estereotipadas).\n\n"
        "ALEGACIÓN SEGUNDA — TIPICIDAD (ART. 3.1 / ART. 18): RIESGO CONCRETO Y HECHO CIRCUNSTANCIADO\n\n"
        "La conducción negligente o la falta de atención permanente exigen: (i) conducta concreta; (ii) riesgo real y objetivable; "
        "y (iii) relación causal entre conducta y riesgo. Debe precisarse, como mínimo:\n"
        "1) Conducta exacta observada y por qué encaja en el tipo aplicado.\n"
        "2) Riesgo concreto: para quién, dónde, cómo se manifestó (maniobra evasiva, invasión de carril, frenada brusca, etc.).\n"
        "3) Circunstancias del tráfico/visibilidad y posición del agente (distancia, ángulo, iluminación, obstáculos).\n"
        "4) Duración aproximada del hecho y momento exacto de observación.\n\n"
        f"{(tramo_line + '\n\n') if tramo_line else ''}"
        f"{(conducta_line + '\n\n') if conducta_line else ''}"
        "En ausencia de concreción suficiente y riesgo objetivable, no puede tenerse por acreditada la infracción.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y PRUEBA COMPLETA\n\n"
        "Se solicita la aportación del expediente íntegro (denuncia/boletín completo, informe ampliatorio si existe, diligencias, propuesta y resolución) "
        "y cualquier soporte objetivo disponible (grabación, fotografías, anotaciones), para posibilitar contradicción efectiva.\n\n"
        f"{(menor_line + '\n\n') if menor_line else ''}"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de motivación individualizada.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def _ai_enhance(core: Dict[str, Any], base_body: str, body: str = "") -> Optional[str]:
    # Feature flag
    if (os.getenv("RTM_ATENCION_AI") or "").strip() not in ("1", "true", "TRUE", "yes", "YES"):
        return None

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    model = (os.getenv("RTM_ATENCION_AI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o").strip()

    # Sólo usamos el texto del expediente; recortamos para seguridad
    blob = _blob(core, body=body)
    blob = blob[:12000]

    system_text = (
        "Eres un abogado experto en recursos administrativos de tráfico en España. "
        "Redactas alegaciones muy técnicas y específicas, pero NUNCA inventas hechos. "
        "Solo puedes basarte en el texto proporcionado. "
        "Si un dato no está claro, formula la exigencia probatoria (no afirmes). "
        "Mantén tono profesional, contundente y verificable."
    )

    user_text = (
        "TEXTO DEL EXPEDIENTE (única fuente):\n\n"
        f"{blob}\n\n"
        "OBJETIVO: Mejorar el escrito base aportando 'chicha' y precisión.\n"
        "REGLAS ESTRICTAS:\n"
        "1) No inventes hechos ni cifras.\n"
        "2) Si el texto menciona tramo (p.ej. '1,5 km'), exige método de medición y continuidad.\n"
        "3) Si menciona menor/ocupantes, pide encaje normativo y relación causal, sin suponer.\n"
        "4) Si describe conductas internas (bailar/palmas/volante), exige concreción (qué, cuándo, cómo) y fiabilidad perceptiva.\n"
        "5) Añade peticiones de prueba: denuncia íntegra, informe ampliatorio, grabaciones, anotaciones, testigos, croquis, etc.\n\n"
        "Devuelve SOLO el texto completo del CUERPO final (no asunto), en español, con estructura I/II/III y ALEGACIONES numeradas.\n\n"
        "ESCRITO BASE A MEJORAR (mantén su estructura, pero mejora):\n\n"
        f"{base_body}\n"
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    if not r.ok:
        return None

    data = r.json()
    out_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out_text += c.get("text", "")
    out_text = (out_text or "").strip()
    return out_text or None


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    base = _deterministic_body(core, body=body)
    improved = _ai_enhance(core, base_body=base["cuerpo"], body=body)
    if isinstance(improved, str) and improved.strip():
        base["cuerpo"] = improved.strip()
    return base
