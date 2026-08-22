# ADR-0006 · UNKNOWN y reconciliación

**Estado:** Aceptado y congelado en C0.

Si no puede saberse si el proveedor ejecutó la actuación, el estado es unknown.
No se repite a ciegas. Reconciliation Engine consulta referencias, webhooks,
recibos, estado remoto o deriva una tarea manual. Solo después clasifica como
confirmed, retryable_failed, permanent_failed o manual_review.
