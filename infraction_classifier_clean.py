
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
    "peso",
]

CANONICAL_HECHO = {
    "semaforo": "NO RESPETAR LA LUZ ROJA (SEMÁFORO)",
    "movil": "USO MANUAL DEL TELÉFONO MÓVIL",
    "auriculares": "USO DE AURICULARES O CASCOS CONECTADOS",
    "cinturon": "NO UTILIZAR CINTURÓN DE SEGURIDAD",
    "casco": "NO UTILIZAR CASCO DE PROTECCIÓN",
    "tacografo": "INCUMPLIMIENTO DE NORMATIVA DE TACÓGRAFO",
    "peso": "EXCESO DE PESO O SOBRECARGA EN TRANSPORTE PROFESIONAL",
    "estiba": "ESTIBA O SUJECIÓN INCORRECTA DE LA CARGA",
    "documentacion_transporte": "INCUMPLIMIENTO DOCUMENTAL EN TRANSPORTE PROFESIONAL",
    "limitador_velocidad": "INCUMPLIMIENTO RELATIVO AL LIMITADOR DE VELOCIDAD",
    "adr": "INCUMPLIMIENTO ADR / MERCANCÍAS PELIGROSAS",
    "neumaticos": "DEFICIENCIAS EN NEUMÁTICOS DEL VEHÍCULO PESADO",
    "seguro": "CARENCIA DE SEGURO OBLIGATORIO",
    "itv": "ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA",
    "marcas_viales": "NO RESPETAR MARCA VIAL",
    "carril": "POSICIÓN INCORRECTA EN LA VÍA / USO INDEBIDO DEL CARRIL",
    "atencion": "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN",
    "condiciones_vehiculo": "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO",
    "velocidad": "EXCESO DE VELOCIDAD",
}

