# ADR-0007 · Seguridad y staging

**Estado:** Aceptado y congelado en C0.

C0 no usa red ni base de datos. Staging mantiene desactivados presentación
externa, correo, Stripe y pagos finales; prohíbe datos reales. Los secretos se
resuelven por referencia y no aparecen en acciones, autorizaciones, resultados,
logs, documentos de continuidad ni pruebas.
