RTM — Mejora V3 (calidad + datos interesado)

Incluye:
1) generate.py
- Si req.interesado viene vacío o parcial, completa desde cases.interested_data automáticamente.
- events.resource_generated.payload añade missing_interested_fields para ver qué falta.

2) ai/prompts/draft_recurso.py (V3)
- Texto más "abogado bueno": ataque principal, estructura fija, ortografía obligatoria.
- Cambia "BORRADOR (no presentar)" por "Borrador para revisión..." cuando sea NOT_ADMISSIBLE pero can_generate_draft=true.
- Incluye reglas para evitar títulos con erratas ("ALEGACIONES/FUNDAMENTOS").

Cómo comprobar:
- Genera y revisa events.resource_generated: ai_used=true y missing_interested_fields vacío en producción.
