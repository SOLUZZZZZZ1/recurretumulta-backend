# -*- coding: utf-8 -*-
"""Tipicidad v3 — Capa 0 transversal (prudente)

Objetivo:
- Verificar coherencia entre el artículo/norma citado y los hechos imputados (señales objetivas).
- No inventar. Si no es concluyente -> unknown (None).

Salidas:
- match: True / False / None
- expected_type: tipo esperado por artículo (si se puede)
- inferred_type: tipo inferido por señales en hechos/documentos
- severity: normal / reforzado / critico
- dominant_argument: tipicidad / motivacion_tipo / none
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# Mapa cerrado de alta confianza (ampliable)
ARTICLE_TYPE_MAP = {
    "RGC": {
        48: "velocidad",
        18: "atencion",
        167: "marcas_viales",
        12: "condiciones_vehiculo",
        15: "condiciones_vehiculo",
    },
    "RDL 8/2004": {2: "seguro"},
}

def _norma_key_from_hint(extraction_core: Dict[str, Any]) -> str:
    hint = (extraction_core or {}).get("norma_hint") or ""
    h = str(hint).upper()
    if "RDL 8/2004" in h or "8/2004" in h:
        return "RDL 8/2004"
    if "RGC" in h or "REGLAMENTO GENERAL DE CIRCUL" in h:
        return "RGC"
    return ""

def _get_article_num(extraction_core: Dict[str, Any]) -> Optional[int]:
    art = (extraction_core or {}).get("articulo_infringido_num")
    if isinstance(art, int):
        return art
    if isinstance(art, str) and art.strip().isdigit():
        try:
            return int(art.strip())
        except Exception:
            return None
    # fallback suave: buscar "art. 48" en json
    blob = json.dumps(extraction_core or {}, ensure_ascii=False)
    m = re.search(r"\bart\.?\s*(\d{1,3})\b", blob, flags=re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None

def _expected_type_from_article(extraction_core: Dict[str, Any]) -> Optional[str]:
    nk = _norma_key_from_hint(extraction_core or {})
    art = _get_article_num(extraction_core or {})
    if not nk or art is None:
        return None
    return (ARTICLE_TYPE_MAP.get(nk) or {}).get(art)

def _infer_type_from_signals(extraction_core: Dict[str, Any], docs: List[Dict[str, Any]]) -> Optional[str]:
    parts: List[str] = []
    try:
        parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass
    for d in docs or []:
        t = d.get("text_excerpt") or ""
        if t:
            parts.append(t)
    blob = " ".join(parts).lower()

    if any(k in blob for k in ["km/h", "cinemómetro", "cinemometro", "radar", "exceso de velocidad", "velocidad medida", "velocidad corregida"]):
        return "velocidad"
    if any(k in blob for k in ["semáforo", "semaforo", "fase roja", "luz roja"]):
        return "semaforo"
    if any(k in blob for k in ["teléfono", "telefono", "móvil", "movil"]):
        return "movil"
    if any(k in blob for k in ["seguro obligatorio", "sin seguro", "póliza", "poliza", "lsoa", "8/2004"]):
        return "seguro"
    if any(k in blob for k in ["itv", "inspección técnica", "inspeccion tecnica"]):
        return "itv"
    return None

def build_tipicity_verdict(docs: List[Dict[str, Any]], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    expected = _expected_type_from_article(extraction_core or {})
    inferred = _infer_type_from_signals(extraction_core or {}, docs or [])

    verdict = {
        "ok": True,
        "match": None,  # True/False/None
        "expected_type": expected,
        "inferred_type": inferred,
        "article": _get_article_num(extraction_core or {}),
        "norma_key": _norma_key_from_hint(extraction_core or {}),
        "severity": "normal",
        "dominant_argument": "none",
        "notes": "",
    }

    if not expected or not inferred:
        verdict["match"] = None
        verdict["severity"] = "reforzado"
        verdict["dominant_argument"] = "motivacion_tipo"
        verdict["notes"] = "insufficient_data_for_strict_match"
        return verdict

    if str(expected).lower().strip() == str(inferred).lower().strip():
        verdict["match"] = True
        verdict["severity"] = "normal"
        verdict["dominant_argument"] = "none"
        verdict["notes"] = "match_ok"
        return verdict

    verdict["match"] = False
    verdict["severity"] = "critico"
    verdict["dominant_argument"] = "tipicidad"
    verdict["notes"] = "mismatch_clear"
    return verdict

def build_tipicity_text_blocks(verdict: Dict[str, Any]) -> Dict[str, str]:
    blocks: Dict[str, str] = {}
    m = (verdict or {}).get("match", None)

    if m is False:
        nk = (verdict or {}).get("norma_key")
        art = (verdict or {}).get("article")
        exp = (verdict or {}).get("expected_type")
        inf = (verdict or {}).get("inferred_type")
        blocks["primary_title"] = "ALEGACIÓN PRIMERA — VULNERACIÓN DEL PRINCIPIO DE TIPICIDAD Y ERRÓNEA SUBSUNCIÓN NORMATIVA"
        blocks["primary_body"] = (
            "El Derecho Administrativo Sancionador exige subsunción exacta entre el hecho descrito y el precepto aplicado "
            "(principio de tipicidad y legalidad sancionadora). Si el artículo citado no se corresponde con la conducta imputada, "
            "se genera indefensión material y procede el archivo."
        )
        if nk and art and exp and inf:
            blocks["mismatch_line"] = (
                f"A la vista de los elementos disponibles, el precepto citado ({nk} art. {art}) "
                f"parece corresponderse con '{exp}', mientras que los hechos/documentación apuntan a '{inf}'. "
                "Se solicita identificación expresa del artículo/apartado aplicado y motivación del encaje típico."
            )
        else:
            blocks["mismatch_line"] = (
                "No consta acreditado el encaje del hecho descrito en el precepto aplicado; se solicita identificación expresa del artículo/apartado y su subsunción."
            )
        blocks["proof_requests"] = (
            "Se interesa la aportación del expediente íntegro (denuncia/boletín, propuesta y resolución, si existieran), "
            "así como la identificación expresa del precepto aplicado (artículo/apartado) y la motivación completa de su encaje con el hecho."
        )
        return blocks

    if m is None:
        blocks["primary_title"] = "ALEGACIÓN PRIMERA — FALTA DE IDENTIFICACIÓN PRECISA DEL PRECEPTO Y MOTIVACIÓN DEL ENCAJE TÍPICO (INDEFENSIÓN)"
        blocks["primary_body"] = (
            "No consta acreditado con precisión el precepto efectivamente aplicado (artículo/apartado) ni su subsunción concreta con el hecho imputado, "
            "lo que impide ejercer contradicción efectiva. Se solicita identificación expresa del artículo/apartado, norma aplicable y motivación individualizada."
        )
        blocks["proof_requests"] = (
            "Se interesa copia íntegra del expediente administrativo y de la norma aplicada, con identificación expresa del precepto (artículo/apartado) "
            "y fundamentos jurídicos utilizados."
        )
        return blocks

    return blocks
