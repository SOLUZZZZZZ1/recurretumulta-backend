# RTM CONNECT C1 · Kernel y esquema PostgreSQL

C1 materializa la persistencia interna de RTM CONNECT sin publicar runtime.

## Tablas

- `rtm_connect_connectors`: registro versionado de conectores.
- `rtm_connect_actions`: Action Ledger y estado vigente.
- `rtm_connect_authorizations`: autorizaciones CORE congeladas.
- `rtm_connect_attempts`: cada intento técnico separado.
- `rtm_connect_evidence`: evidencia E0–E4 append-only.
- `rtm_connect_transitions`: historial de estados append-only.
- `rtm_connect_idempotency_claims`: guardia idempotente y replays.

## Invariantes

- Ninguna transición inválida puede guardarse.
- `unknown` no vuelve directamente a `queued` ni a `confirmed`.
- La autorización es inmutable.
- Evidencia y transiciones son append-only.
- La confirmación usa la puerta de evidencia de C0.
- C1 no registra conectores persistentes, no usa red y no publica endpoints.
- Staging mantiene presentación, correo, Stripe y pagos finales desactivados.
