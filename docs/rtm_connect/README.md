# RTM CONNECT · C0 · Arquitectura congelada

## Regla de autoridad

**CORE autoriza; CONNECT ejecuta; la evidencia confirma; solo entonces CORE
puede cambiar el estado jurídico.**

C0 no publica rutas, no crea tablas, no registra conectores y no ejecuta efectos
externos. Su objeto es convertir la Arquitectura Maestra v1.0 en contratos
versionados, comprobables y difíciles de alterar accidentalmente.

## Contenido congelado

- Contrato `ConnectActionRequest`.
- Autorización `AuthorizationGrant` aprobada y congelada.
- Resultado `ConnectExecutionResult`.
- Máquina de estados, incluido `unknown`.
- Idempotencia determinista anterior a toda llamada externa.
- Evidencia E0–E4 y puerta de confirmación por riesgo.
- Riesgos R0–R4 y doble control para R4.
- Modos API, webhook, polling, batch, assisted y manual.
- Prohibición de decisiones jurídicas en conectores.
- Secretos por referencia, nunca incrustados.
- Orden C0 → C8.

## Estado operativo

- Runtime CONNECT: no publicado.
- Esquema PostgreSQL CONNECT: no creado.
- Efectos externos: desactivados.
- Datos reales: prohibidos.
