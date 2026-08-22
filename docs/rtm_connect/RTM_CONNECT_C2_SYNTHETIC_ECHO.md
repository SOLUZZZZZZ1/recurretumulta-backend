# RTM CONNECT C2 · Conector `synthetic.echo`

## Finalidad

C2 incorpora el primer conector ejecutable sobre el Kernel C1, pero permanece
100 % sintético, determinista y sin red. Su función es demostrar la cadena
completa sin depender de un organismo o proveedor:

`acción → autorización → cola → intento → resultado → evidencia → confirmación`

También demuestra el camino:

`unknown → reconciliación → evidencia E4 → confirmed`

## Contrato

- Código: `synthetic.echo`.
- Versión: `v1.0`.
- Modo: `api` simulado sin conexión de red.
- Capacidad: `synthetic.echo`.
- Techo de riesgo: R4, manteniendo las reglas de doble control de CORE.
- Idempotencia: obligatoria.
- Reconciliación: soportada.
- Credenciales: ninguna.
- Persistencia automática: ninguna; el smoke usa rollback.

## Escenarios

- `success`: referencia determinista y evidencia E4 verificada.
- `unknown`: evidencia E2 y reconciliación obligatoria.
- `retryable_failure`: fallo transitorio normalizado.
- `permanent_failure`: fallo terminal normalizado.
- `manual_review`: intervención humana normalizada.

## Límites

C2 no modifica `app.py`, no publica endpoints, no crea tablas, no registra de
forma permanente el conector, no usa B2, correo, Stripe, pagos o presentación
externa y no permite datos reales. Cualquier registro del smoke desaparece por
rollback; el esquema C1 permanece instalado.
