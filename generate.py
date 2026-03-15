import json
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import build_movil_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template
from ai.infractions.distracciones import build_auriculares_strong_template
from ai.infractions.atencion import build_atencion_strong_template
from ai.infractions.marcas_viales import build_marcas_viales_strong_template
from ai.infractions.seguro import build_seguro_strong_template
from ai.infractions.cinturon import build_cinturon_strong_template
from ai.infractions.itv import build_itv_strong_template
from ai.infractions.generic import build_generic_body
from ai.infractions.municipal_semaforo import build_municipal_semaforo_template
from ai.infractions.casco import build_casco_strong_template
from ai.infractions.municipal_sentido_contrario import build_municipal_sentido_contrario_template
from ai.infractions.municipal_generic import build_municipal_generic_template
from ai.infractions.velocidad import (
    build_velocity_calc_paragraph,
    build_tramo_error_paragraph,
)

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from ai.infractions.dispatch import dispatch_deterministic_template

router = APIRouter(tags=["generate"])


_ADMIN_PREFIXES = [
    "organismo:",
    "expediente_ref:",
    "tipo_sancion:",
    "observaciones:",
    "vision_raw_text:",
    "raw_text_pdf:",
    "raw_text_vision:",
    "raw_text_blob:",
    "fecha_documento:",
    "fecha_notificacion:",
    "importe:",
    "jurisdiccion:",
    "tipo_infraccion:",
    "facts_phrases:",
    "preceptos_detectados:",
    "articulo_infringido_num:",
    "apartado_infringido_num:",
    "norma_hint:",
]


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _clean_hecho_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r", " ").replace("\n", " ")
    low = t.lower()

    for p in _ADMIN_PREFIXES:
        idx = low.find(p)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    stop_signals = [
        " datos vehiculo",
        " datos vehículo",
        " importe",
        " puntos",
        " fecha limite",
        " fecha límite",
        " boletin",
        " boletín",
        " agente denunciante",
        " telefono de informacion",
        " teléfono de información",
        " telefono de atencion",
        " teléfono de atención",
        " fax",
        " correo ordinario",
        " remitir el presente",
        " impreso relleno",
        " total principal",
        " precepto infringido",
    ]
    for s in stop_signals:
        idx = low.find(s)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    t = re.sub(r'^[\"“”]+|[\"“”]+$', "", t).strip()
    t = re.sub(r"^(movil|m[oó]vil)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(5a|5b|5c)\s+", "", t, flags=re.IGNORECASE)
    return t


def _looks_like_internal_extract(text: str) -> bool:
    low = _safe_str(text).lower().strip()
    if not low:
        return True
    bad_tokens = [
        "pone_fin_via_administrativa",
        "plazo_recurso_sugerido",
        "tipo_infraccion_scores",
        "tipo_infraccion_confidence",
        "subtipo_infraccion",
        "evidence_gaps",
        "recurso_strategy",
        "raw_text_pdf",
        "raw_text_vision",
        "raw_text_blob",
        "vision_raw_text",
        "radar_modelo_hint",
        "radar_tipo",
        "metrologia_requerida",
    ]
    return any(tok in low for tok in bad_tokens)


def get_hecho_para_recurso(core: Dict[str, Any]) -> str:
    raw = (
        core.get("hecho_denunciado_resumido")
        or core.get("hecho_denunciado_literal")
        or core.get("hecho_imputado")
        or ""
    )
    txt = _clean_hecho_text(_safe_str(raw))
    low = txt.lower().strip()
    if (
        low.startswith("tipo_sancion:")
        or low.startswith("organismo:")
        or low.startswith("expediente_ref:")
        or low.startswith("hecho_imputado:")
    ):
        return ""
    return txt


