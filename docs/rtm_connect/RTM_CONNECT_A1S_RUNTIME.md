# RTM CONNECT A1-S Runtime — validación sintética en staging

## Estado y veredicto

**Estado: `pending_external_execution`.**

Este sobre define la validación Runtime del workflow A1-S sobre la base exacta
`a94dcd314c67880e40aa333dc679ef98b80a1956`. A fecha 2026-08-25 todavía no
se ha congelado ni desplegado el overlay Runtime. Tampoco existe evidencia
externamente congelada del preflight offline, del audit/provisioning de
fixtures Runtime ni del smoke E2E transaccional contra PostgreSQL en Render.

Por tanto, este documento no acredita que el Runtime esté listo, que el flujo
sea utilizable desde el frontend ni que exista una activación live. El veredicto
para producción y para cualquier presentación con efecto jurídico continúa
siendo **`NO-GO`**.

## Identidad de entrega: base congelada y overlay pendiente

| Elemento | Estado |
|---|---|
| Commit base | `a94dcd314c67880e40aa333dc679ef98b80a1956` |
| ZIP base | `RTM_CONNECT_A1S_RUNTIME_BASE_a94dcd3.zip` |
| SHA-256 base | `4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21` |
| Commit del overlay Runtime | pendiente |
| ZIP y SHA-256 del overlay Runtime | pendientes |
| Deploy Render del overlay | no observado |

El commit base contiene A1-S y el hotfix de ejecución DDL PostgreSQL. Esa
identidad de código no demuestra por sí sola provisioning Runtime, ejecución
del workflow, rollback, ausencia de residuo ni salud del despliegue posterior.

## Alcance del sobre Runtime

La entrega queda limitada a doce paths exactos:

- `rtm_connect/human_filing_runtime.py`;
- `scripts/rtm_connect_a1s_runtime_preflight.py`;
- `scripts/rtm_staging_connect_a1s_runtime_fixture.py`;
- `scripts/rtm_connect_a1s_runtime_smoke.py`;
- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md`;
- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json`;
- `docs/rtm_connect/adrs/0019-a1s-runtime-validation.md`;
- `tests/test_rtm_connect_a1s_runtime_contract.py`;
- `tests/test_rtm_connect_a1s_runtime_fixture_script_contract.py`;
- `tests/test_rtm_connect_a1s_runtime_preflight_contract.py`;
- `tests/test_rtm_connect_a1s_runtime_smoke_contract.py`;
- `tests/test_rtm_connect_a1s_runtime_docs_contract.py`.

El módulo y los scripts deben preparar y validar únicamente fixtures
sintéticas en una base PostgreSQL de staging aislada. No añaden un transport,
worker, webhook, acceso a B2, proveedor, sede o Administración.

## Frontera de entorno obligatoria

Toda operación con PostgreSQL debe fallar antes de abrir la conexión salvo que
se cumplan simultáneamente las siguientes condiciones:

- `RTM_ENV=staging`;
- `RTM_INSTANCE_ID` y `RTM_DATA_NAMESPACE` identifican staging y excluyen
  `production`, `prod` y `live`;
- `DATABASE_URL` es PostgreSQL, identifica una base staging y declara un rol;
- `RTM_EXPECTED_BRANCH` es una rama explícita distinta de `main`/`master`, y
  coincide exactamente con `RENDER_GIT_BRANCH` o `GIT_BRANCH`;