TOKEN_WEIGHTS: Dict[str, List[Tuple[str, int]]] = {
    "tacografo": [
        ("tacografo", 12),
        ("tacógrafo", 12),
        ("tiempos maximos de conduccion", 11),
        ("tiempos máximos de conducción", 11),
        ("tiempos de conduccion", 10),
        ("tiempos de conducción", 10),
        ("tiempos de descanso", 10),
        ("periodos de descanso obligatorios", 10),
        ("períodos de descanso obligatorios", 10),
        ("descanso obligatorio", 9),
        ("horas de conduccion", 9),
        ("horas de conducción", 9),
        ("registro tacografo", 10),
        ("registro tacógrafo", 10),
        ("tarjeta del conductor", 10),
        ("tarjeta conductor", 9),
        ("conductor profesional", 6),
        ("manipulacion del tacografo", 13),
        ("manipulación del tacógrafo", 13),
        ("sin introducir la tarjeta del conductor", 12),
        ("no se aportan los registros del tacografo", 12),
        ("no se aportan los registros del tacógrafo", 12),
        ("descarga de datos del tacografo", 10),
        ("descarga de datos del tacógrafo", 10),
    ],
    "semaforo": [
        ("semaforo", 8), ("semáforo", 8), ("fase roja", 11), ("luz roja", 10),
        ("linea de detencion", 9), ("línea de detención", 9), ("articulo 146", 8), ("art. 146", 8),
        ("cruce con fase del rojo", 12), ("cruce con fase roja", 12), ("cruce en rojo", 10),
    ],
    "movil": [
        ("telefono movil", 11), ("teléfono móvil", 11), ("uso manual", 8),
        ("manipulando el movil", 10), ("manipulando el móvil", 10),
        ("sujetando con la mano el dispositivo", 11), ("interactuando con la pantalla", 10),
        ("terminal telefonico", 8), ("terminal telefónico", 8), ("pantalla", 5),
    ],
    "auriculares": [
        ("auricular", 10), ("auriculares", 10), ("cascos conectados", 9),
        ("cascos o auriculares", 9), ("dispositivo de audio", 8),
        ("reproductores de sonido", 8), ("oido derecho", 7), ("oído derecho", 7),
        ("oido izquierdo", 7), ("oído izquierdo", 7),
    ],
    "cinturon": [
        ("cinturon de seguridad", 11), ("cinturón de seguridad", 11), ("sin cinturon", 10),
        ("sin cinturón", 10), ("no utilizar el cinturon", 10), ("no utilizar el cinturón", 10),
        ("no llevar abrochado el cinturon", 10), ("no llevar abrochado el cinturón", 10),
        ("correctamente abrochado", 6),
    ],
    "casco": [
        ("sin casco", 11), ("no llevar casco", 11), ("no utilizar casco", 11),
        ("no hacer uso del casco", 12), ("casco reglamentario", 8), ("casco de proteccion", 8),
        ("casco de protección", 8), ("casco homologado", 7), ("desabrochado", 5),
    ],
    "peso": [
        ("exceso de peso", 99),
        ("sobrecarga", 12),
        ("sobrepeso", 12),
        ("masa maxima", 11),
        ("masa máxima", 11),
        ("masa maxima autorizada", 12),
        ("masa máxima autorizada", 12),
        ("mma", 10),
        ("peso por eje", 10),
        ("pesaje", 10),
        ("bascula", 10),
        ("báscula", 10),
        ("camion", 2),
        ("camión", 2),
        ("vehiculo pesado", 2),
        ("vehículo pesado", 2),
    ],
    "estiba": [
        ("estiba", 12),
        ("carga mal sujeta", 14),
        ("carga mal asegurada", 14),
        ("sujecion de carga", 11),
        ("sujeción de carga", 11),
        ("amarre de la carga", 11),
        ("trincaje", 11),
        ("carga desplazada", 11),
        ("mercancia mal estibada", 14),
        ("mercancía mal estibada", 14),
        ("camion", 2),
        ("camión", 2),
        ("vehiculo pesado", 2),
        ("vehículo pesado", 2),
    ],
    "documentacion_transporte": [
        ("documentacion de transporte", 14),
        ("documentación de transporte", 14),
        ("carece de documentacion", 14),
        ("carece de documentación", 14),
        ("sin documentacion", 14),
        ("sin documentación", 14),
        ("carta de porte", 12),
        ("documento de control", 12),
        ("permiso comunitario", 10),
        ("licencia comunitaria", 10),
        ("transporte", 3),
    ],
    "limitador_velocidad": [
        ("limitador de velocidad", 15),
        ("limitador", 10),
        ("no funciona", 3),
    ],
    "adr": [
        ("adr", 15),
        ("mercancias peligrosas", 13),
        ("mercancías peligrosas", 13),
        ("panel naranja", 11),
        ("senalizacion adr", 10),
        ("señalizacion adr", 10),
    ],
    "neumaticos": [
        ("neumaticos", 13),
        ("neumáticos", 13),
        ("desgaste", 8),
        ("profundidad del dibujo", 11),
        ("cubierta", 8),
        ("mal estado", 5),
        ("camion", 2),
        ("camión", 2),
    ],
    "seguro": [
        ("seguro obligatorio", 11), ("sin seguro", 11), ("vehiculo no asegurado", 11),
        ("vehículo no asegurado", 11), ("vehiculo sin asegurar", 11), ("vehículo sin asegurar", 11),
        ("poliza", 7), ("póliza", 7), ("aseguramiento obligatorio", 9), ("responsabilidad civil", 6),
        ("8/2004", 6),
    ],
    "itv": [
        ("itv", 10), ("inspeccion tecnica", 9), ("inspección técnica", 9),
        ("itv caducada", 11), ("caducidad de itv", 11),
    ],
    "marcas_viales": [
        ("linea continua", 11), ("línea continua", 11), ("marca longitudinal continua", 10),
        ("marca vial", 9), ("senalizacion horizontal", 8), ("señalización horizontal", 8),
        ("articulo 167", 7), ("art. 167", 7),
    ],
    "carril": [
        ("carril", 7), ("carril derecho", 9), ("carril izquierdo", 9), ("carril central", 9),
        ("borde derecho", 8), ("posicion en la via", 8), ("posición en la vía", 8),
        ("adelantar por la derecha", 10),
    ],
    "atencion": [
        ("atencion permanente", 10), ("atención permanente", 10), ("conduccion negligente", 11),
        ("conducción negligente", 11), ("conducir de forma negligente", 12),
        ("distraccion", 9), ("distracción", 9), ("golpeando el volante", 8),
        ("mordia las uñas", 7), ("mordía las uñas", 7), ("libertad de movimientos", 6),
    ],
    "condiciones_vehiculo": [
        ("alumbrado", 8), ("senalizacion optica", 8), ("señalizacion optica", 8),
        ("superficie acristalada", 9), ("visibilidad diafana", 9), ("visibilidad diáfana", 9),
        ("laminas adhesivas", 9), ("láminas adhesivas", 9), ("cortinillas", 9),
        ("parabrisas", 8), ("luz azul", 8), ("luces azules", 8), ("destellos", 8),
        ("intermitente", 6), ("piloto trasero", 7), ("luz de freno", 7),
    ],
    "velocidad": [
        ("km/h", 10), ("velocidad", 8), ("radar", 9), ("cinemometro", 9), ("cinemómetro", 9),
        ("multanova", 9), ("exceso de velocidad", 11), ("limitada la velocidad a", 9),
        ("teniendo limitada la velocidad a", 9), ("velocidad maxima", 8), ("velocidad máxima", 8),
        ("circular a", 5), ("circulaba a", 5),
    ],
}