def extract_hecho_denunciado_literal(core: Dict[str, Any]) -> str:
    text_parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "vision_raw_text"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)

    text = "\n".join(text_parts)
    if not text:
        return ""

    pattern = re.search(
        r"(hecho denunciado|hecho que se notifica|hecho imputado|hecho infringido)\s*[:\-]?\s*",
        text,
        re.IGNORECASE,
    )
    tail = text[pattern.end():] if pattern else text
    lines = [l.strip() for l in tail.split("\n") if l.strip()]

    collected = []
    started = False

    for ln in lines:
        low = ln.lower()

        if any(
            x in low
            for x in [
                "datos vehiculo",
                "datos vehículo",
                "importe",
                "bonificacion",
                "reduccion",
                "fecha limite",
                "fecha límite",
                "puntos",
                "entidad",
                "matricula",
                "marca:",
                "modelo",
                "domicilio",
                "boletin",
                "boletín",
                "telefono de informacion",
                "teléfono de información",
                "telefono de atencion",
                "teléfono de atención",
                "fax",
                "correo ordinario",
                "remitir el presente",
                "impreso relleno",
                "motivo de no notificacion",
                "motivo de no notificación",
            ]
        ):
            if started:
                break
            continue

        if not started:
            if any(
                s in low
                for s in [
                    "circular a",
                    "circulaba a",
                    "conducir",
                    "cruce",
                    "fase roja",
                    "luz roja",
                    "semaforo",
                    "utilizando",
                    "auricular",
                    "auriculares",
                    "cascos",
                    "bail",
                    "palmas",
                    "volante",
                    "km/h",
                    "velocidad",
                    "linea continua",
                    "línea continua",
                    "itv",
                    "seguro",
                    "alumbrado",
                    "detencion",
                    "detención",
                ]
            ):
                started = True
                collected.append(ln)
        else:
            collected.append(ln)

        if len(" ".join(collected)) > 900:
            break

    return _clean_hecho_text(" ".join(collected))


def resolve_jurisdiction(core: Dict[str, Any]) -> str:
    j = _safe_str(core.get("jurisdiccion")).lower().strip()
    if j in ("municipal", "estatal", "desconocida"):
        return j

    blob = json.dumps(core, ensure_ascii=False).lower()
    if any(s in blob for s in ["ayuntamiento", "policia local", "policía local", "guardia urbana"]):
        return "municipal"
    if any(
        s in blob
        for s in [
            "direccion general de trafico",
            "dirección general de tráfico",
            "dgt",
            "guardia civil",
            "ministerio del interior",
        ]
    ):
        return "estatal"
    return "desconocida"


def _looks_like_semaforo(core: Dict[str, Any]) -> bool:
    blob = json.dumps(core, ensure_ascii=False).lower()
    blob = blob.replace("semáforo", "semaforo").replace("línea", "linea")

    sema_signals = [
        "semaforo",
        "fase roja",
        "luz roja",
        "cruce en rojo",
        "cruce con fase roja",
        "señal luminosa roja",
        "senal luminosa roja",
        "linea de detencion",
        "línea de detención",
        "rebase la linea de detencion",
        "rebasar la linea de detencion",
        "semaforo en rojo",
        "paso en rojo",
        "cruce fase roja",
        "articulo 146",
        "art. 146",
    ]
    if any(s in blob for s in sema_signals):
        return True

    if ("roja" in blob and "cruce" in blob) or ("roja" in blob and "detencion" in blob):
        return True

    return False