- `RTM_ENVIRONMENT_CONFIRMATION=RTM_STAGING_ISOLATED`;
- `RTM_SIDE_EFFECT_POLICY=isolated`;
- `RTM_DOCUMENT_INPUT_POLICY=synthetic_only`;
- `RTM_ALLOW_REAL_CUSTOMER_DATA=0`;
- `RTM_ENABLE_B2=0`;
- `RTM_ENABLE_DOCUMENT_PROVIDER=0`;
- `RTM_ENABLE_EXTERNAL_SUBMISSION=0`;
- `RTM_ENABLE_OUTBOUND_EMAIL=0`;
- `RTM_ENABLE_STRIPE=0`;
- `RTM_ENABLE_FINAL_PAYMENTS=0`;
- `RTM_CONNECT_A1S_NETWORK_ALLOWED=0`;
- `RTM_CONNECT_A1S_B2_ALLOWED=0`;
- `RTM_CONNECT_A1S_PROVIDER_ALLOWED=0`;
- `RTM_CONNECT_A1S_REAL_DATA_ALLOWED=0`;
- `RTM_CONNECT_A1S_EXTERNAL_EFFECTS_ALLOWED=0`;
- `RTM_ENABLE_CONNECT_SUPERVISOR_V1=0`;
- `RTM_ENABLE_CONNECT_C6_SANDBOX=0`;
- `RTM_ENABLE_CONNECT_C7_ASSISTED=0`;
- `RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION=0`;
- `RTM_ENABLE_CONNECT_C8_LIVE=0`;
- `RTM_ENABLE_CONNECT_A1S_HUMAN_FILING=0` al iniciar cada comando;
- `RTM_ENABLE_OPERATOR_AUTH_V1=0` al iniciar cada comando;
- para el smoke, `RTM_OPERATOR_ACCESS_HMAC_KEY` está configurada con al menos
  32 caracteres, pero su valor no se imprime ni se incorpora a evidencia.

Las variables de endpoint, origen, proveedor, credencial y B2 específicas de
A1-S deben permanecer vacías. Durante provisioning, las rutas A1-S y la
autenticación individual permanecen apagadas. Solo el smoke, dentro de su
proceso y durante una ventana acotada, puede habilitar temporalmente
`RTM_ENABLE_OPERATOR_AUTH_V1=1` y
`RTM_ENABLE_CONNECT_A1S_HUMAN_FILING=1`; debe restaurar después los valores
originales.

## Secuencia de admisión

La validación deberá ejecutarse en este orden y detenerse ante el primer
bloqueo:

1. **Preflight offline.** Audita el ZIP base exacto y el overlay sin extraerlo,
   sin importar el runtime, sin abrir PostgreSQL y sin resolver secretos.
2. **Audit de schema y fixtures.** Abre exclusivamente la base staging cuya
   identidad coincide con la configuración congelada y comprueba schema,
   constraints y readiness de la cohorte sintética.
3. **Provisioning confirmado.** Si faltan fixtures, el modo de escritura exige
   la frase literal `STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY`. Debe ser
   creation-only, idempotente y transaccional; no puede actualizar, borrar o
   reciclar filas preexistentes ni fixtures consumidas. Las tablas A1-S reciben
   solo inserts; la acción CORE recién creada transiciona de `draft` a
   `authorized` dentro de la misma transacción. La cohorte congelada usa la clave
   `runtime-a94dcd3-v1` y queda ligada a los tres UUID seleccionados.
4. **Smoke E2E transaccional.** Usa sesiones individuales y la API ASGI local
   para recorrer preparación, asignación, revisión, preaprobación, liberación,
   simulación, recibo y verificación E4 sintética. No acepta UUIDs por CLI:
   deriva los tres operadores de los memberships exactos de la cohorte
   persistente `runtime-a94dcd3-v1` y vuelve a auditarla.
5. **Rollback y verificación independiente.** La transacción se revierte siempre.
   Una conexión nueva debe demostrar cero delta frente a los dos baselines:
   la cohorte persistente queda intacta y la fixture UNKNOWN y las tres
   sesiones efímeras no dejan residuo.
6. **Cierre.** Se apagan los flags temporales y se repiten los audits read-only.

Comandos previstos, que todavía no constituyen evidencia de ejecución:

