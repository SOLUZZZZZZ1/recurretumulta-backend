import re

# ==============================
# BASE INICIAL DE DESTINOS
# ==============================

DGT_DESTINATION = {
    "type": "dgt",
    "name": "Dirección General de Tráfico",
    "endpoint": "https://sede.dgt.gob.es",
    "channel": "sede_dgt"
}

MUNICIPAL_KNOWN = {
    "MADRID": {
        "name": "Ayuntamiento de Madrid",
        "endpoint": "https://sede.madrid.es",
        "channel": "sede_municipal"
    },
    "BARCELONA": {
        "name": "Ajuntament de Barcelona",
        "endpoint": "https://seuelectronica.ajuntament.barcelona.cat",
        "channel": "sede_municipal"
    },
    "VALENCIA": {
        "name": "Ayuntamiento de Valencia",
        "endpoint": "https://sede.valencia.es",
        "channel": "sede_municipal"
    }
}

# fallback universal (clave)
REG_GENERAL = {
    "type": "registro_general",
    "name": "Registro Electrónico General",
    "endpoint": "https://rec.redsara.es/registro/action/are/acceso.do",
    "channel": "registro_electronico"
}


# ==============================
# DETECCIÓN
# ==============================

def normalize(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).upper().strip()


def detect_organismo(data: dict):
    text = normalize(str(data))

    if "DGT" in text or "TRAFICO" in text:
        return "DGT"

    if "AYUNTAMIENTO" in text or "POLICIA LOCAL" in text:
        return "AYUNTAMIENTO"

    return "UNKNOWN"


def detect_city(data: dict):
    text = normalize(str(data))

    for city in MUNICIPAL_KNOWN.keys():
        if city in text:
            return city

    return None


# ==============================
# RESOLVER DESTINO
# ==============================

def resolve_destination(case_data: dict):
    organismo = detect_organismo(case_data)
    city = detect_city(case_data)

    # 1. DGT → directo
    if organismo == "DGT":
        return DGT_DESTINATION

    # 2. Ayuntamiento conocido
    if organismo == "AYUNTAMIENTO" and city:
        if city in MUNICIPAL_KNOWN:
            return MUNICIPAL_KNOWN[city]

    # 3. Ayuntamiento desconocido → REG GENERAL
    if organismo == "AYUNTAMIENTO":
        return REG_GENERAL

    # 4. fallback total
    return REG_GENERAL