def _score_infraction_from_core(core: Dict[str, Any]) -> Dict[str, int]:
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    scores = {
        "velocidad": 0,
        "semaforo": 0,
        "movil": 0,
        "auriculares": 0,
        "cinturon": 0,
        "casco": 0,
        "atencion": 0,
        "marcas_viales": 0,
        "seguro": 0,
        "itv": 0,
        "condiciones_vehiculo": 0,
        "carril": 0,
    }

    def add(tipo: str, signals, points: int) -> None:
        for s in signals:
            if s in blob:
                scores[tipo] += points

    add(
        "velocidad",
        ["km/h", "radar", "cinemometro", "cinemómetro", "exceso de velocidad", "limitada la velocidad a", "multanova"],
        3,
    )
    add("semaforo", ["semaforo", "semáforo", "fase roja", "luz roja", "cruce en rojo", "linea de detencion", "línea de detención"], 3)
    add("movil", ["telefono movil", "teléfono móvil", "uso manual", "manipulando el movil", "manipulando el móvil", "sujetando con la mano el dispositivo"], 3)
    add("auriculares", ["auricular", "auriculares", "cascos conectados", "reproductores de sonido", "porta auricular"], 3)
    add("cinturon", ["cinturon de seguridad", "cinturón de seguridad", "sin cinturón", "sin cinturon", "correctamente abrochado", "no utilizar el cinturón"], 3)
    add("casco", ["sin casco", "no llevar casco", "casco de protección", "casco de proteccion", "casco homologado"], 3)
    add("atencion", ["atencion permanente", "atención permanente", "conduccion negligente", "conducción negligente", "distraccion", "distracción"], 3)
    add("marcas_viales", ["linea continua", "línea continua", "marca vial", "marca longitudinal continua"], 3)
    add("seguro", ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehículo no asegurado", "8/2004", "fiva"], 3)
    add("itv", ["itv", "inspeccion tecnica", "inspección técnica", "itv caducada"], 3)
    add("condiciones_vehiculo", ["condiciones reglamentarias", "alumbrado", "senalizacion optica", "señalización óptica", "homolog", "reflectante"], 3)
    add("carril", ["carril distinto del situado más a la derecha", "carril distinto del situado mas a la derecha", "adelantar por la derecha", "posición en la vía", "posicion en la via"], 3)
    return scores


def resolve_infraction_type(core: Dict[str, Any]) -> str:
    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    if tipo and tipo not in ("otro", "unknown", "desconocido", "generic"):
        return tipo

    if _looks_like_semaforo(core):
        return "semaforo"

    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    if any(s in blob for s in ["fase roja", "luz roja", "semaforo", "semáforo", "cruce en rojo", "linea de detencion", "línea de detención"]):
        return "semaforo"
    if any(s in blob for s in ["bicicleta", "ciclistas", "ciclista"]) and any(s in blob for s in ["atencion permanente", "atención permanente", "conduccion negligente", "conducción negligente", "distraccion", "distracción"]):
        return "atencion"

    scores = _score_infraction_from_core(core)
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] > 0:
        return best[0]
    return "generic"


def fix_roman_headings(text: str) -> str:
    replacements = {
        r"\bi\.\s*antecedentes": "I. ANTECEDENTES",
        r"\bii\.\s*alegaciones": "II. ALEGACIONES",
        r"\biii\.\s*solicito": "III. SOLICITO",
    }
    out = text or ""
    for pattern, repl in replacements.items():
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _build_fundamentos_derecho(tipo: str = "") -> str:
    return (
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los principios generales del Derecho "
        "Administrativo sancionador, en particular los principios de legalidad, "
        "tipicidad, presunción de inocencia y carga de la prueba a cargo de la "
        "Administración.\n\n"
        "SEGUNDO.– Conforme a reiterada jurisprudencia, la potestad sancionadora "
        "de la Administración exige una motivación suficiente del hecho imputado "
        "y una acreditación probatoria bastante que permita enervar la presunción "
        "de inocencia del administrado.\n\n"
        "TERCERO.– La ausencia de prueba suficiente, la insuficiente motivación "
        "del expediente o la falta de concreción del hecho imputado determinan "
        "la improcedencia de la sanción propuesta.\n\n"
    )


def _build_unified_suplico(tipo: str = "") -> str:
    punto_4 = (
        "4) Subsidiariamente, que se imponga en su caso la sanción mínima legalmente\n"
        "procedente dentro del tipo infractor que finalmente pudiera considerarse\n"
        "aplicable.\n\n"
    )

    return (
        "S U P L I C A:\n\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n\n"
        "2) Que, en atención a las alegaciones presentadas y sus fundamentos, se acuerde "
        "el ARCHIVO del expediente por insuficiencia probatoria, falta de acreditación "
        "suficiente del hecho imputado o ausencia de motivación individualizada.\n\n"
        "3) Subsidiariamente, para el caso de no estimarse el archivo, que se proceda "
        "a una correcta recalificación jurídica de los hechos conforme a la prueba "
        "realmente acreditada en el expediente.\n\n"
        f"{punto_4}"
        "5) Subsidiariamente, que se aporte expediente íntegro y prueba completa "
        "para contradicción efectiva.\n\n"
        "OTROSÍ DIGO\n\n"
        "Que esta parte se reserva expresamente el ejercicio de cuantos recursos "
        "administrativos y acciones legales pudieran corresponder en defensa de sus "
        "derechos e intereses legítimos.\n"
    )