```powershell
python -I -S -B scripts\rtm_connect_a1s_runtime_preflight.py --archive "C:\rtm\RTM_CONNECT_A1S_RUNTIME_BASE_a94dcd3.zip" --compact
python -I -B scripts\rtm_staging_connect_a1s_runtime_fixture.py --list-eligible --compact
python -I -B scripts\rtm_staging_connect_a1s_runtime_fixture.py --apply --confirmation STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY --requester-operator-id "<UUID_1>" --releaser-operator-id "<UUID_2>" --verifier-operator-id "<UUID_3>" --compact
python -I -B scripts\rtm_connect_a1s_runtime_smoke.py --compact
python -I -B scripts\rtm_staging_connect_a1s_runtime_fixture.py --requester-operator-id "<UUID_1>" --releaser-operator-id "<UUID_2>" --verifier-operator-id "<UUID_3>" --compact
```

## Escenario E2E mínimo pendiente

El smoke debe demostrar como mínimo:

1. feature gate cerrado por defecto y rechazo fail-closed de configuración
   insegura;
2. tres sesiones bearer efímeras de principales sintéticos distintos; el
   endpoint de login y las credenciales quedan fuera del alcance del smoke;
3. scope exacto de tenant, expediente, representación y documentos sintéticos;
4. `prepared → assigned → reviewing → ready_for_release → released → in_progress`;
5. resultado `submitted → awaiting_receipt → receipt_submitted → verified → completed`;
6. ramas `outcome_unknown` y reconciliación sin crear un segundo intento;
7. idempotencia exacta, optimistic locking y separación entre executor,
   releaser y verifier;
8. E3 sintética distinta de E4 sintética, sin confundir ninguna con recibo real;
9. ausencia de red saliente y de cualquier efecto externo;
10. rollback incondicional y cero delta frente al baseline, verificado desde
    otra conexión, con fixture persistente intacta y sin sesiones ni fixture
    UNKNOWN residuales.

Hasta que una salida real del smoke satisfaga todos esos puntos, los campos
`postgresql_runtime_audit_executed`, `transactional_e2e_executed`,
`rollback_verified` y `zero_delta_from_baseline_verified` permanecen en
`false`.

## Garantía de no efectos

La única red admitida por el sobre es la conexión a la base PostgreSQL staging
declarada. La aplicación no contacta proveedor, Administración, B2 ni otro
origen. El smoke debe instalar un guard de egress después de abrir PostgreSQL y
fallar si se intenta DNS, socket o conexión externa.

Todas las escrituras del smoke deben compartir una única conexión y una única
transacción externa. Un bloque `finally` debe restaurar el entorno, revertir la
transacción y cerrar la conexión incluso ante error. El resultado solo puede
ser `ok=true` si la comprobación posterior reproduce exactamente el baseline
de la cohorte persistente y demuestra que la fixture UNKNOWN transaccional y
las tres sesiones bearer efímeras no dejaron filas. Los tres operadores
preexistentes y la cohorte `runtime-a94dcd3-v1` no se borran ni se consideran
residuo.

El provisioning confirmado es una operación distinta: puede persistir una
cohorte sintética acotada para staging, pero debe declarar esa escritura de
forma explícita, no contener secretos, no mutar filas preexistentes y no
producir efectos fuera de la base.

## Límites no negociables

```text
synthetic_only=true
staging_only=true
real_data_used=false
provider_network_used=false
administration_network_used=false
provider_contacted=false
administration_contacted=false
b2_used=false
b2b_enabled=false
external_effects_executed=false
production_authorized=false
live_verdict=no_go
```

Un eventual `completed` de A1-S Runtime significará solamente que una
simulación con fixtures sintéticas terminó. No probará una presentación real,
una E4 auténtica de proveedor ni readiness de producción.

## Evidencia

La evidencia machine-readable está en
`docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json`. Mientras su estado sea
`pending_external_execution`, no debe usarse como aprobación para habilitar el
frontend, abrir las rutas de forma persistente o declarar finalizado el gate.
