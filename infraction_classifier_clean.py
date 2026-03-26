import re
from typing import Any, Dict, List, Optional, Tuple

FAMILY_ORDER = [
    "semaforo",
    "movil",
    "auriculares",
    "cinturon",
    "casco",
    "tacografo",
    "peso",
    "estiba",
    "documentacion_transporte",
    "limitador_velocidad",
    "adr",
    "neumaticos",
    "seguro",
    "itv",
    "marcas_viales",
    "carril",
    "atencion",
    "condiciones_vehiculo",
    "velocidad",
]

def normalize(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    repl = {
        "á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n",
        "semáforo":"semaforo","teléfono":"telefono","móvil":"movil",
        "línea":"linea","señal":"senal"
    }
    for k,v in repl.items():
        t = t.replace(k,v)
    return re.sub(r"\s+", " ", t)

KEYWORDS = {
    "peso": ["exceso de peso","sobrecarga","sobrepeso","masa maxima","mma","bascula","pesaje"],
    "estiba": ["carga mal sujeta","carga mal asegurada","estiba","trincaje","amarre"],
    "documentacion_transporte": ["documentacion de transporte","carta de porte","permiso comunitario"],
    "tacografo": ["tacografo","tiempos de conduccion","descanso obligatorio"],
    "adr": ["adr","mercancias peligrosas"],
    "limitador_velocidad": ["limitador de velocidad"],
    "neumaticos": ["neumaticos","desgaste","cubierta"],
    "semaforo": ["semaforo","luz roja","fase roja"],
    "movil": ["telefono movil","uso manual","pantalla"],
    "auriculares": ["auriculares","cascos"],
    "cinturon": ["cinturon","sin cinturon"],
    "casco": ["casco","sin casco"],
    "seguro": ["seguro obligatorio","sin seguro"],
    "itv": ["itv","inspeccion tecnica"],
    "marcas_viales": ["linea continua","marca vial"],
    "carril": ["carril","calzada"],
    "atencion": ["distraccion","negligente"],
    "condiciones_vehiculo": ["alumbrado","parabrisas"],
    "velocidad": ["km/h","velocidad","radar"],
}

def score(text: str):
    t = normalize(text)
    scores = {k:0 for k in KEYWORDS}

    for family, words in KEYWORDS.items():
        for w in words:
            if w in t:
                scores[family] += 5

    return scores

def classify(text: str):
    scores = score(text)
    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return "generic", scores

    return best, scores