def _strip_initial_antecedentes_block(body: str) -> str:
    txt = _safe_str(body).strip()
    txt = re.sub(
        r"^\s*A la atención del órgano competente,?\s*",
        "",
        txt,
        flags=re.IGNORECASE,
    )
    return txt.strip()


def _build_comparecencia_text(core: Dict[str, Any], asunto_out: str) -> str:
    tipo_accion = _safe_str(core.get("tipo_accion")).lower().strip()
    fecha_res = core.get("fecha_resolucion") or "........"
    tenor = core.get("tenor_resolucion") or "................................"

    if "alzada" in tipo_accion:
        return (
            "Que mediante el presente escrito, documentación adjunta y sus copias, "
            f"vengo a formular RECURSO DE ALZADA contra la resolución de fecha {fecha_res}, "
            f"dictada por ese organismo, por la que se acuerda {tenor}, y todo ello según los siguientes\n\n"
            "A N T E C E D E N T E S\n\n"
        )

    if "reposicion" in tipo_accion or "reposición" in tipo_accion:
        return (
            "Que mediante el presente escrito, documentación adjunta y sus copias, "
            f"vengo a formular RECURSO POTESTATIVO DE REPOSICIÓN contra la resolución de fecha {fecha_res}, "
            f"dictada por ese organismo, por la que se acuerda {tenor}, y todo ello según los siguientes\n\n"
            "A N T E C E D E N T E S\n\n"
        )

    return (
        "Que mediante el presente escrito, documentación adjunta y sus copias, "
        f"vengo a formular {asunto_out} en el expediente más arriba referenciado, "
        "y todo ello según los siguientes\n\n"
        "A N T E C E D E N T E S\n\n"
    )


