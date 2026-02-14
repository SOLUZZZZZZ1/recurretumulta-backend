import json
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from database import get_engine
from openai import OpenAI

from ai.text_loader import load_text_from_b2
from ai.prompts.classify_documents import PROMPT as PROMPT_CLASSIFY
from ai.prompts.timeline_builder import PROMPT as PROMPT_TIMELINE
from ai.prompts.procedure_phase import PROMPT as PROMPT_PHASE
from ai.prompts.admissibility_guard import PROMPT as PROMPT_GUARD
from ai.prompts.draft_recurso import PROMPT as PROMPT_DRAFT
from ai.prompts.module_semaforo import module_semaforo

MAX_EXCERPT_CHARS = 12000


# =========================================================
# Semáforo: detección de tipo de captación (heurística)
# =========================================================
def _detect_capture_mode(docs: List[Dict[str, Any]], latest_extraction: Optional[Dict[str, Any]]) -> str:
    '''
    Devuelve: 'AUTO' (captación automática/cámara), 'AGENT' (agente presencial),
    o 'UNKNOWN' si no se puede determinar con fiabilidad.
    Heurístico y conservador: ante duda -> UNKNOWN.
    '''
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(latest_extraction or {}, ensure_ascii=False))
    except Exception:
        pass

    for d in docs or []:
        t = (d.get("text_excerpt") or "")
        if t:
            blob_parts.append(t)

    blob = "\n".join(blob_parts).lower()

    auto_signals = [
        "cámara", "camara", "fotograma", "fotogramas", "secuencia", "foto", "fotografía", "fotografia",
        "captación automática", "captacion automatica", "sistema automático", "sistema automatico",
        "dispositivo", "sensor", "instalación", "instalacion", "vídeo", "video"
    ]
    agent_signals = [
        "agente", "policía", "policia", "guardia civil", "denunciante", "observó", "observo",
        "manifestó", "manifesto", "presencial", "in situ"
    ]

    auto_score = sum(1 for s in auto_signals if s in blob)
    agent_score = sum(1 for s in agent_signals if s in blob)

    # Señal frecuente en boletines: no notificación en acto por vehículo en marcha
    # No prueba cámara, pero refuerza que NO fue notificación en mano.
    if "motivo de no notificación" in blob or "motivo de no notificacion" in blob:
        if "vehículo en marcha" in blob or "vehiculo en marcha" in blob:
            auto_score += 0
            agent_score += 0

    if auto_score >= 2 and auto_score >= agent_score + 1:
        return "AUTO"
    if agent_score >= 2 and agent_score >= auto_score + 1:
        return "AGENT"
    return "UNKNOWN"


