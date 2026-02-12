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
from ai.prompts.draft_recurso import PROMPT as PROMPT_DRAFT

MAX_EXCERPT_CHARS = 12000


# =========================================================
# OpenAI JSON helper (SDK directo, sin openai_text)
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
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
                """
            ),
            {"case_id": case_id, "type": event_type, "payload": json.dumps(payload)},
        )


def _load_latest_extraction(case_id: str) -> Optional[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT extracted_json
                FROM extractions
                WHERE case_id=:case_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"case_id": case_id},
        ).fetchone()
    return row[0] if row else None


def _load_interested_data(case_id: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT interested_data FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return (row[0] if row else None) or {}


def _load_case_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at
                FROM documents
                WHERE case_id=:case_id
                ORDER BY created_at ASC
                """
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
# MAIN ORCHESTRATOR
# =========================================================
def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    latest_extraction = _load_latest_extraction(case_id)

    classify = _llm_json(
        PROMPT_CLASSIFY,
        {"case_id": case_id, "documents": docs, "latest_extraction": latest_extraction},
    )

    timeline = _llm_json(
        PROMPT_TIMELINE,
        {
            "case_id": case_id,
            "classification": classify,
            "documents": docs,
            "latest_extraction": latest_extraction,
        },
    )

    phase = _llm_json(
        PROMPT_PHASE,
        {
            "case_id": case_id,
            "classification": classify,
            "timeline": timeline,
            "latest_extraction": latest_extraction,
        },
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

    draft = None
    if bool(admissibility.get("can_generate_draft")):
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
                "required_constraints": admissibility.get("required_constraints") or [],
                "latest_extraction": latest_extraction,
            },
        )

    result = {
        "ok": True,
        "case_id": case_id,
        "classify": classify,
        "timeline": timeline,
        "phase": phase,
        "admissibility": admissibility,
        "draft": draft,
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
