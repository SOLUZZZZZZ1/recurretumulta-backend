# ADR-0011 · C4 webhook, UNKNOWN y reconciliación

**Estado:** Aceptado para staging sintético.

## Decisión

C4 incorpora una bandeja durable de webhooks normalizados y un expediente de
reconciliación. La observación solo puede clasificar un intento previamente
ejecutado que está en `unknown`; no crea otro intento ni repite el efecto.

La identidad del ingreso se deduplica exactamente por
`ingress_connector_id + source_event_id`. La identidad del origen se valida de
forma conjunta mediante acción, intento, conector, versión, hash de solicitud y
referencia externa. El conector de ingreso no puede ser el conector de origen.

## Reglas

1. `UNKNOWN` nunca se reintenta a ciegas.
2. Una repetición exacta reutiliza el sobre; el mismo evento con otro contenido
   produce conflicto.
3. La identidad y el contenido normalizado del sobre quedan congelados.
4. No existe correlación parcial, por referencia ambigua ni por orden temporal.
5. El webhook de ingreso y el intento de origen tienen identidades distintas.
6. Solo un intento `unknown`, reconciliable y marcado
   `reconciliation_required`, perteneciente a un conector activo, sintético,
   de staging y sin credenciales puede hacer `match`.
7. La reconciliación no invoca `queue_action` ni `start_attempt`.
8. `confirmed` requiere E4 vinculada exactamente a la acción y al intento y el
   kernel recibe el `evidence_id` concreto.
9. `retryable_failed`, `unknown`, `manual_review` y `permanent_failed` son
   clasificaciones explícitas; solo la primera habilita un posible reintento
   posterior.
10. Los historiales de webhook y reconciliación son append-only.
11. La DLQ es el estado terminal `dead_lettered` de la bandeja, con motivo
    auditable.
12. La prueba de integridad sintética no se presenta como firma real.
13. C4 no publica rutas, no modifica `app.py`, no usa red, datos reales,
    secretos ni efectos externos.
14. El smoke es transaccional y elimina todos los datos sintéticos por
    rollback.
15. Un expediente `started` nunca se presenta como replay resuelto; se informa
    explícitamente que la reconciliación sigue en curso.

## Consecuencias

La incertidumbre se convierte en un proceso trazable en vez de provocar una
segunda ejecución potencialmente duplicada. El modelo deja preparados los
límites para un futuro proveedor sandbox, pero C4 no conecta ninguno ni
rebaja las barreras necesarias para producción.