def _upgrade_generated_template(asunto: str, cuerpo: str, tipo: str = "", core: Dict[str, Any] = None) -> Dict[str, str]:
    core = core or {}
    asunto_out = "ESCRITO DE ALEGACIONES"

    exp_ref = core.get("expediente_ref") or core.get("numero_expediente") or "........ / ........"
    organismo = core.get("organismo") or "............................................"
    provincia = core.get("provincia") or "............................................"

    comparecencia = _build_comparecencia_text(core, asunto_out)

    cabecera = (
        f"REFERENCIA: EXPTE. {exp_ref}\n\n\n"
        f"                A LA {str(organismo).upper()}\n\n"
        f"                          DE {str(provincia).upper()}\n\n\n"
        "D./D.ª ........................................, mayor de edad, con DNI/NIE/TR "
        "........................, y con domicilio en ........................................, "
        "a efectos de notificaciones, actuando en su propio nombre e interés "
        "[o actuando por cuenta de D./D.ª ................................, según autorización "
        "o poder que se adjunta como documento núm. 1], ante esta Dependencia comparece y, "
        "como mejor proceda en Derecho,\n\n"
        "D I G O:\n\n"
        f"{comparecencia}"
    )

    body = _safe_str(cuerpo)

    suplico = _build_unified_suplico(tipo)
    fundamentos = _build_fundamentos_derecho(tipo)

    if re.search(r"\bIII\.\s*(SOLICITO|SUPLICO)\b", body, flags=re.IGNORECASE):
        body = re.sub(
            r"\bIII\.\s*(?:SOLICITO|SUPLICO)\b[\s\S]*$",
            fundamentos + "\n" + suplico,
            body,
            flags=re.IGNORECASE,
        )
    else:
        body = body.rstrip() + "\n\n" + fundamentos + "\n" + suplico

    body = fix_roman_headings(body)
    body = _strip_initial_antecedentes_block(body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"

    body = cabecera + body

    return {"asunto": asunto_out, "cuerpo": body}


def build_cinturon_v4_template(core: Dict[str, Any]) -> Dict[str, str]:
    tpl = build_cinturon_strong_template(core)
    if not isinstance(tpl, dict):
        return {"asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE", "cuerpo": str(tpl or "")}

    subtipo = _safe_str(core.get("subtipo_infraccion")).lower().strip()
    evidence_gaps = core.get("evidence_gaps") or []
    extra = ""

    if subtipo == "cinturon_redaccion_ambigua":
        extra += (
            "\n\nALEGACIÓN ESPECÍFICA — AMBIGÜEDAD DEL HECHO IMPUTADO\n\n"
            "La propia redacción del boletín resulta internamente equívoca al combinar fórmulas propias del no uso absoluto con referencias a un supuesto cinturón 'correctamente abrochado'. "
            "Esa formulación híbrida impide conocer con precisión qué conducta concreta se atribuye realmente: ausencia total de uso, uso incorrecto, mal abrochado o colocación defectuosa. "
            "Tal indeterminación debilita la tipicidad y exige una descripción mucho más concreta y circunstanciada del hecho imputado.\n"
        )
    elif subtipo == "cinturon_mal_abrochado":
        extra += (
            "\n\nALEGACIÓN ESPECÍFICA — FALTA DE PRECISIÓN MATERIAL\n\n"
            "No basta afirmar de manera estereotipada que el cinturón no estaba correctamente abrochado. "
            "Debe concretarse si se observó ausencia total, mala fijación, colocación defectuosa o desabrochado momentáneo, con detalle bastante para permitir contradicción efectiva.\n"
        )

    if evidence_gaps:
        bullets = []
        gap_map = {
            "no_prueba_objetiva": "No consta fotografía, vídeo ni soporte objetivo adicional.",
            "distancia_no_acreditada": "No se precisa la distancia de observación.",
            "posicion_agente_no_acreditada": "No consta la posición exacta del agente respecto del vehículo.",
            "duracion_observacion_no_acreditada": "No se concreta el tiempo durante el cual se mantuvo la observación.",
            "visibilidad_no_acreditada": "No se describen las condiciones de visibilidad concurrentes.",
            "concrecion_missing": "No se precisa si se imputa ausencia total, mal abrochado o colocación incorrecta.",
        }
        for g in evidence_gaps:
            if g in gap_map:
                bullets.append("• " + gap_map[g])
        if bullets:
            extra += "\n\nREFUERZO PROBATORIO\n\n" + "\n".join(bullets) + "\n"

    body = _safe_str(tpl.get("cuerpo"))
    if extra and extra not in body:
        insert_after = "II. ALEGACIONES\n\n"
        if insert_after in body:
            body = body.replace(insert_after, insert_after + extra + "\n", 1)
        else:
            body += extra

    tpl["cuerpo"] = body
    return tpl


def build_atencion_bicicleta_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = get_hecho_para_recurso(core) or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FALTA DE DESCRIPCIÓN SUFICIENTE Y CIRCUNSTANCIADA\n\n"
        "La denuncia describe una conducta observada durante la circulación en bicicleta, pero no concreta con el detalle exigible la conducta exacta, su duración, ni las circunstancias espaciales y temporales que permitirían verificarla con fiabilidad.\n\n"
        "ALEGACIÓN SEGUNDA — AUSENCIA DE SOPORTE OBJETIVO Y DE DATOS DE OBSERVACIÓN\n\n"
        "No consta en el expediente soporte objetivo adicional, ni se precisa desde qué posición se realizó la observación, a qué distancia ni durante cuánto tiempo, extremos imprescindibles para valorar la consistencia de una observación de este tipo en vía abierta.\n\n"
        "ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DE LA CONDUCTA DENUNCIADA\n\n"
        "Tratándose de una persona que circula en bicicleta junto con otros ciclistas, la Administración debe concretar de forma especialmente rigurosa la posición exacta del denunciante respecto del ciclista, la visibilidad existente y la forma en que se individualizó la conducta denunciada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa para contradicción efectiva.\n"
    )
    return {
        "asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(cuerpo),
    }


def _is_bicicleta_context(core: Dict[str, Any]) -> bool:
    contexto = _safe_str(core.get("contexto_movilidad")).lower().strip()
    if contexto == "bicicleta":
        return True
    blob = json.dumps(core or {}, ensure_ascii=False).lower()
    return any(s in blob for s in ["bicicleta", "ciclista", "ciclistas", "arcen", "arcén"])


def _sanitize_bicicleta_body(body: str) -> str:
    txt = _safe_str(body)
    if not txt:
        return txt

    txt = txt.replace("ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO", "ALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DE LA CONDUCTA DENUNCIADA")
    txt = txt.replace("La denuncia describe conductas realizadas dentro del habitáculo del vehículo.", "La denuncia atribuye una conducta observada durante la circulación en bicicleta junto con otros ciclistas.")
    txt = txt.replace("interior del vehículo", "circulación en bicicleta")
    txt = txt.replace("habitáculo del vehículo", "entorno de circulación")
    txt = txt.replace("dentro del vehículo", "durante la circulación")

    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt


