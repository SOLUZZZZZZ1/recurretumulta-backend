# RTM CONNECT C5 · Supervisor Panel

## Objetivo

C5 publica una proyección HTTP de supervisión sobre los ledgers ya creados en
C1, C3 y C4. Permite observar acciones, intentos, evidencia, transiciones,
tareas manuales, reconciliaciones y la DLQ de webhooks sin convertir el panel
en un runtime de ejecución.

La regla de autoridad de C0 permanece intacta:

**CORE autoriza; CONNECT ejecuta; la evidencia confirma; solo entonces CORE
puede cambiar el estado jurídico.**

C5 no crea, autoriza, encola, reintenta, reconcilia, verifica, completa,
confirma ni cancela actuaciones. Tampoco registra, activa, pausa o desactiva
conectores. Los estados y avisos visibles son observaciones técnicas, nunca
decisiones jurídicas ni permisos para ejecutar una transición.

## Activación y autorización

El panel está cerrado por defecto y toda su superficie exige una sesión bearer
individual válida. Para estar disponible deben cumplirse conjuntamente:

- `RTM_ENABLE_CONNECT_SUPERVISOR_V1=1`;
- autenticación individual de operadores activa;
- `RTM_ENV=staging`;
- `RTM_ENVIRONMENT_CONFIRMATION=RTM_STAGING_ISOLATED` e identidad de instancia
  inequívocamente staging;
- `RTM_DATA_NAMESPACE` inequívocamente identificado como staging;
- `DATABASE_URL` PostgreSQL con nombre staging, coincidente además con
  `current_database()` en la conexión abierta;
- frontend y allowlist CORS explícitos de staging, nunca `*`;
- rama desplegada coherente con `RTM_EXPECTED_BRANCH` y distinta de `main`;
- `RTM_DOCUMENT_INPUT_POLICY=synthetic_only`;
- `RTM_SIDE_EFFECT_POLICY=isolated`;
- `RTM_ALLOW_REAL_CUSTOMER_DATA=0`;
- B2, proveedor documental, presentación externa, correo saliente, Stripe y
  pagos finales desactivados;
- operador activo, sin cambio de contraseña pendiente;
- operador sintético de staging, no bloqueado y sin requisito MFA pendiente;
- rol vigente y activo con permiso `ops.supervise`;
- todos los conectores persistidos pertenecientes al scope C5 en staging,
  `synthetic_only=true` y sin `credential_ref`.
- toda acción vinculada a un expediente apunta a `cases.test_mode=true`; las
  acciones sin conector actual solo son visibles si conservan un intento con
  un conector sintético elegible.

Los únicos tuples de conector observables son `synthetic.echo/v1.0`,
`manual.handoff/v1.0` y `synthetic.webhook/v1.0`. Las acciones echo deben usar
capacidad/satélite `synthetic.echo/synthetic`; las manuales,
`administration.submit_document/administration`. Tareas, intentos, webhooks y
reconciliaciones deben conservar relaciones exactas con la misma acción,
intento y conector. Una contaminación bloquea el panel completo.

La autorización se vuelve a comprobar contra PostgreSQL en cada petición. Un
permiso conservado en una sesión antigua no permite acceder si el rol fue
desactivado o dejó de contener `ops.supervise`.

La capacidad falla cerrada:

- feature flag desactivada: `404`;
- configuración o scope sintético incompatibles: `503`;
- sesión ausente o inválida: `401`;
- permiso supervisor ausente o no vigente: `403`.

## Rutas publicadas

El prefijo exacto es `/ops/connect/supervisor`. Todas las rutas son protegidas,
responden con `Cache-Control: no-store` y solo admiten lectura:

