import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from database import get_engine
from openai import OpenAI

from ai.text_loader import load_text_from_b2
from ai.prompts.classify_documents import PROMPT as PROMPT_CLASSIFY
from ai.prompts.timeline_builder import PROMPT as PROMPT_TIMELINE
from ai.prompts.procedure_phase import PROMPT as PROMPT_PHASE
from ai.prompts.admissibility_guard import PROMPT as PROMPT_GUARD
from ai.prompts.draft_recurso_v2 import PROMPT as PROMPT_DRAFT
from ai.prompts.module_semaforo import module_semaforo

MAX_EXCERPT_CHARS = 12000


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
    """Devuelve el JSONB tal y como está guardado en extractions.extracted_json."""
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


def _detect_capture_mode(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]]) -> str:
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
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

    if ("motivo de no notificación" in blob or "motivo de no notificacion" in blob) and (
        "vehículo en marcha" in blob or "vehiculo en marcha" in blob
    ):
        pass

    if auto_score >= 2 and auto_score >= agent_score + 1:
        return "AUTO"
    if agent_score >= 2 and agent_score >= auto_score + 1:
        return "AGENT"
    return "UNKNOWN"


def _infer_infraction_from_facts_phrases(classify: Dict[str, Any]) -> Optional[str]:
    phrases = (classify or {}).get("facts_phrases") or []
    if not phrases:
        return None
    joined = "\n".join([str(p) for p in phrases if p]).lower()
    if any(s in joined for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
        return "semaforo"
    if any(s in joined for s in ["móvil", "movil", "teléfono", "telefono"]):
        return "movil"
    if any(s in joined for s in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"]):
        return "velocidad"
    return None


def _has_semaforo_signals(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], classify: Optional[Dict[str, Any]] = None) -> bool:
    phrases = (classify or {}).get("facts_phrases") or []
    for p in phrases:
        pl = (p or "").lower()
        if any(s in pl for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            return True

    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False).lower())
    except Exception:
        pass
    for d in docs or []:
        blob_parts.append((d.get("text_excerpt") or "").lower())
    blob = "\n".join(blob_parts)

    signals = ["semáforo", "semaforo", "fase roja", "no respetar la luz roja", "circular con luz roja"]
    return any(s in blob for s in signals)


