# -*- coding: utf-8 -*-
# recurreTuMulta - scoring afinado v3 (sinónimos + protecciones)
# Sustituye tu módulo de scoring por este contenido

import re

def normalize(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower())

# --- Diccionario ampliado de señales ---
KEYWORDS = {
    "itv": [
        "itv", "inspeccion tecnica", "inspección técnica", "revision tecnica",
        "caducada", "sin itv", "no tener vigente la inspeccion",
        "carecer de inspeccion tecnica", "inspeccion tecnica del vehiculo",
        "inspección técnica del vehículo"
    ],
    "seguro": [
        "seguro obligatorio", "sin seguro", "carecer de seguro",
        "poliza", "póliza", "cobertura obligatoria", "sin cobertura",
        "no tener concertado", "seguro en vigor", "seguro obligatorio del vehiculo"
    ],
    "casco": [
        "casco", "sin casco", "no utilizar casco",
        "casco desabrochado", "casco mal ajustado",
        "no hacer uso del casco", "casco no debidamente"
    ],
    "condiciones_vehiculo": [
        "defectos mecanicos", "deficiencias tecnicas", "ruedas en mal estado",
        "neumaticos en mal estado", "componentes mecanicos defectuosos",
        "fallo sistema iluminacion", "dispositivos luminosos no reglamentarios",
        "deficiencias relevantes", "vehiculo defectuoso"
    ],
    "carril": [
        "carril", "posicion en la calzada", "posición en la calzada",
        "carril derecho", "carril izquierdo", "carril no habilitado",
        "posicion no ajustada", "posición no ajustada"
    ],
    "atencion": [
        "distraccion", "distracción", "sin atencion", "desatencion",
        "no conservar atencion", "mantener distraccion",
        "conducta distraida", "conducta distraída"
    ],
    "auriculares": [
        "auriculares", "cascos", "oidos", "oídos",
        "dispositivo de audio", "aparato sonoro", "receptor sonoro"
    ],
    "alcohol": [
        "alcohol", "alcoholemia", "positivo", "tasa",
        "etilometro", "etilómetro", "test de alcohol",
        "resultado positivo"
    ],
    "velocidad": [
        "km/h", "velocidad", "radar", "cinemometro", "cinemómetro",
        "multanova", "velolaser", "pegasus"
    ],
    "semaforo": [
        "luz roja", "fase roja", "semaforo", "semáforo",
        "indicacion luminosa", "señal luminosa"
    ],
    "marcas_viales": [
        "linea continua", "línea continua", "marca vial",
        "marcas viales", "delimitacion continua", "delimitación continua"
    ],
}

def score_text(text: str):
    t = normalize(text)
    scores = {k: 0 for k in KEYWORDS.keys()}

    for family, words in KEYWORDS.items():
        for w in words:
            if w in t:
                scores[family] += 3

    # --- PROTECCIONES ---
    # Si hay alcohol, evitar que "agentes" o ruido suba atención
    if scores["alcohol"] > 0:
        scores["atencion"] = 0

    # Si hay semáforo, eliminar velocidad
    if scores["semaforo"] > 0:
        scores["velocidad"] = 0

    # Si hay km/h pero no "velocidad" real → bajar peso
    if "km/h" in t and scores["velocidad"] <= 3:
        scores["velocidad"] = 0

    return scores

def classify(text: str):
    scores = score_text(text)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "generic", scores
    return best, scores
