# ADR-0010 · C3 manual_handoff normalizado

**Estado:** Aceptado para staging sintético.

## Decisión

Una actuación manual no será una excepción fuera del sistema. RTM CONNECT la
tratará como conector formal con acción autorizada, intento, paquete congelado,
tarea asignada, plazo, justificante, evidencia y confirmación.

## Reglas

1. CORE autoriza antes de crear la tarea.
2. El paquete se congela mediante manifiesto y SHA-256.
3. Existe una única tarea por acción y por intento.
4. La tarea sigue `prepared → assigned → in_progress → awaiting_receipt →
   receipt_submitted → verified → completed`.
5. El justificante capturado produce E3.
6. Un verificador distinto del ejecutor compara hash y referencia y produce E4.
7. CORE confirma únicamente después de E4.
8. El historial manual es append-only.
9. C3 funciona sin rutas, sin red y sin presentación real.
10. El conector y los datos del smoke desaparecen mediante rollback.

## Consecuencia

C3 demuestra el flujo operativo que posteriormente podrá usar un operador real,
sin fingir que una sede electrónica dispone de API ni rebajar la trazabilidad.