| Método y ruta | Proyección |
| --- | --- |
| `GET /ops/connect/supervisor/status` | Versión, disponibilidad y límites de C5. |
| `GET /ops/connect/supervisor/overview` | Contadores por estado y riesgo, conectores, tareas manuales, webhooks, reconciliaciones y atención. |
| `GET /ops/connect/supervisor/attention` | Cola técnica unificada de acciones que requieren atención y webhooks en DLQ. |
| `GET /ops/connect/supervisor/actions` | Acciones sanitizadas, filtrables por estado, riesgo, capacidad y `case_id`. |
| `GET /ops/connect/supervisor/actions/{action_id}` | Detalle sanitizado y acotado de una acción y sus subhistoriales. |
| `GET /ops/connect/supervisor/manual-tasks` | Tareas manuales filtrables por estado, asignatario y vencimiento. |
| `GET /ops/connect/supervisor/webhook-dlq` | Sobres `dead_lettered` y su motivo normalizado. |

Las colecciones admiten `limit` entre 1 y 100 y `offset` acotado. El detalle de
acción admite `history_limit` entre 1 y 200 e informa `total`, `limit` y
`truncated` para cada subhistorial. No existen rutas `POST`, `PUT`, `PATCH` o
`DELETE` en C5.

El prefijo se oculta antes de resolver rutas, validaciones y CORS cuando el
flag o el entorno no son seguros; también se excluye de OpenAPI. Por ello
`OPTIONS`, métodos inválidos y UUID malformados no revelan la superficie cuando
C5 está cerrado. Toda respuesta bajo el prefijo, incluidos errores de
validación, hereda `no-store`.

## Estados y avisos visibles

El panel puede representar los estados congelados de C0:

```text
draft · authorized · queued · executing · external_accepted
evidence_pending · confirmed · retryable_failed · unknown · reconciling
manual_review · permanent_failed · cancelled
```

También muestra, cuando existen:

- intentos `started`, `external_accepted`, `succeeded`, `failed`, `unknown` o
  `cancelled`;
- tareas manuales `prepared`, `assigned`, `in_progress`,
  `awaiting_receipt`, `receipt_submitted`, `verified` o `completed`;
- webhooks `received`, `verified`, `matched`, `processed` o
  `dead_lettered`;
- reconciliaciones `started` o `resolved`, junto con la resolución normalizada;
- evidencia E0–E4 y transiciones append-only.

La cola de atención deriva motivos técnicos como `unknown`,
`reconciliation_open`, `manual_task_open`, `manual_task_overdue` o
`webhook_dead_lettered`. Su prioridad es una clasificación operativa del panel:
no modifica plazos, riesgo, estrategia, autorización ni el derecho a reintentar.
En particular, `unknown` nunca ofrece un reintento ciego y
`retryable_failed` no publica un botón de reejecución.

La prioridad se calcula sobre el conjunto completo antes de aplicar
`limit/offset`. Cada subhistorial conserva los N eventos más recientes y luego
los presenta en orden cronológico; `truncated=true` nunca significa que se
hayan conservado solo los eventos más antiguos.

## Redacción de datos

Las consultas seleccionan columnas explícitas y las respuestas no incluyen:

- `payload`, `target_ref` ni contenido o hashes documentales de la actuación;
- claves idempotentes ni material congelado completo de autorización;
- configuración o referencias de credenciales de conectores;
- `request_metadata`, `result_metadata` o metadatos JSON libres;
- manifiesto, instrucciones o contenido documental del paquete manual;
- justificantes, `receipt_storage_ref` o evidencia bruta;
- payload, prueba de integridad o referencia externa bruta de webhooks;
- `reason_detail` o contenido libre de historiales.
- códigos libres de fallo/verificación/resolución, identificadores reclamados
  por un webhook y arrays o versiones libres de la autorización.

Las referencias externas se reducen a indicadores de presencia cuando son
necesarias para la supervisión. Los identificadores internos, estados,
versiones, marcas temporales, códigos normalizados y relaciones exactas por UUID
sí pueden mostrarse. El detalle declara expresamente qué familias de material
han sido redactadas.