FALSE_FRIEND_PENALTIES: Dict[str, List[Tuple[str, int]]] = {
    "semaforo": [
        ("orden del agente", 8), ("ordenes del agente", 8), ("órdenes del agente", 8),
        ("alto de los agentes", 8), ("alto policial", 8),
    ],
    "velocidad": [
        ("fecha limite", 7), ("fecha límite", 7), ("bonificacion del 50", 5), ("reduccion del 50", 5),
    ],
}

def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""

def normalize_match(text: str) -> str:
    t = _safe_str(text).lower()
    repl = {
        "semáforo": "semaforo",
        "señal": "senal",
        "línea": "linea",
        "teléfono": "telefono",
        "móvil": "movil",
        "cinemómetro": "cinemometro",
        "inspección": "inspeccion",
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
    }
    for a,b in repl.items():
        t = t.replace(a,b)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n+", "\n", t)
    return t.strip()

def build_blob(text_blob: str = "", core: Optional[Dict[str, Any]] = None) -> str:
    core = core or {}
    parts = [
        _safe_str(text_blob),
        _safe_str(core.get("hecho_denunciado_literal")),
        _safe_str(core.get("hecho_denunciado_resumido")),
        _safe_str(core.get("hecho_imputado")),
        _safe_str(core.get("hecho_imputado_textual")),
        _safe_str(core.get("hecho_crudo")),
        _safe_str(core.get("hecho_reconstruido")),
        _safe_str(core.get("organismo")),
        _safe_str(core.get("tipo_sancion")),
        _safe_str(core.get("norma_hint")),
        _safe_str(core.get("raw_text_blob")),
    ]
    return normalize_match("\n".join(p for p in parts if p))

def score_infraction_text(text_blob: str = "", core: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    blob = build_blob(text_blob, core)
    scores = {k: 0 for k in TOKEN_WEIGHTS.keys()}
    for family, pairs in TOKEN_WEIGHTS.items():
        for token, pts in pairs:
            if normalize_match(token) in blob:
                scores[family] += pts
        for token, pts in FALSE_FRIEND_PENALTIES.get(family, []):
            if normalize_match(token) in blob:
                scores[family] -= pts
        if scores[family] < 0:
            scores[family] = 0
    return scores

def pick_best(scores: Dict[str, int]) -> Tuple[str, float]:

    # PRIORIDAD ABSOLUTA SEMÁFORO
    if scores.get("semaforo", 0) >= 8:
        return "semaforo", 0.99

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    if not ordered or ordered[0][1] <= 0:
        return "generic", 0.0

    best_type, best_score = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0

    confidence = round(best_score / max(best_score + second, 1), 4)

    return best_type, confidence

def classify_infraction_text(text_blob: str = "", core: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    scores = score_infraction_text(text_blob, core)
    best_type, confidence = pick_best(scores)
    hecho = CANONICAL_HECHO.get(best_type, "")
    facts = [hecho] if hecho else []
    return {
        "tipo": best_type,
        "confidence": confidence,
        "scores": scores,
        "hecho_canonico": hecho,
        "facts": facts,
    }