def _build_facts_summary(extraction_core: Optional[Dict[str, Any]], attack_plan: Dict[str, Any]) -> str:
    inf = ((attack_plan or {}).get("infraction_type") or "").lower()
    try:
        hecho = (extraction_core or {}).get("hecho_imputado")
        if isinstance(hecho, str) and hecho.strip():
            hl = hecho.lower()

            def consistent() -> bool:
                if inf == "semaforo":
                    return any(k in hl for k in ["semáforo", "semaforo", "fase roja", "rojo"])
                if inf == "velocidad":
                    return any(k in hl for k in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"])
                if inf == "movil":
                    return any(k in hl for k in ["móvil", "movil", "teléfono", "telefono"])
                return True

            if consistent():
                return hecho.strip()
    except Exception:
        pass
    return ""


def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()
    traffic = ("tráfico" in organism) or ("dgt" in organism)

    blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    inferred = _infer_infraction_from_facts_phrases(classify)

    triage_tipo = None
    try:
        triage_tipo = (extraction_core or {}).get("tipo_infraccion")
    except Exception:
        triage_tipo = None

    infraction_type = inferred or "generic"

    if infraction_type == "generic" and triage_tipo in ("semaforo", "velocidad", "movil", "atencion", "parking"):
        infraction_type = triage_tipo

    if infraction_type == "generic":
        if any(s in blob for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            infraction_type = "semaforo"
        elif any(s in blob for s in ["teléfono", "telefono", "móvil", "movil"]):
            infraction_type = "movil"
        elif any(s in blob for s in ["km/h", "radar", "cinemómetro", "cinemometro", "velocidad"]):
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
            plan["secondary"].append(
                {
                    "title": "Uso manual del móvil: prueba objetiva y motivación reforzada",
                    "points": [
                        "Debe acreditarse de forma concreta el uso manual (circunstancias y descripción suficiente).",
                        "Si no consta prueba objetiva o descripción detallada, procede el archivo por insuficiencia probatoria.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Boletín/denuncia/acta completa, con identificación del agente si consta.",
                "Descripción detallada del hecho y circunstancias (lugar/hora/forma de observación).",
                "Si existiera: fotografía/vídeo/capturas completas.",
            ]

        if infraction_type == "velocidad":
            plan["secondary"].append(
                {
                    "title": "Velocidad: prueba técnica completa (cinemómetro/radar)",
                    "points": [
                        "Debe constar identificación del cinemómetro y certificado vigente de verificación/calibración.",
                        "Debe constar margen aplicado y capturas completas.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Capturas/fotografías completas del hecho infractor.",
                "Identificación del cinemómetro (marca/modelo/nº serie) y ubicación exacta.",
                "Certificado de verificación/calibración vigente y constancia del margen aplicado.",
            ]

        tl = (timeline or {}).get("timeline") or []
        dates: List[str] = []
        for ev in tl:
            d = ev.get("date")
            if isinstance(d, str) and len(d) >= 10:
                dates.append(d[:10])
        if dates:
            oldest = sorted(dates)[0]
            if oldest.startswith("201") or oldest.startswith("200"):
                plan["secondary"].insert(
                    0,
                    {
                        "title": "Antigüedad del expediente: acreditación de notificación, firmeza y actos interruptivos",
                        "points": [
                            "Dada la antigüedad, corresponde acreditar notificación válida, firmeza y, en su caso, actos interruptivos.",
                            "Si no consta acreditación suficiente, procede el archivo.",
                        ],
                    },
                )
                plan["proof_requests"] += [
                    "Acreditación de la notificación válida (fecha de recepción/acuse/medio).",
                    "Acreditación de firmeza y actuaciones interruptivas, si existieran.",
                    "Estado actual del expediente y fundamento de su vigencia.",
                ]

    return plan


def _map_precept_to_type(extraction_core: Dict[str, Any]) -> Optional[str]:
    if not isinstance(extraction_core, dict):
        return None
    norma_hint = (extraction_core.get("norma_hint") or "").upper()
    precepts = extraction_core.get("preceptos_detectados") or []

    if "8/2004" in norma_hint or any("8/2004" in (p or "") for p in precepts) or any("LSOA" in (p or "").upper() for p in precepts):
        return "seguro"

    art = extraction_core.get("articulo_infringido_num")
    if isinstance(art, str) and art.isdigit():
        art = int(art)
    if isinstance(art, int):
        if art in (12, 15):
            return "condiciones_vehiculo"
        if art == 18:
            return "atencion"
        if art == 167:
            return "marcas_viales"

    if any("2822/98" in (p or "") for p in precepts) or "2822/98" in norma_hint:
        return "condiciones_vehiculo"

    blob = json.dumps(extraction_core, ensure_ascii=False).lower()
    if "9.1 bis" in blob or "9,1 bis" in blob:
        return "no_identificar"

    return None


def _apply_tipicity_guard(attack_plan: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    inferred = (plan.get("infraction_type") or "").lower().strip()
    mapped = (_map_precept_to_type(extraction_core) or "").lower().strip()

    if mapped and inferred in ("", "generic", "otro"):
        plan["infraction_type"] = mapped
        plan.setdefault("meta", {})
        plan["meta"]["precept_forced_type"] = mapped
        return plan

    if mapped and inferred and mapped != inferred:
        sec = plan.get("secondary") or []
        sec = list(sec) if isinstance(sec, list) else []
        sec.insert(
            0,
            {
                "title": "Principio de tipicidad: posible incongruencia entre el precepto citado y el hecho denunciado",
                "points": [
                    "La Administración debe subsumir el hecho descrito en el precepto concreto citado, con motivación suficiente.",
                    "Si el hecho denunciado no encaja en el artículo indicado, se vulnera el principio de tipicidad (Derecho sancionador) y procede el archivo.",
                    "Se solicita aclaración y acreditación completa del encaje típico, aportando el expediente íntegro y la base normativa aplicada.",
                ],
            },
        )
        plan["secondary"] = sec

        pr = plan.get("proof_requests") or []
        pr = list(pr) if isinstance(pr, list) else []
        pr += [
            "Copia íntegra del expediente administrativo (incluyendo propuesta/resolución y fundamentos).",
            "Identificación expresa del precepto aplicado (artículo/apartado) y su encaje con el hecho denunciado.",
            "Aportación de la norma/ordenanza aplicable y motivación completa.",
        ]
        seen = set()
        pr2 = []
        for x in pr:
            if x not in seen:
                seen.add(x)
                pr2.append(x)
        plan["proof_requests"] = pr2

        plan.setdefault("meta", {})
        plan["meta"]["tipicity_mismatch"] = {"mapped": mapped, "inferred": inferred}

    return plan


def _compute_context_intensity(timeline: Dict[str, Any], extraction_core: Dict[str, Any], classify: Dict[str, Any]) -> str:
    blob = ""
    try:
        blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    except Exception:
        blob = ""

    precepts = (extraction_core or {}).get("preceptos_detectados") or []
    pre_blob = " ".join([str(p) for p in precepts]).lower()

    has_lsoa = ("lsoa" in pre_blob) or ("8/2004" in pre_blob) or ("8/2004" in blob)
    has_speed = ("km/h" in blob) or ("cinemómetro" in blob) or ("cinemometro" in blob) or ("radar" in blob)
    if has_lsoa and has_speed:
        return "critico"

    dates: List[str] = []
    tl = (timeline or {}).get("timeline") or []
    for ev in tl:
        d = ev.get("date")
        if isinstance(d, str) and len(d) >= 10:
            dates.append(d[:10])
    for k in ("fecha_documento", "fecha_notificacion"):
        v = (extraction_core or {}).get(k)
        if isinstance(v, str) and len(v) >= 10:
            dates.append(v[:10])

    if dates:
        oldest = sorted(dates)[0]
        if oldest[:4].isdigit() and int(oldest[:4]) <= 2015:
            return "reforzado"

    return "normal"


def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    extraction_wrapper = _load_latest_extraction(case_id) or {}
    extraction_core = (extraction_wrapper.get("extracted") or {}) if isinstance(extraction_wrapper, dict) else {}

    capture_mode = _detect_capture_mode(docs, extraction_core)

    classify = _llm_json(
        PROMPT_CLASSIFY,
        {"case_id": case_id, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    timeline = _llm_json(
        PROMPT_TIMELINE,
        {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    phase = _llm_json(
        PROMPT_PHASE,
        {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": extraction_wrapper},
    )

    admissibility = _llm_json(
        PROMPT_GUARD,
        {
            "case_id": case_id,
            "recommended_action": phase,
            "timeline": timeline,
            "classification": classify,
            "latest_extraction": extraction_wrapper,
        },
    )

    flags = _load_case_flags(case_id)
    if flags.get("test_mode") and flags.get("override_deadlines"):
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["deadline_status"] = admissibility.get("deadline_status") or "UNKNOWN"
        admissibility["required_constraints"] = admissibility.get("required_constraints") or []
        _save_event(case_id, "test_override_applied", {"flags": flags})

    force_semaforo = _has_semaforo_signals(docs, extraction_core, classify)

    if force_semaforo:
        sem = module_semaforo()
        secondary_attacks = list(sem.get("secondary_attacks") or [])

        if capture_mode == "AUTO":
            secondary_attacks.insert(
                0,
                {
                    "title": "Captación automática: exigencia de secuencia completa y verificación del sistema",
                    "points": [
                        "Debe aportarse secuencia completa que permita verificar fase roja activa en el instante del cruce.",
                        "Debe acreditarse el correcto funcionamiento/sincronización del sistema de captación.",
                    ],
                },
            )
        elif capture_mode == "AGENT":
            secondary_attacks.insert(
                0,
                {
                    "title": "Denuncia presencial: motivación reforzada y descripción detallada de la observación",
                    "points": [
                        "Debe describirse con precisión la observación (ubicación, visibilidad, distancia y circunstancias).",
                        "La falta de detalle impide contradicción efectiva y genera indefensión.",
                    ],
                },
            )
        else:
            secondary_attacks.insert(
                0,
                {
                    "title": "Tipo de captación no concluyente: aportar prueba completa para evitar indefensión",
                    "points": [
                        "Debe aportarse la prueba completa del hecho: secuencia/fotogramas si captación automática, o descripción detallada si denuncia presencial.",
                        "En caso de no constar, procede el archivo por insuficiencia probatoria.",
                    ],
                },
            )

        attack_plan = {
            "infraction_type": "semaforo",
            "primary": {
                "title": (sem.get("primary_attack") or {}).get("title") or "Insuficiencia probatoria",
                "points": (sem.get("primary_attack") or {}).get("points") or [],
            },
            "secondary": [{"title": sa.get("title"), "points": sa.get("points") or []} for sa in secondary_attacks],
            "proof_requests": sem.get("proof_requests") or [],
            "petition": {
                "main": "Archivo / estimación íntegra",
                "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
            },
            "meta": {"capture_mode": capture_mode, "forced": True},
        }
    else:
        attack_plan = _build_attack_plan(classify, timeline, extraction_core or {})

    attack_plan = _apply_tipicity_guard(attack_plan, extraction_core)
    facts_summary = _build_facts_summary(extraction_core, attack_plan)
    context_intensity = _compute_context_intensity(timeline, extraction_core, classify)

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
                "latest_extraction": extraction_wrapper,
                "extraction_core": extraction_core,
                "attack_plan": attack_plan,
                "facts_summary": facts_summary,
                "context_intensity": context_intensity,
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
        "context_intensity": context_intensity,
        "extraction_debug": {
            "wrapper_keys": list(extraction_wrapper.keys()) if isinstance(extraction_wrapper, dict) else [],
            "core_keys": list(extraction_core.keys()) if isinstance(extraction_core, dict) else [],
        },
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
