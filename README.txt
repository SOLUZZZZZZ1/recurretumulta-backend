RTM — generate.py (AI_FIRST + Override MODO PRUEBA B)

Qué hace:
- Genera con IA primero (run_expediente_ai). Si falla, fallback a plantillas.
- Si el case tiene test_mode=true y override_deadlines=true:
  - El asunto se marca con '(MODO PRUEBA)'
  - Se elimina el prefijo 'Borrador...' en asunto y, si apareciera como primera línea del cuerpo, se elimina.

Verificación:
- events.resource_generated.payload incluirá override_mode=true cuando aplique.
