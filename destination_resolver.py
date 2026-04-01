from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional

DGT_DESTINATION = {
    "entity": "dgt",
    "channel": "sede_dgt",
    "destination": "Dirección General de Tráfico (DGT)",
    "address": "https://sede.dgt.gob.es",
    "endpoint": "https://sede.dgt.gob.es",
    "confidence": "high",
}

REG_GENERAL_DESTINATION = {
    "entity": "registro_general",
    "channel": "registro_electronico",
    "destination": "Registro Electrónico General",
    "address": "https://rec.redsara.es/registro/action/are/acceso.do",
    "endpoint": "https://rec.redsara.es/registro/action/are/acceso.do",
    "confidence": "fallback",
}

KNOWN_CITY_DESTINATIONS = {
    "MADRID": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ayuntamiento de Madrid",
        "address": "https://sede.madrid.es",
        "endpoint": "https://sede.madrid.es",
        "confidence": "high",
    },
    "BARCELONA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ajuntament de Barcelona",
        "address": "https://seuelectronica.ajuntament.barcelona.cat",
        "endpoint": "https://seuelectronica.ajuntament.barcelona.cat",
        "confidence": "high",
    },
    "VALENCIA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ajuntament de València",
        "address": "https://sede.valencia.es",
        "endpoint": "https://sede.valencia.es",
        "confidence": "high",
    },
    "SEVILLA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ayuntamiento de Sevilla",
        "address": "https://www.sevilla.org/sede-electronica",
        "endpoint": "https://www.sevilla.org/sede-electronica",
        "confidence": "high",
    },
    "MALAGA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ayuntamiento de Málaga",
        "address": "https://sede.malaga.eu",
        "endpoint": "https://sede.malaga.eu",
        "confidence": "high",
    },
    "BADAJOZ": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Ayuntamiento de Badajoz",
        "address": "https://sede.aytobadajoz.es",
        "endpoint": "https://sede.aytobadajoz.es",
        "confidence": "high",
    },
    "PONTEVEDRA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Concello de Pontevedra",
        "address": "https://sede.pontevedra.gal",
        "endpoint": "https://sede.pontevedra.gal",
        "confidence": "medium",
    },
    "VIGO": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Concello de Vigo",
        "address": "https://sede.vigo.org",
        "endpoint": "https://sede.vigo.org",
        "confidence": "medium",
    },
    "A CORUNA": {
        "entity": "ayuntamiento",
        "channel": "sede_municipal",
        "destination": "Concello da Coruña",
        "address": "https://sede.coruna.gal",
        "endpoint": "https://sede.coruna.gal",
        "confidence": "medium",
    },
}

DGT_MARKERS = [
    "DIRECCION GENERAL DE TRAFICO",
    "JEFATURA DE TRAFICO",
    "JEFATURA PROVINCIAL DE TRAFICO",
    "MINISTERIO DEL INTERIOR",
    "DGT",
    "TRAFICO",
]

LOCAL_ENTITY_MARKERS = {
    "policia_local": ["POLICIA LOCAL", "GUARDIA URBANA", "GUARDIA MUNICIPAL"],
    "ayuntamiento": ["AYUNTAMIENTO DE", "AJUNTAMENT DE", "CONCELLO DE", "AYUNTAMIENTO", "AJUNTAMENT", "CONCELLO"],
    "diputacion": ["DIPUTACION DE", "DIPUTACION PROVINCIAL", "DIPUTACION"],
    "cabildo": ["CABILDO DE", "CABILDO INSULAR", "CABILDO"],
    "consell": ["CONSELL DE", "CONSEJO INSULAR", "CONSELL"],
    "generalitat": ["GENERALITAT", "JUNTA DE", "GOBIERNO DE", "COMUNIDAD AUTONOMA"],
    "guardia_civil": ["GUARDIA CIVIL"],
}

