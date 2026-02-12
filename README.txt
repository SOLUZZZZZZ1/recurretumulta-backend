RTM PATCH — generate.py AI_FIRST estable (sin SyntaxError) + expediente_engine draft cuando can_generate_draft=true

Qué arregla:
1) generate.py
- Se reescribe con indentación válida.
- AI_FIRST: usa run_expediente_ai(case_id) -> draft.asunto + draft.cuerpo.
- Si no hay draft usable o falla -> fallback a dgt_templates como antes.
- events.resource_generated.payload incluye ai_used y ai_error.
- Compatible con OPS/automation: conserva kinds generated_pdf_* / generated_docx_* y endpoint /generate/dgt.

2) ai/expediente_engine.py
- Genera borrador si admissibility.can_generate_draft == true (aunque admissibility sea NOT_ADMISSIBLE).
- Pasa required_constraints al prompt de draft.

Rollback rápido:
- RTM_DGT_GENERATION_MODE=TEMPLATES_ONLY (vuelves a plantillas 100%)
