"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (SVL-ATN-PRO) — FAIL-SAFE

Nivel muy alto:
- Base determinista robusta (siempre disponible).
- + Capa IA opcional (RTM_ATENCION_AI=1) para personalizar con "chicha"
  usando SOLO texto del expediente (raw_text_pdf/raw_text_blob/hecho_imputado),
  sin inventar hechos.

Blindajes:
- Si OpenAI falla (401/429/timeout/…): NO rompe, vuelve a base determinista.
- Si OpenAI devuelve negativas tipo "I can't assist..." o similares: se ignora.
- Sanitiza OPENAI_API_KEY (quita comillas y espacios).
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import os
import re
import requests


# ----------------------------
# Helpers
# ----------------------------
def _sanitize_key(v: str) -> str:
    v = (v or "").strip()
    # quita comillas accidentales
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1].strip()
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

    return any(s in b for s in signals)


# ----------------------------
# Determinista con “chicha” por señales reales del hecho
# ----------------------------
def _deterministic_body(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE (RGC)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    b = _blob_lower(core, body=body)

    # señales específicas (sin inventar)
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
            "si la observación fue continua y por qué no se procedió a intervención inmediata si el riesgo era real."
        )

    menor_line = ""
    if has_menor:
        menor_line = (
            "La mención a la presencia de un menor/ocupante no suple la prueba de la conducta imputada. "
            "Si se pretende fundamentar o agravar la imputación en esa circunstancia, debe identificarse el encaje normativo específico "
            "(p. ej., normativa SRI) y la relación causal con el riesgo concreto, sin presunciones."
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


# ----------------------------
# IA opcional + fail-safe
# ----------------------------
def _looks_like_refusal(text: str) -> bool:
    t = (text or "").lower()
    bad = [
        "i can't assist",
        "i cannot assist",
        "i'm sorry",
        "lo siento",
        "no puedo ayudar",
        "no puedo asist",
        "missing bearer",
        "invalid_request_error",
    ]
    return any(x in t for x in bad)


def _ai_enhance(core: Dict[str, Any], base_body: str, body: str = "") -> Optional[str]:
    if (os.getenv("RTM_ATENCION_AI") or "").strip().lower() not in ("1", "true", "yes"):
        return None

    api_key = _sanitize_key(os.getenv("OPENAI_API_KEY") or "")
    if not api_key:
        return None

    model = (os.getenv("RTM_ATENCION_AI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o").strip()

    blob = _blob(core, body=body)[:12000]

    system_text = (
        "Eres un abogado experto en recursos administrativos de tráfico en España. "
        "Redactas alegaciones muy técnicas y específicas, pero NUNCA inventas hechos. "
        "Solo puedes basarte en el texto proporcionado. "
        "Si un dato no está claro, formula exigencias probatorias (no afirmes). "
        "Mantén tono profesional, contundente y verificable."
    )

    user_text = (
        "TEXTO DEL EXPEDIENTE (única fuente):\n\n"
        f"{blob}\n\n"
        "OBJETIVO: Mejorar el escrito base aportando 'chicha' y precisión.\n"
        "REGLAS:\n"
        "1) No inventes hechos ni cifras.\n"
        "2) Si se menciona tramo (p.ej. '1,5 km'), exige método de medición y continuidad.\n"
        "3) Si se menciona menor/ocupantes, pide encaje normativo y relación causal, sin suponer.\n"
        "4) Si hay conductas internas (bailar/palmas/volante), exige concreción y fiabilidad perceptiva.\n"
        "5) Pide prueba: denuncia íntegra, informe ampliatorio, grabaciones, anotaciones, testigos, croquis.\n\n"
        "Devuelve SOLO el CUERPO final (no asunto), en español, con estructura I/II/III y ALEGACIONES numeradas.\n\n"
        "ESCRITO BASE A MEJORAR:\n\n"
        f"{base_body}\n"
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
    except Exception:
        return None

    if not r.ok:
        return None

    try:
        data = r.json()
    except Exception:
        return None

    out_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out_text += c.get("text", "")

    out_text = (out_text or "").strip()
    if not out_text or _looks_like_refusal(out_text):
        return None

    return out_text


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    base = _deterministic_body(core, body=body)
    improved = _ai_enhance(core, base_body=base["cuerpo"], body=body)
    if isinstance(improved, str) and improved.strip():
        base["cuerpo"] = improved.strip()
    return base