def _build_facts_summary(
    classification: Dict[str, Any],
    timeline: Dict[str, Any],
    latest_extraction: Optional[Dict[str, Any]],
    docs: List[Dict[str, Any]],
    attack_plan: Dict[str, Any],
) -> str:
    '''
    Construye un facts_summary breve y conservador para que el recurso "suene" al expediente real.

    Reglas de seguridad (muy importante):
    - Si latest_extraction.extracted.hecho_imputado existe, SOLO se usa si es consistente con el tipo de infracción.
      (Ej: si es semáforo, debe mencionar semáforo/luz roja/fase roja; si no, se ignora.)
    - Si no hay hecho imputado consistente, se deja vacío para que el prompt de redacción use su plantilla correcta
      según attack_plan.infraction_type (semáforo/velocidad/móvil/...).
    - Además, se añade un resumen mínimo con organismo/expediente/fecha y "vehículo en marcha" si consta.
    '''
    inf = ((attack_plan or {}).get("infraction_type") or "").lower()

    # 1) Intentar usar "hecho_imputado" SOLO si es consistente con la infracción detectada
    try:
        hecho = ((latest_extraction or {}).get("extracted") or {}).get("hecho_imputado")
        if isinstance(hecho, str) and hecho.strip():
            h = hecho.strip()
            hl = h.lower()

            def _consistent() -> bool:
                if inf == "semaforo":
                    return any(k in hl for k in ["semáforo", "semaforo", "luz roja", "fase roja", "rojo"])
                if inf == "velocidad":
                    return any(k in hl for k in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"])
                if inf == "movil":
                    return any(k in hl for k in ["móvil", "movil", "teléfono", "telefono"])
                # genérico: si no sabemos, aceptar
                return True

            if _consistent():
                return h
    except Exception:
        pass

    # 2) Si no es consistente, NO metemos un hecho erróneo. Devolvemos vacío y el prompt pondrá el hecho correcto.
    #    Aun así, añadimos un mini-resumen neutral (organismo/expediente/fecha + motivo "vehículo en marcha").
    parts: List[str] = []
    global_refs = (classification or {}).get("global_refs") or {}
    organismo = global_refs.get("main_organism")
    expediente_ref = global_refs.get("expediente_ref")

    if organismo:
        parts.append(f"Organismo: {organismo}.")
    if expediente_ref:
        parts.append(f"Expediente: {expediente_ref}.")

    tl = (timeline or {}).get("timeline") or []
    notif_date = None
    for ev in tl:
        act = (ev.get("act_type") or "").lower()
        if "notific" in act:
            d = ev.get("date")
            if isinstance(d, str) and len(d) >= 10:
                notif_date = d[:10]
                break
    if notif_date:
        parts.append(f"Notificación: {notif_date}.")

    blob = ""
    try:
        blob = json.dumps(latest_extraction or {}, ensure_ascii=False).lower()
    except Exception:
        blob = ""
    if "vehículo en marcha" in blob or "vehiculo en marcha" in blob:
        parts.append("Motivo de no notificación en acto: vehículo en marcha.")

    # Si solo hay meta neutral, devolvemos "" para que el prompt ponga el "Hecho imputado" correcto.
    # El meta neutral lo mandamos en notes_for_operator vía evento, no en facts_summary.
    return ""





# =========================================================
# Señales fuertes para forzar SEMÁFORO (determinista)
# =========================================================
def _has_semaforo_signals(docs: List[Dict[str, Any]], latest_extraction: Optional[Dict[str, Any]]) -> bool:
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(latest_extraction or {}, ensure_ascii=False).lower())
    except Exception:
        pass
    for d in docs or []:
        blob_parts.append((d.get("text_excerpt") or "").lower())

    blob = "\n".join(blob_parts)

    signals = [
        "semáforo",
        "semaforo",
        "luz roja",
        "fase roja",
        "no respetar la luz roja",
        "circular con luz roja",
    ]
    return any(s in blob for s in signals)


# =========================================================
# OpenAI JSON helper (igual que tu versión estable)
# =========================================================
def _llm_json(prompt: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    return json.loads(resp.choices[0].message.content)


# =========================================================
# DB helpers
# =========================================================
def _save_event(case_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
            ),
            {"case_id": case_id, "type": event_type, "payload": json.dumps(payload)},
        )


def _load_latest_extraction(case_id: str) -> Optional[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
            {"case_id": case_id},
        ).fetchone()
    return row[0] if row else None


