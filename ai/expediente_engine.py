# ai/expediente_engine.py

import json
import os
from typing import Any, Dict

from openai import OpenAI
from ai.prompts.rtm_legal_strategy_v1 import RTM_LEGAL_STRATEGY_V1
from ai.prompts.draft_recurso import PROMPT as PROMPT_DRAFT


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


def generate_with_strategy(case_payload: Dict[str, Any]) -> Dict[str, Any]:

    strategy = _llm_json(
        RTM_LEGAL_STRATEGY_V1,
        case_payload
    )

    draft_payload = case_payload.copy()
    draft_payload["strategy"] = strategy

    draft = _llm_json(
        PROMPT_DRAFT,
        draft_payload
    )

    return {
        "strategy": strategy,
        "draft": draft
    }