def _select_template(core: Dict[str, Any], tipo: str, jurisdiccion: str):
    if tipo == "semaforo" and jurisdiccion == "municipal":
        return build_municipal_semaforo_template(core), "municipal_semaforo"
    elif tipo == "semaforo":
        return build_semaforo_strong_template(core), "semaforo"
    elif tipo == "velocidad":
        return build_velocity_strong_template(core), "velocidad"
    elif tipo == "movil":
        return build_movil_strong_template(core), "movil"
    elif tipo == "auriculares":
        return build_auriculares_strong_template(core), "auriculares"
    elif tipo == "cinturon":
        return build_cinturon_v4_template(core), "cinturon"
    elif tipo == "casco":
        return build_casco_strong_template(core), "casco"
    elif tipo == "atencion":
        if _is_bicicleta_context(core):
            return build_atencion_bicicleta_template(core), "atencion_bicicleta"
        return build_atencion_strong_template(core), "atencion"
    elif tipo == "marcas_viales":
        return build_marcas_viales_strong_template(core), "marcas_viales"
    elif tipo == "seguro":
        return build_seguro_strong_template(core), "seguro"
    elif tipo == "itv":
        return build_itv_strong_template(core), "itv"
    elif tipo == "condiciones_vehiculo":
        return build_condiciones_vehiculo_strong_template(core), "condiciones_vehiculo"
    elif tipo == "carril":
        return build_generic_body(core), "carril"
    elif jurisdiccion == "municipal":
        blob = json.dumps(core, ensure_ascii=False).lower()
        if "sentido contrario" in blob or "direccion prohibida" in blob or "dirección prohibida" in blob:
            return build_municipal_sentido_contrario_template(core), "municipal_sentido_contrario"
        elif _looks_like_semaforo(core):
            return build_municipal_semaforo_template(core), "municipal_semaforo_fallback"
        else:
            return build_municipal_generic_template(core), "municipal_generic"
    else:
        return build_generic_body(core), "generic"


def ensure_tpl_dict(tpl: Any, core: Dict[str, Any]) -> Dict[str, str]:
    if isinstance(tpl, dict):
        asunto = tpl.get("asunto")
        cuerpo = tpl.get("cuerpo")
        if isinstance(asunto, str) and asunto.strip() and isinstance(cuerpo, str) and cuerpo.strip():
            return {"asunto": asunto.strip(), "cuerpo": fix_roman_headings(cuerpo.strip())}

    fallback = build_generic_body(core)
    return {
        "asunto": fallback.get("asunto") or "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(fallback.get("cuerpo") or "A la atención del órgano competente."),
    }


def build_velocity_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "[EXPEDIENTE]"
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."

    hecho = get_hecho_para_recurso(core) or "EXCESO DE VELOCIDAD"
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")
    radar = core.get("radar_modelo_hint") or "cinemometro (no especificado)"

    tech_lines = []
    if measured:
        tech_lines.append(f"• Velocidad medida: {measured} km/h")
    if limit:
        tech_lines.append(f"• Velocidad límite: {limit} km/h")
    if radar:
        tech_lines.append(f"• Dispositivo/radar: {radar}")

    tech_block = ""
    if tech_lines:
        tech_block = "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n" + "\n".join(tech_lines) + "\n\n"

    calc_paragraph = build_velocity_calc_paragraph(core)
    tramo_paragraph = build_tramo_error_paragraph(core)

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA DEL CINEMÓMETRO\n\n"
        "La imputación por exceso de velocidad exige acreditación técnica completa y verificable. No basta una referencia genérica al radar o cinemómetro: debe constar de forma precisa el dispositivo utilizado, su situación exacta, su verificación metrológica vigente y la trazabilidad íntegra del dato captado.\n\n"
        "No consta acreditado de forma completa en el expediente:\n"
        "1) Identificación completa del cinemómetro utilizado (marca/modelo/número de serie).\n"
        "2) Certificado de verificación metrológica vigente en la fecha del hecho.\n"
        "3) Acreditación del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020 o la normativa metrológica que corresponda en la fecha del hecho).\n"
        "4) Captura o fotograma completo y legible, con identificación inequívoca del vehículo.\n"
        "5) Aplicación concreta del margen y determinación de la velocidad corregida.\n"
        "6) Acreditación de la cadena de custodia del dato y su correspondencia inequívoca con el vehículo denunciado.\n"
        "7) Acreditación del límite aplicable y de su señalización en el punto exacto.\n\n"
        f"{tech_block}"
        f"{calc_paragraph}\n\n"
    )

    if tramo_paragraph:
        cuerpo += f"{tramo_paragraph}\n\n"

    cuerpo += (
        "ALEGACIÓN SEGUNDA — DEFECTOS DE MOTIVACIÓN Y FALTA DE SOPORTE COMPLETO\n\n"
        "La Administración debe motivar de forma individualizada por qué la velocidad atribuida, una vez aplicado el margen correspondiente, encaja exactamente en el tramo sancionador impuesto. Sin fotograma completo, certificado metrológico, identificación técnica del equipo y acreditación de la cadena de custodia, no puede enervarse la presunción de inocencia con el rigor exigible en Derecho sancionador.\n\n"
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo: boletín/denuncia completa, fotograma o secuencia completa, certificado de verificación metrológica, identificación del equipo, documentación técnica del control y motivación detallada del tramo sancionador aplicado.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba técnica completa para contradicción efectiva.\n"
    )

    return {
        "asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(cuerpo),
    }


