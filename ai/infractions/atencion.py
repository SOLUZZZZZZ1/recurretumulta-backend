"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (ART. 3.1 / 18 RGC)
ATENCION_ULTRA_ADMIN v4 — Subtipos + IA opcional (FAIL-SAFE)

Objetivo:
- Mantener tono técnico-administrativo fuerte.
- Añadir “chicha” mediante BLOQUES ESPECÍFICOS activados por hechos (regex) sin inventar.
- Subtipos: bicicleta/ciclistas, arcén, carril/paralelo, atropello, menor/SRI, conductas internas, manos/acciones (morder uñas), etc.
- Capa IA opcional (RTM_ATENCION_AI=1) con fail-safe.

Salida: {"asunto","cuerpo"}
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import os
import re
import requests


def _sanitize_key(v: str) -> str:
    v = (v or "").strip()
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


def _detect_subtypes(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    is_bici = any(w in t for w in ["bicicleta", "ciclista", "ciclistas"])
    has_arcen = any(w in t for w in ["arcén", "arcen"])
    has_carril = any(w in t for w in ["carril", "carril derecho", "ocupando", "paralelo", "en paralelo"])
    has_atropello = any(w in t for w in ["atropello", "exponiéndose", "exponiendose"])
    has_menor = any(w in t for w in ["menor", "dos años", "dos anos", "asiento trasero", "sri", "sistema de retención", "sistema de retencion"])
    has_bail = any(w in t for w in ["bail", "palmas", "golpeando", "volante", "tambor"])
    has_morder_unas = any(w in t for w in ["mordía", "mordia", "morder", "uñas", "unas"])
    has_auriculares = any(w in t for w in ["auricular", "auriculares", "cascos", "sonido", "reproductor", "receptores", "reproductores"])

    km_val = None
    m_km = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*km\b", t)
    if m_km:
        km_val = m_km.group(1).replace(",", ".")

    return {
        "is_bici": is_bici,
        "has_arcen": has_arcen,
        "has_carril": has_carril,
        "has_atropello": has_atropello,
        "has_menor": has_menor,
        "has_bail": has_bail,
        "has_morder_unas": has_morder_unas,
        "has_auriculares": has_auriculares,
        "km_val": km_val,
    }


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
        "libertad de movimientos",
        "creando una situación de riesgo", "creando una situacion de riesgo",
        "riesgo", "peligro",
    ]

    if art_i in (3, 18) and any(s in b for s in signals):
        return True

    return any(s in b for s in signals)


