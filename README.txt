RTM FIX — IA end-to-end (draft aunque NOT_ADMISSIBLE) + generate AI_FIRST fallback

Incluye 2 cambios CLAVE:

1) ai/expediente_engine.py
- Antes: solo generaba draft si admissibility == ADMISSIBLE
- Ahora: genera draft si can_generate_draft == true
  (evita draft=None y permite que /generate/dgt use IA)

2) generate.py
- AI_FIRST por defecto: intenta run_expediente_ai(case_id) y usa draft.asunto + draft.cuerpo
- Si falla o viene vacío: fallback a plantillas como siempre
- Event 'resource_generated' guarda ai_used y ai_error

Verificación rápida:
- POST /ai/expediente/run -> events: ai_expediente_result con draft NO null
- POST /generate/dgt -> events: resource_generated con ai_used=true (si usa IA)

Rollback:
- RTM_DGT_GENERATION_MODE=TEMPLATES_ONLY