def _load_interested_data(case_id: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return (row[0] if row and row[0] else {}) or {}


def _load_case_flags(case_id: str) -> Dict[str, bool]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return {"test_mode": bool(row[0]) if row else False, "override_deadlines": bool(row[1]) if row else False}


def _load_case_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at "
                "FROM documents WHERE case_id=:case_id ORDER BY created_at ASC"
            ),
            {"case_id": case_id},
        ).fetchall()

    docs: List[Dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        kind, bucket, key, mime, size_bytes, created_at = r
        text_excerpt = load_text_from_b2(bucket, key, mime)
        if text_excerpt:
            text_excerpt = text_excerpt[:MAX_EXCERPT_CHARS]

        docs.append(
            {
                "doc_index": i,
                "kind": kind,
                "bucket": bucket,
                "key": key,
                "mime": mime,
                "size_bytes": int(size_bytes or 0),
                "created_at": str(created_at),
                "text_excerpt": text_excerpt or "",
            }
        )
    return docs


# =========================================================
# Attack plan determinista (SIN imports nuevos)
# =========================================================
def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], latest_extraction: Dict[str, Any]) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()
    traffic = ("tráfico" in organism) or ("dgt" in organism)

    blob = json.dumps(latest_extraction or {}, ensure_ascii=False).lower()

    # Fuente de verdad si viene del primer triaje (/analyze)
    triage_tipo = None
    triage_hecho = None
    try:
        triage_tipo = (latest_extraction or {}).get('extracted', {}).get('tipo_infraccion')
        triage_hecho = (latest_extraction or {}).get('extracted', {}).get('hecho_imputado')
    except Exception:
        triage_tipo = None
        triage_hecho = None


    infraction_type = "generic"
    if triage_tipo in ("semaforo","velocidad","movil","atencion","parking"):
        infraction_type = triage_tipo
    if "teléfono" in blob or "telefono" in blob or "móvil" in blob or "movil" in blob:
        infraction_type = "movil"
    elif "km/h" in blob or "radar" in blob or "cinemómetro" in blob or "cinemometro" in blob:
        infraction_type = "velocidad"

    plan = {
        "infraction_type": infraction_type,
        "primary": {
            "title": "Presunción de inocencia e insuficiencia probatoria (art. 24 CE)",
            "points": [
                "La carga de la prueba corresponde a la Administración.",
                "No cabe sancionar sin prueba suficiente y concreta del hecho infractor.",
            ],
        },
        "secondary": [],
        "proof_requests": [],
        "petition": {
            "main": "Archivo / estimación íntegra",
            "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
        },
    }

    if traffic:
        if infraction_type == "movil":
            plan["secondary"].append({
                "title": "Uso manual del móvil: prueba objetiva y motivación reforzada",
                "points": [
                    "Debe acreditarse de forma concreta el uso manual (circunstancias y descripción suficiente).",
                    "Si no consta prueba objetiva o descripción detallada, procede el archivo por insuficiencia probatoria.",
                ],
            })
            plan["proof_requests"] += [
                "Boletín/denuncia/acta completa, con identificación del agente si consta.",
                "Descripción detallada del hecho y circunstancias (lugar/hora/forma de observación).",
                "Si existiera: fotografía/vídeo/capturas completas.",
            ]

        if infraction_type == "velocidad":
            plan["secondary"].append({
                "title": "Velocidad: prueba técnica completa (cinemómetro/radar)",
                "points": [
                    "Debe constar identificación del cinemómetro y certificado vigente de verificación/calibración.",
                    "Debe constar margen aplicado y capturas completas.",
                ],
            })
            plan["proof_requests"] += [
                "Capturas/fotografías completas del hecho infractor.",
                "Identificación del cinemómetro (marca/modelo/nº serie) y ubicación exacta.",
                "Certificado de verificación/calibración vigente y constancia del margen aplicado.",
            ]

        # Antigüedad: si hay fechas muy antiguas, exigir acreditación de notificación/firmeza/actos interruptivos
        tl = (timeline or {}).get("timeline") or []
        dates = []
        for ev in tl:
            d = ev.get("date")
            if isinstance(d, str) and len(d) >= 10:
                dates.append(d[:10])
        if dates:
            oldest = sorted(dates)[0]
            if oldest.startswith("201") or oldest.startswith("200"):
                plan["secondary"].insert(0, {
                    "title": "Antigüedad del expediente: acreditación de notificación, firmeza y actos interruptivos",
                    "points": [
                        "Dada la antigüedad, corresponde acreditar notificación válida, firmeza y, en su caso, actos interruptivos.",
                        "Si no consta acreditación suficiente, procede el archivo.",
                    ],
                })
                plan["proof_requests"] += [
                    "Acreditación de la notificación válida (fecha de recepción/acuse/medio).",
                    "Acreditación de firmeza y actuaciones interruptivas, si existieran.",
                    "Estado actual del expediente y fundamento de su vigencia.",
                ]

    return plan