## Auditoría de lectura

Cada `GET` protegido que termina correctamente registra un evento
`connect.supervisor.*_viewed` en el historial append-only de accesos de
operadores. El identificador del evento no se devuelve al cliente. El evento
conserva actor, sesión,
fingerprint de petición, código de motivo y flags `supervisor_read` y
`connect_c5`. La evidencia técnica de acceso sigue la retención configurada por
la autenticación individual y nunca se devuelve desde el panel.

C5 registra el dispositivo real de la sesión y no confía en
`X-Forwarded-For` aportado por el cliente. La atribución de red fiable queda en
el ingress controlado.

Esta auditoría es la única escritura causada por una lectura C5. Ocurre en los
ledgers de acceso existentes; no altera acciones, autorizaciones, intentos,
evidencia, transiciones, tareas, webhooks ni reconciliaciones de RTM CONNECT.
La sesión se valida con `touch=false`, por lo que la consulta tampoco simula una
operación de negocio mediante un heartbeat implícito.

## Persistencia y efectos

C5 reutiliza las tablas y guards de C1, C3 y C4 y el ledger de acceso individual
ya existente. `connect_c5_supervisor_ddl()` devuelve una lista vacía:

- no hay DDL ni migración C5;
- no se crean tablas, índices, constraints o triggers;
- no se siembran conectores ni datos de demostración persistentes;
- no se publican endpoints de webhook o ejecución;
- no hay llamadas de red salientes;
- no se usa B2, correo, Stripe, pagos ni presentación externa;
- no se cambia ningún estado legal ni operativo de CONNECT.

El `runtime_published=false` del manifiesto C0 continúa describiendo el runtime
de ejecución. C5 publica únicamente una proyección supervisora GET y no obliga a
reescribir el manifiesto congelado.

## Límites operativos deliberados

`cases.test_mode` es una marca mutable, no una prueba criptográfica de origen.
C5 la combina con una base, instancia, namespace, rama, frontend y política de
entrada aislados, además de scopes relacionales exactos. Antes de producción se
deberá añadir procedencia sintética inmutable y/o RLS.

En C5 la ausencia de mutaciones CONNECT se garantiza en aplicación, consultas
`SELECT` allowlisted, smoke byte-a-byte y guards existentes. El rol compartido
del monolito todavía puede poseer permisos más amplios; una fase posterior debe
separar un rol de proyección con `SELECT` limitado e `INSERT` únicamente en el
ledger de auditoría. El ingress debe aplicar rate limiting y monitorización al
prefijo aun siendo GET-only.

## Criterio de cierre

C5 se considera cerrado cuando se verifica todo lo siguiente:

1. preflight C5 de solo lectura con `safe=true`, manifiesto C0 íntegro, scope
   sintético limpio y un supervisor activo;
2. auditoría de esquema con `ready=true`, cero sentencias DDL, cero migraciones
   C5 registradas y snapshot antes/después idéntico;
3. las siete rutas exactas están cableadas y no existe ningún método mutador ni
   import de los ejecutores C2–C4 en el router;
4. flag, entorno, autenticación, permiso vigente y scope de conectores fallan
   y los expedientes no sintéticos fallan cerrados con los códigos HTTP
   definidos;
5. smoke HTTP transaccional demuestra acceso supervisor, rechazo de operador,
   filtros y paginación acotados, orden estable y `404` para UUID inexistente;
6. el smoke comprueba recursivamente la ausencia de todos los campos redactados
   y las cabeceras `no-store`;
7. cada lectura satisfactoria deja exactamente su evento de auditoría y ninguna
   tabla `rtm_connect_*` cambia en conteo, estado o versión;
8. rollback completo y cero residuo sintético desde una conexión nueva;
9. suites C0–C4 y de autenticación/supervisión sin regresiones, `/health`
   correcto y restore remoto verificado;
10. cero llamadas de red salientes y cero efectos externos reales.
