# -*- coding: utf-8 -*-
import re

def normalize(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower())

# --- DICCIONARIO COMPLETO ---
KEYWORDS = {

    # =====================
    # COCHES
    # =====================

    "itv": [
        "itv", "inspeccion tecnica", "inspección técnica",
        "caducada", "sin itv"
    ],

    "seguro": [
        "seguro obligatorio", "sin seguro",
        "poliza", "póliza"
    ],

    "casco": [
        "casco", "sin casco"
    ],

    "condiciones_vehiculo": [
        "deficiencias", "alumbrado", "vehiculo defectuoso"
    ],

    "carril": [
        "carril", "calzada"
    ],

    "atencion": [
        "distraccion", "distracción", "negligente"
    ],

    "auriculares": [
        "auriculares", "cascos"
    ],

    "alcohol": [
        "alcohol", "alcoholemia"
    ],

    "velocidad": [
        "km/h", "velocidad", "radar"
    ],

    "semaforo": [
        "luz roja", "fase roja", "semaforo", "semáforo"
    ],

    "marcas_viales": [
        "linea continua", "línea continua"
    ],

    # =====================
    # CAMIONES (CLAVE)
    # =====================

    "peso": [
        "exceso de peso",
        "sobrecarga",
        "sobrepeso",
        "masa maxima",
        "masa máxima",
        "mma",
        "bascula",
        "báscula"
    ],

    "estiba": [
        "carga mal sujeta",
        "carga mal asegurada",
        "estiba",
        "trincaje",
        "amarre"
    ],

    "documentacion_transporte": [
        "documentacion de transporte",
        "documentación de transporte",
        "carece de documentacion",
        "carece de documentación",
        "sin documentacion",
        "sin documentación",
        "carta de porte",
        "documento de control"
    ],

    "tacografo": [
        "tacografo",
        "tacógrafo",
        "tiempos de conduccion",
        "tiempos de descanso"
    ],

    "limitador_velocidad": [
        "limitador de velocidad"
    ],

    "adr": [
        "adr",
        "mercancias peligrosas",
        "mercancías peligrosas"
    ],

    "neumaticos": [
        "neumaticos",
        "neumáticos",
        "desgaste"
    ],
}

def score_text(text: str):
    t = normalize(text)
    scores = {k: 0 for k in KEYWORDS}

    for family, words in KEYWORDS.items():
        for w in words:
            if w in t:
                scores[family] += 5

    # --- PRIORIDADES IMPORTANTES ---
    if scores["semaforo"] > 0:
        scores["velocidad"] = 0

    return scores

def classify(text: str):
    scores = score_text(text)

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return "generic", scores

    return best, scores