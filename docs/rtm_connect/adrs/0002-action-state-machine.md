# ADR-0002 · Máquina de estados

**Estado:** Aceptado y congelado en C0.

Estados: draft, authorized, queued, executing, external_accepted,
evidence_pending, confirmed, retryable_failed, unknown, reconciling,
manual_review, permanent_failed y cancelled.

`unknown` es un estado de primer nivel. Nunca pasa directamente a queued ni a
confirmed. Debe ir a reconciling o manual_review. Los terminales son confirmed,
permanent_failed y cancelled.