TEXT_KEYS = [
    "ocr_text",
    "ocr",
    "raw_text",
    "texto",
    "texto_completo",
    "body",
    "description",
    "denuncia_text",
    "hecho",
    "hecho_denunciado",
    "facts",
    "organismo",
    "issuer",
    "issuer_name",
    "authority",
    "authority_name",
    "denunciante",
    "place",
    "municipio",
    "city",
    "province",
    "provincia",
]


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = _strip_accents(value).upper()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _walk_text(node: Any, chunks: list[str]) -> None:
    if node is None:
        return
    if isinstance(node, str):
        txt = normalize_text(node)
        if txt:
            chunks.append(txt)
        return
    if isinstance(node, dict):
        norm_keys = {normalize_text(k) for k in TEXT_KEYS}
        for key, value in node.items():
            key_txt = normalize_text(key)
            if key_txt in norm_keys:
                _walk_text(value, chunks)
            else:
                if isinstance(value, (dict, list, tuple, set)):
                    _walk_text(value, chunks)
                elif isinstance(value, str):
                    txt = normalize_text(value)
                    if txt:
                        chunks.append(txt)
        return
    if isinstance(node, (list, tuple, set)):
        for item in node:
            _walk_text(item, chunks)
        return
    txt = normalize_text(node)
    if txt:
        chunks.append(txt)


def collect_case_text(case_data: Dict[str, Any]) -> str:
    chunks: list[str] = []
    _walk_text(case_data or {}, chunks)
    return " | ".join(chunks)


def detect_city(normalized_text: str, case_data: Dict[str, Any]) -> Optional[str]:
    direct = normalize_text(case_data.get("municipio") or case_data.get("city") or case_data.get("localidad"))
    if direct in KNOWN_CITY_DESTINATIONS:
        return direct

    province = normalize_text(case_data.get("provincia") or case_data.get("province"))
    if province in KNOWN_CITY_DESTINATIONS:
        return province

    for city in KNOWN_CITY_DESTINATIONS.keys():
        if city in normalized_text:
            return city
    return None


def detect_entity(normalized_text: str) -> str:
    for marker in DGT_MARKERS:
        if marker in normalized_text:
            return "dgt"

    for entity, markers in LOCAL_ENTITY_MARKERS.items():
        for marker in markers:
            if marker in normalized_text:
                return entity

    return "registro_general"


def resolve_destination(case_data: Dict[str, Any]) -> Dict[str, Any]:
    case_data = case_data or {}
    normalized_text = collect_case_text(case_data)
    entity = detect_entity(normalized_text)
    city = detect_city(normalized_text, case_data)

    if entity == "dgt":
        destination = dict(DGT_DESTINATION)
        destination["matched_city"] = city
        destination["matched_entity"] = entity
        destination["resolver_mode"] = "ocr_detected"
        return destination

    if city and city in KNOWN_CITY_DESTINATIONS:
        destination = dict(KNOWN_CITY_DESTINATIONS[city])
        destination["matched_city"] = city
        destination["matched_entity"] = entity
        destination["resolver_mode"] = "known_city"
        return destination

    if entity == "ayuntamiento":
        destination = dict(REG_GENERAL_DESTINATION)
        destination["entity"] = "ayuntamiento"
        destination["destination"] = "Registro Electrónico General (fallback ayuntamiento)"
        destination["matched_city"] = city
        destination["matched_entity"] = entity
        destination["resolver_mode"] = "municipal_fallback"
        return destination

    if entity in {"policia_local", "guardia_civil", "diputacion", "cabildo", "consell", "generalitat"}:
        destination = dict(REG_GENERAL_DESTINATION)
        destination["entity"] = entity
        destination["destination"] = f"Registro Electrónico General (fallback {entity})"
        destination["matched_city"] = city
        destination["matched_entity"] = entity
        destination["resolver_mode"] = "entity_fallback"
        return destination

    destination = dict(REG_GENERAL_DESTINATION)
    destination["matched_city"] = city
    destination["matched_entity"] = entity
    destination["resolver_mode"] = "default_fallback"
    return destination