def _deterministic_body(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE (RGC)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    full_text = _blob(core, body=body)
    subs = _detect_subtypes(full_text)

    blocks: List[str] = []

    if subs.get("km_val"):
        km = subs["km_val"]
        blocks.append(
            "BLOQUE ESPECÍFICO — TRAMO/SEGUIMIENTO\n\n"
            f"Se afirma un seguimiento/tramo de aproximadamente {km} km. "
            "Se exige indicar el método de determinación del tramo (punto inicial/final, referencias de vía), "
            "si la observación fue continua y por qué no se procedió a intervención inmediata si el riesgo era real y continuado.\n"
        )

    if subs.get("has_bail"):
        blocks.append(
            "BLOQUE ESPECÍFICO — CONDUCTAS INTERNAS IMPUTADAS\n\n"
            "Si se imputan conductas internas (p. ej. 'bailar', 'tocar palmas', 'golpear el volante'), "
            "debe describirse con precisión qué se observó (gestos concretos), durante cuánto tiempo, "
            "y cómo se aseguró la correcta identificación del conductor y la continuidad de la observación.\n"
        )

    if subs.get("has_morder_unas"):
        blocks.append(
            "BLOQUE ESPECÍFICO — 'LIBERTAD DE MOVIMIENTOS' / ACCIÓN CONCRETA\n\n"
            "Si se imputa falta de libertad de movimientos (p. ej. 'morderse las uñas'), debe precisarse cómo esa acción concreta "
            "afectaba de forma real y relevante al control del vehículo (duración, intensidad, maniobras asociadas), "
            "evitando presunciones automáticas.\n"
        )

    if subs.get("has_auriculares"):
        blocks.append(
            "BLOQUE ESPECÍFICO — POSIBLE SUBTIPO AURICULARES/SONIDO\n\n"
            "Si el hecho se relaciona con auriculares/cascos conectados a sonido, debe concretarse si el uso era efectivo durante la conducción "
            "(no mera tenencia), cuántos auriculares, ubicación, y cómo se constató, aportando soporte si existiera.\n"
        )

    if subs.get("is_bici") or subs.get("has_arcen") or subs.get("has_carril") or subs.get("has_atropello"):
        lines: List[str] = []
        lines.append(
            "BLOQUE ESPECÍFICO — CICLISTAS / BICICLETA / POSICIONAMIENTO EN VÍA\n\n"
            "Si el hecho se refiere a circulación en bicicleta, ocupación de carril, circulación en paralelo o uso del arcén, "
            "debe concretarse la conducta exacta y el encaje normativo específico en función de las circunstancias reales (tráfico, visibilidad, estado del arcén, señalización).\n"
        )
        if subs.get("has_arcen"):
            lines.append(
                "En particular, la mera mención a un arcén no basta: debe motivarse por qué era utilizable y seguro en ese punto "
                "(estado, obstáculos, continuidad), y por qué se afirma una obligación concreta de circular por él.\n"
            )
        if subs.get("has_carril"):
            lines.append(
                "Si se alega ocupación relevante del carril o circulación en paralelo, debe precisarse ancho del carril, posición exacta, "
                "presencia de otros usuarios y qué riesgo concreto se produjo (no meramente hipotético).\n"
            )
        if subs.get("has_atropello"):
            lines.append(
                "Si se menciona exposición a atropello, debe acreditarse el riesgo concreto: qué vehículos, maniobras, distancia y circunstancias objetivas "
                "sustentan esa conclusión.\n"
            )
        blocks.append("".join(lines))

    if subs.get("has_menor"):
        blocks.append(
            "BLOQUE ESPECÍFICO — MENOR/OCUPANTES\n\n"
            "La mención a la presencia de un menor/ocupante no suple la prueba de la conducta imputada. "
            "Si se pretende fundamentar o agravar la imputación en esa circunstancia, debe identificarse el encaje normativo específico "
            "(p. ej., normativa SRI) y la relación causal con el riesgo concreto, sin presunciones.\n"
        )

    blocks_text = "\n\n".join([b.strip() for b in blocks if b.strip()])
    if blocks_text:
        blocks_text = "\n\n" + blocks_text + "\n\n"

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
        "ALEGACIÓN SEGUNDA — INADECUADA SUBSUNCIÓN EN EL ART. 3.1 / ART. 18 RGC: RIESGO CONCRETO\n\n"
        "La conducción negligente o la falta de atención permanente exigen una conducta concreta y un riesgo real, específico y objetivable. "
        "No basta la referencia abstracta a 'riesgo/peligro'. Debe precisarse: conducta, riesgo, destinatario del riesgo y consecuencia objetiva.\n\n"
        "ALEGACIÓN TERCERA — CONCRECIÓN FÁCTICA Y COHERENCIA INTERNA\n\n"
        "Si se afirma conducta prolongada o a lo largo de un tramo, debe precisarse el método de observación, continuidad y circunstancias. "
        "Un peligro real y continuado requiere motivación reforzada.\n"
        f"{blocks_text}"
        "ALEGACIÓN CUARTA — EXPEDIENTE ÍNTEGRO Y PRUEBA COMPLETA\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones, testigos, croquis) para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de motivación suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def _looks_like_refusal(text: str) -> bool:
    t = (text or "").lower()
    bad = ["i can't assist", "i cannot assist", "i'm sorry", "lo siento", "no puedo", "missing bearer", "invalid_request_error"]
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
        "Redactas alegaciones técnicas, precisas y contundentes, pero NUNCA inventas hechos. "
        "Solo puedes basarte en el texto proporcionado. "
        "Mantén tono técnico-administrativo."
    )

    user_text = (
        "TEXTO DEL EXPEDIENTE (única fuente):\n\n" + blob + "\n\n"
        "OBJETIVO: Reescribir el CUERPO con mayor precisión usando los hechos concretos (bicicleta/arcén/carril/atropello/menor/tramo/acciones).\n"
        "REGLAS: no inventes; refuerza subsunción y estándar probatorio; activa bloques por hechos; pide prueba completa.\n\n"
        "Devuelve SOLO el CUERPO final, con I/II/III y ALEGACIONES numeradas.\n\n"
        "CUERPO BASE A MEJORAR:\n\n" + base_body
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
