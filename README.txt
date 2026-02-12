RTM — Generate DGT AI-first (con fallback a plantillas)

Qué cambia:
- /generate/dgt y cualquier flujo que llame a generate_dgt_for_case ahora intenta:
  1) run_expediente_ai(case_id) -> draft.asunto + draft.cuerpo
  2) Si NO hay draft usable o falla la IA -> usa plantillas dgt_templates como antes

Rollback rápido:
- Variable de entorno RTM_DGT_GENERATION_MODE:
  - AI_FIRST (default): IA primero + fallback
  - TEMPLATES_ONLY: fuerza plantillas (comportamiento previo)

Auditoría:
- event 'resource_generated' incluye:
  - ai_used: true/false
  - ai_error: string|null
