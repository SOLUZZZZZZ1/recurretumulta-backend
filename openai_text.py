import json
import os
import re
from typing import Any, Dict, Optional

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


SYSTEM_PROMPT = """
Eres un asistente que extrae información estructurada de denuncias administrativas de tráfico en España.

Devuelve SIEMPRE un JSON válido con los siguientes campos.
Si un dato no aparece claramente en el documento, devuelve null.

Campos:
- organismo
- expediente_ref
- importe
- fecha_notificacion
- fecha_documento
- tipo_sancion
- pone_fin_via_administrativa
- plazo_recurso_sugerido
- hecho_denunciado_literal
- observaciones

Reglas estrictas para hecho_denunciado_literal:
1) Extrae SOLO la frase del hecho denunciado o del relato fáctico principal.
2) NO incluyas bloques administrativos, importes, puntos, tipificación, clasificación, aparato, valor de la prueba, reducciones, datos del vehículo, encabezados, ni observaciones accesorias.
3) El texto debe ser corto, claro y útil para clasificar la infracción. Máximo 220 caracteres.
4) Si el documento mezcla el hecho con ruido administrativo y no puedes aislarlo con seguridad, devuelve null.
5) Si el supuesto "hecho" contiene palabras absurdas o incoherentes para tráfico sin contexto claro (por ejemplo "guante") o parece OCR roto, devuelve null.
6) Mantén el texto lo más literal posible, pero limpiando basura obvia.
7) No inventes ni completes datos faltantes.

Ejemplos válidos:
- "NO RESPETAR LA LUZ ROJA NO INTERMITENTE DE UN SEMÁFORO"
- "CIRCULAR A 153 KM/H TENIENDO LIMITADA LA VELOCIDAD A 120 KM/H"
- "UTILIZAR MANUALMENTE TELÉFONO MÓVIL DURANTE LA CONDUCCIÓN"

Ejemplos inválidos:
- "CLASIFICACIÓN: GRAVE IMPORTE 200 € PUNTOS 4"
- "HECHO: ... APARATO 513 VALOR DE LA PRUEBA ..."
- "CIRCULAR SIN HACER USO DE GUANTE"
"""

_ADMIN_TOKENS = [
    "tipificacion",
    "tipificación",
    "clasificacion",
    "clasificación",
    "valor de la prueba",
    "aparato",
    "reduccion",
    "reducción",
    "bonificacion",
    "bonificación",
    "importe",
    "puntos",
    "agente",
    "total principal",
    "para ingresar",
    "datos vehiculo",
    "datos vehículo",
    "boletin",
    "boletín",
    "fecha limite",
    "fecha límite",
]

_BAD_HECHO_TOKENS = [
    "guante",
    "calzoncillo",
    "pene",
    "desnudo",
]


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _clean_value(text: str) -> str:
    t = _safe_str(text).replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    return t


def _sanitize_hecho(value: Optional[str]) -> Optional[str]:
    txt = _clean_value(value)
    if not txt:
        return None

    low = txt.lower()

    low = re.sub(r"^(hecho denunciado|hecho imputado|hecho infringido|hecho infractor|hecho)\s*[:\-]?\s*", "", low, flags=re.IGNORECASE)
    txt = re.sub(r"^(hecho denunciado|hecho imputado|hecho infringido|hecho infractor|hecho)\s*[:\-]?\s*", "", txt, flags=re.IGNORECASE)

    for token in _ADMIN_TOKENS:
        idx = low.find(token)
        if idx > 0:
            txt = txt[:idx].strip(" ,;:-")
            low = txt.lower()

    txt = re.sub(r"\s+", " ", txt).strip(" ,;:-")
    low = txt.lower()

    if len(txt) > 220:
        txt = txt[:220].rsplit(" ", 1)[0].strip(" ,;:-")
        low = txt.lower()

    words = [w for w in re.split(r"\s+", low) if w]
    if len(words) < 4:
        return None

    if any(bad in low for bad in _BAD_HECHO_TOKENS):
        return None

    # Si no hay ningún verbo/estructura típica de hecho, mejor no forzar.
    if not any(s in low for s in [
        "no respetar",
        "circular",
        "circulaba",
        "conducir",
        "utilizar",
        "utilizando",
        "fase roja",
        "luz roja",
        "semaforo",
        "semáforo",
        "km/h",
        "velocidad",
        "auricular",
        "cinturon",
        "cinturón",
        "itv",
        "seguro",
        "alumbrado",
        "linea continua",
        "línea continua",
    ]):
        return None

    return txt or None


def _postprocess_data(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    out = dict(data)

    out["organismo"] = _clean_value(out.get("organismo"))
    out["expediente_ref"] = _clean_value(out.get("expediente_ref"))
    out["tipo_sancion"] = _clean_value(out.get("tipo_sancion"))
    out["fecha_notificacion"] = _clean_value(out.get("fecha_notificacion"))
    out["fecha_documento"] = _clean_value(out.get("fecha_documento"))
    out["plazo_recurso_sugerido"] = _clean_value(out.get("plazo_recurso_sugerido"))
    out["observaciones"] = _clean_value(out.get("observaciones"))

    hecho = _sanitize_hecho(out.get("hecho_denunciado_literal"))
    out["hecho_denunciado_literal"] = hecho

    if out.get("importe") not in (None, ""):
        try:
            raw = _clean_value(out.get("importe")).replace("€", "").replace(",", ".")
            m = re.search(r"\d+(?:\.\d+)?", raw)
            out["importe"] = float(m.group(0)) if m else None
        except Exception:
            out["importe"] = None
    else:
        out["importe"] = None

    if isinstance(out.get("pone_fin_via_administrativa"), str):
        val = out["pone_fin_via_administrativa"].strip().lower()
        if val in ("true", "si", "sí", "1"):
            out["pone_fin_via_administrativa"] = True
        elif val in ("false", "no", "0"):
            out["pone_fin_via_administrativa"] = False
        else:
            out["pone_fin_via_administrativa"] = None

    return out


def extract_from_text(text: str) -> Dict[str, Any]:
    prompt = f"""
Analiza el siguiente documento de denuncia administrativa y extrae los campos solicitados.

Documento:

{text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response.choices[0].message.content)
    except Exception:
        data = {}

    return _postprocess_data(data)