# =========================================================
# MAIN ORCHESTRATOR (tu flujo intacto)
# =========================================================
def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    latest_extraction = _load_latest_extraction(case_id)

    capture_mode = _detect_capture_mode(docs, latest_extraction)

    triage_hecho_global = None
    try:
        triage_hecho_global = (latest_extraction or {}).get('extracted', {}).get('hecho_imputado')
    except Exception:
        triage_hecho_global = None


    classify = _llm_json(
        PROMPT_CLASSIFY,
        {"case_id": case_id, "documents": docs, "latest_extraction": latest_extraction},
    )

    timeline = _llm_json(
        PROMPT_TIMELINE,
        {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": latest_extraction},
    )

    phase = _llm_json(
        PROMPT_PHASE,
        {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": latest_extraction},
    )

    admissibility = _llm_json(
        PROMPT_GUARD,
        {
            "case_id": case_id,
            "recommended_action": phase,
            "timeline": timeline,
            "classification": classify,
            "latest_extraction": latest_extraction,
        },
    )

    # Override pruebas (tu lógica)
    flags = _load_case_flags(case_id)
    if flags.get("test_mode") and flags.get("override_deadlines"):
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["deadline_status"] = admissibility.get("deadline_status") or "UNKNOWN"
        admissibility["required_constraints"] = admissibility.get("required_constraints") or []
        _save_event(case_id, "test_override_applied", {"flags": flags})
# Attack plan (determinista) + FORZADO semáforo si hay señales fuertes
force_semaforo = _has_semaforo_signals(docs, latest_extraction)

if force_semaforo:
    attack_plan = {"infraction_type": "semaforo"}
else:
    attack_plan = _build_attack_plan(classify, timeline, latest_extraction or {})


    # Si es semáforo, usamos módulo específico + ajustamos según tipo de captación
    if (attack_plan.get('infraction_type') or '') == 'semaforo':
        try:
            sem = module_semaforo()
            secondary_attacks = list(sem.get('secondary_attacks') or [])
            if capture_mode == 'AUTO':
                secondary_attacks.insert(0, {
                    'title': 'Captación automática: exigencia de secuencia completa y verificación del sistema',
                    'points': [
                        'Debe aportarse secuencia completa que permita verificar fase roja activa en el instante del cruce.',
                        'Debe acreditarse el correcto funcionamiento/sincronización del sistema de captación.'
                    ]
                })
            elif capture_mode == 'AGENT':
                secondary_attacks.insert(0, {
                    'title': 'Denuncia presencial: motivación reforzada y descripción detallada de la observación',
                    'points': [
                        'Debe describirse con precisión la observación (ubicación, visibilidad, distancia y circunstancias).',
                        'La falta de detalle impide contradicción efectiva y genera indefensión.'
                    ]
                })
            else:
                secondary_attacks.insert(0, {
                    'title': 'Tipo de captación no concluyente: aportar prueba completa (sistema o denuncia) para evitar indefensión',
                    'points': [
                        'Debe aportarse la prueba completa del hecho: o bien secuencia/fotogramas si captación automática, o bien descripción detallada si denuncia presencial.',
                        'En caso de no constar, procede el archivo por insuficiencia probatoria.'
                    ]
                })

            attack_plan = {
                'infraction_type': 'semaforo',
                'primary': {
                    'title': (sem.get('primary_attack') or {}).get('title') or 'Insuficiencia probatoria',
                    'points': (sem.get('primary_attack') or {}).get('points') or [],
                },
                'secondary': [
                    {'title': sa.get('title'), 'points': sa.get('points') or []}
                    for sa in secondary_attacks
                ],
                'proof_requests': sem.get('proof_requests') or [],
                'petition': {
                    'main': 'Archivo / estimación íntegra',
                    'subsidiary': 'Subsidiariamente, práctica de prueba y aportación documental completa',
                },
                'meta': {'capture_mode': capture_mode},
            }
        except Exception:
            pass

    facts_summary = _build_facts_summary(classify, timeline, latest_extraction, docs, attack_plan)

    draft = None
    if bool(admissibility.get("can_generate_draft")) or (admissibility.get("admissibility") or "").upper() == "ADMISSIBLE":
        interested_data = _load_interested_data(case_id)
        draft = _llm_json(
            PROMPT_DRAFT,
            {
                "case_id": case_id,
                "interested_data": interested_data,
                "classification": classify,
                "timeline": timeline,
                "recommended_action": phase,
                "admissibility": admissibility,
                "latest_extraction": latest_extraction,
                "attack_plan": attack_plan,
                "facts_summary": facts_summary,
            },
        )

    result = {
        "ok": True,
        "case_id": case_id,
        "classify": classify,
        "timeline": timeline,
        "phase": phase,
        "admissibility": admissibility,
        "attack_plan": attack_plan,
        "draft": draft,
        "capture_mode": capture_mode,
        "facts_summary": facts_summary,
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