def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    if not core.get("hecho_denunciado_literal"):
        literal = extract_hecho_denunciado_literal(core)
        if literal:
            core["hecho_denunciado_literal"] = literal

    tipo = resolve_infraction_type(core)
    jurisdiccion = resolve_jurisdiction(core)

    draft_body = get_hecho_para_recurso(core)
    bicicleta_ctx = _is_bicicleta_context(core)
    dispatched_tpl = None if (tipo == "atencion" and bicicleta_ctx) else dispatch_deterministic_template(core, draft_body=draft_body)

    if isinstance(dispatched_tpl, dict) and dispatched_tpl.get("asunto") and dispatched_tpl.get("cuerpo"):
        tpl = dispatched_tpl
        final_kind = tipo or "deterministic"
    else:
        tpl, final_kind = _select_template(core, tipo, jurisdiccion)

    tpl = ensure_tpl_dict(tpl, core)
    tpl = _upgrade_generated_template(
        tpl.get("asunto") or "",
        tpl.get("cuerpo") or "",
        tipo,
        core,
    )

    cuerpo = tpl.get("cuerpo") or ""
    if tipo == "atencion" and _is_bicicleta_context(core):
        cuerpo = _sanitize_bicicleta_body(cuerpo)
    hecho = get_hecho_para_recurso(core)

    if hecho and not _looks_like_internal_extract(hecho) and hecho.lower() not in cuerpo.lower():
        cuerpo = "Extracto literal del boletín:\n" + f"“{hecho}”\n\n" + cuerpo

    tpl["cuerpo"] = fix_roman_headings(cuerpo)

    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(case_id, "generated", pdf_bytes, ".pdf", "application/pdf")

    conn.execute(
        text("""
            INSERT INTO documents (case_id, kind, b2_bucket, b2_key, mime, created_at)
            VALUES (:case_id, :kind_docx, :bucket, :key_docx, :mime_docx, NOW()),
                   (:case_id, :kind_pdf,  :bucket, :key_pdf,  :mime_pdf,  NOW())
        """),
        {
            "case_id": case_id,
            "kind_docx": f"{final_kind}_docx",
            "kind_pdf": f"{final_kind}_pdf",
            "bucket": b2_bucket,
            "key_docx": b2_key_docx,
            "key_pdf": b2_key_pdf,
            "mime_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "mime_pdf": "application/pdf",
        },
    )

    return {
        "ok": True,
        "kind": final_kind,
        "asunto": tpl["asunto"],
        "cuerpo": tpl["cuerpo"],
        "docx": {"bucket": b2_bucket, "key": b2_key_docx},
        "pdf": {"bucket": b2_bucket, "key": b2_key_pdf},
        "tipo_infraccion": tipo,
        "jurisdiccion": jurisdiccion,
    }


class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado)
    return {"ok": True, "message": "Recurso generado.", **result}
