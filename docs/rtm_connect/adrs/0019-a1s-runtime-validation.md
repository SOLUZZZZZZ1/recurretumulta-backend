# ADR 0019: validación Runtime del workflow A1-S

- Estado: propuesta; ejecución externa pendiente
- Fecha: 2026-08-25
- Base: `a94dcd314c67880e40aa333dc679ef98b80a1956`
- Ámbito: staging sintético
- Decisión de producción: `NO-GO`

## Contexto

ADR 0018 aceptó el contrato backend A1-S para staging sintético. Sus preflight
y smoke de entrega inspeccionan estructura, exports y fronteras de seguridad,
pero declaran honestamente `database_constraints_executed=false` y
`workflow_scenario_executed=false`.

El esquema A1-S dispone de un hotfix para ejecutar DDL PostgreSQL estático sin
que SQLAlchemy reinterprete literales o PL/pgSQL. Ese arreglo y los tests de
contrato no sustituyen una prueba Runtime contra la base staging desplegada.
Todavía falta acreditar provisioning sintético, sesiones individuales, el
workflow HTTP completo, las constraints reales y la restauración exacta del
baseline tras el rollback.

## Decisión

Se define un sobre separado de validación A1-S Runtime sobre el commit base
`a94dcd314c67880e40aa333dc679ef98b80a1956`, compuesto por:

- `rtm_connect/human_filing_runtime.py`;
- `scripts/rtm_connect_a1s_runtime_preflight.py`;
- `scripts/rtm_staging_connect_a1s_runtime_fixture.py`;
- `scripts/rtm_connect_a1s_runtime_smoke.py`;
- documentación, evidencia y tests específicos.

La decisión autoriza únicamente preparar y probar fixtures sintéticas en una
base PostgreSQL de staging aislada. No autoriza proveedor, Administración,
red externa, B2, B2B, datos reales, worker, reintento automático, canary ni
efecto jurídico.

## Gate de admisión

La fase permanecerá `blocked` hasta acreditar conjuntamente:

1. ZIP base exacto, comentario Git y SHA-256 congelados externamente;
2. overlay exacto, sin paths accidentales ni secretos, también congelado;
3. preflight offline ejecutado con éxito sin PostgreSQL ni red;
4. identidad de instancia, namespace, rama, base y rol staging comprobada;
5. audit read-only del schema y de todas las dependencias de fixtures;
6. provisioning creation-only ejecutado, si es necesario, con la confirmación
   `STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY`;
7. smoke HTTP E2E con tres sesiones individuales sintéticas y separación de
   funciones;
8. happy path, UNKNOWN/reconciliación, idempotencia y constraints ejercitados;
9. transacción revertida siempre y cero delta frente al baseline comprobado
   desde una conexión nueva: cohorte persistente intacta, fixture UNKNOWN y
   sesiones efímeras sin residuo;
10. entorno y feature flags restaurados y evidencia de salida congelada.

Ninguna comprobación aislada limpia el gate. En particular, un deploy LIVE o un
`/health` correcto no demuestran el workflow, y un `completed` sintético no
demuestra una presentación real.

## Provisioning

El provisioning Runtime es distinto del smoke. Debe ejecutarse con rutas y
autenticación individual apagadas, dentro de una transacción y contra
operadores sintéticos previamente provisionados. Solo puede insertar una
cohorte acotada: tenant, memberships, expediente `test_mode`, documentos,
binding, representación, acción y autorización congelada.

La cohorte persistente exacta se identifica por `runtime-a94dcd3-v1`. El
provisioning recibe tres UUID explícitos. Después, el smoke no los recibe por
CLI: deriva los operadores de los tres memberships deterministas de esa
cohorte y vuelve a comprobar roles, identidad y condición sintética.

La operación es idempotente por identidad y contenido. Las filas A1-S se
insertan sin actualizar preexistentes; la acción CORE nueva recorre dentro de
la misma transacción su transición obligatoria `draft → authorized`. Si una
fila existente no coincide exactamente, o una acción ya fue consumida, el
proceso bloquea y la transacción revierte. No se permite corregir una fila
preexistente mediante `UPDATE`, borrarla ni reutilizarla automáticamente.

## Smoke y rollback

El smoke utiliza una aplicación ASGI temporal y una única conexión PostgreSQL
compartida por autenticación y rutas A1-S. Todas sus escrituras quedan dentro de
una transacción externa. Las pruebas negativas que puedan forzar un rollback de
la conexión se aíslan mediante savepoints o transacciones separadas.

Después de abrir PostgreSQL se instala un guard que bloquea DNS y sockets. El
smoke nunca resuelve endpoints, credenciales de proveedor ni secretos B2. Un
bloque `finally` restaura las variables de entorno, revierte la transacción y
cierra la conexión. La salida solo es admisible cuando otra conexión confirma
el mismo snapshot que antes del smoke para la cohorte persistente y ausencia
de la fixture UNKNOWN y de las sesiones efímeras. No se ejecuta un endpoint de
login ni se crean credenciales: el smoke materializa tres sesiones bearer
dentro de la transacción y solo persiste sus hashes mientras esta permanece
abierta.

## Estado actual

A fecha 2026-08-25 esta ADR registra una decisión y un gate, no una ejecución.
La base sí quedó congelada como `RTM_CONNECT_A1S_RUNTIME_BASE_a94dcd3.zip`,
comentario `a94dcd314c67880e40aa333dc679ef98b80a1956` y SHA-256
`4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21`.
Todavía no existe en esta evidencia:

- commit o ZIP final del overlay;
- deploy Render observado para el overlay;
- audit/provisioning Runtime PostgreSQL ejecutado;
- smoke E2E transaccional ejecutado;
- prueba observada de rollback o cero delta frente al baseline.

La evidencia conserva por ello `status=pending_external_execution`,
`gate_status=blocked` y `live_verdict=no_go`.

## Consecuencias

El frontend solo podrá usar esta fase como contrato sintético después de que el
gate Runtime disponga de evidencia real. Incluso entonces, la autorización se
limitará al staging y a la ventana aprobada; el flag vuelve a apagarse al cerrar
el ejercicio.

Esta ADR no modifica la decisión `NO-GO` de G0/G1 ni las condiciones de una
fase real. Para datos reales seguirán siendo necesarios identidad fuerte,
aislamiento y protección documental, base legal, representación vigente,
proveedor/Administración verificados, E4 auténtica, reconciliación remota,
observabilidad, kill switch, retención y aprobaciones ligadas a hash.

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
