# RTM CONNECT A1S — presentación humana sintética en staging

## Veredicto

**A1S queda limitado a `staging`, fixtures sintéticas y trabajo humano interno.**
No habilita una integración con proveedor ni con una Administración, no hace
presentaciones reales y no autoriza datos reales. El veredicto para producción
y para cualquier efecto jurídico continúa siendo **`NO-GO`**.

A1S permite validar el recorrido que después necesitará el frontend: un
operador autenticado prepara y puede ejecutar un paquete, un releaser y un
verificador independientes lo preaprueban, y el mismo verificador registra la
E4 sintética. La aplicación no abre
la sede, no introduce credenciales y no pulsa el botón final por el operador.

## Identidad congelada de la base

| Elemento | Valor exacto |
|---|---|
| Commit base | `b0bc7ddfad9278e601dce8dd69083472662874b5` |
| ZIP base | `RTM_CONNECT_G2_BASE_b0bc7dd.zip` |
| SHA-256 | `4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e` |
| Entradas / ficheros | `542 / 523` |

El preflight inspecciona el ZIP sin extraerlo, verifica comentario Git, SHA-256,
CRC, tamaños, nombres seguros y ausencia del overlay A1S en la base. El hash y
el comentario prueban identidad de entrega, no autoría ni firma de supply chain.

El overlay incluye también `app.py` y tres assertions sucesoras que preservan
las fronteras anteriores mientras admiten exclusivamente A1S:
`test_rtm_connect_c7_scripts_contract.py`,
`test_rtm_connect_c8_scripts_contract.py` y
`test_rtm_connect_post_c8_g1_docs_contract.py`. Esos paths existen en la base,
pero el preflight verifica que el contenido sucesor local no estaba todavía en
el ZIP congelado; las versiones base y sus hashes siguen siendo inmutables.

## Frontera ejecutable A1S

La frontera se cierra por defecto. Para una prueba sintética controlada deben
cumplirse simultáneamente:

- `RTM_ENV=staging` y la identidad exhaustiva de instancia, namespace, rama,
  base de datos, rol y política de efectos de la frontera staging existente;
- `RTM_ALLOW_REAL_CUSTOMER_DATA=0`;
- `RTM_ENABLE_CONNECT_A1S_HUMAN_FILING=1` solo durante la ventana aprobada;
- `RTM_CONNECT_A1S_NETWORK_ALLOWED=0`;
- `RTM_CONNECT_A1S_B2_ALLOWED=0`;
- `RTM_CONNECT_A1S_PROVIDER_ALLOWED=0`;
- `RTM_CONNECT_A1S_REAL_DATA_ALLOWED=0`;
- `RTM_CONNECT_A1S_EXTERNAL_EFFECTS_ALLOWED=0`.

Un valor ausente, ambiguo o distinto del esperado bloquea la operación. No hay
fallback a `OPERATOR_TOKEN`, PIN compartido, cabecera de actor elegida por el
cliente ni sesión anónima. La identidad del actor se deriva exclusivamente de
una sesión bearer individual validada por el backend. El middleware
`human_filing_gate_middleware` y las dependencias de cada ruta aplican la misma
política fail-closed; la documentación OpenAPI permanece oculta.

## Autorización, tenant y expediente

Cada mutación operativa normal exige todas estas relaciones persistidas y
activas; las lecturas históricas conservan visibles las tareas ya cerradas:

1. principal autenticado y operador activo;
2. membership activa del operador en el tenant solicitado;
3. binding explícito e inmutable entre tenant y expediente sintético;
4. permiso de menor privilegio para la transición concreta;
5. estado y versión esperados para control optimista;
6. idempotency key vinculada al tenant, expediente, operación y hash canónico.

La única excepción es el cierre terminal `/manual-reviews`: conserva y verifica
la identidad sintética y el paquete congelado, pero permite inmovilizar la tarea
si binding, representación, autoridad o participantes previos han cerrado.
Siempre exige un supervisor activo del mismo tenant y sincroniza action/attempt
CORE a `manual_review` en la misma transacción.

No se confía en `tenant_id`, `case_id`, `operator_id`, roles o permisos aportados
por el cuerpo HTTP. Un UUID válido sin ownership no concede acceso. Las lecturas,
las mutaciones, los artefactos, los eventos y las claves de idempotencia quedan
siempre acotados por tenant y expediente. Las respuestas que cruzarían ese
límite son indistinguibles de un recurso inexistente.

## Separación humana de funciones

El solicitante prepara el paquete y puede ser también el ejecutor. Antes de
liberarlo se requieren **dos aprobaciones individuales**: una del `releaser` y
otra preaprobación del futuro `verifier`. Releaser, verifier y ejecutor deben ser
tres identidades distintas. El mismo verifier que preaprueba es quien después
verifica la E4 sintética. El ejecutor solo puede iniciar la simulación final
cuando la tarea ya está liberada.

La preparación crea una tarea `prepared` sin ejecutor. La asignación es un
comando posterior y explícito (`POST /{task_id}/assignments`) protegido por
`connect.human_filing.assign`, `Idempotency-Key` e `If-Match`; el candidato debe
tener membership activa y permiso de ejecución en el mismo tenant. Así, preparar
un expediente no equivale a autoasignárselo. El solicitante puede ser el ejecutor
solo si una asignación autorizada lo fija expresamente.

Las aprobaciones se registran en `rtm_connect_a1s_approvals` como hechos
append-only vinculados al hash exacto del paquete y al principal autenticado.
Cambiar un documento, autoridad, representación, destino, versión o hash
invalida las aprobaciones previas. El ejecutor no puede ocupar ninguno de los
dos puestos de aprobación y los aprobadores no pueden coincidir entre sí. No
existe delegación implícita ni aprobación por token técnico.

## Máquina de estados y no doble envío

La progresión permitida es explícita y versionada:

`prepared → assigned → reviewing → ready_for_release → released → in_progress`

Después del trabajo humano solo cabe registrar un resultado sintético:

- `awaiting_receipt → receipt_submitted → verified → completed`;
- `outcome_unknown → reconciling → ...`, sin reenvío automático;
- `manual_review` o `permanent_failed` cuando la evidencia no sea admisible.

Un supervisor activo del tenant puede cerrar a `manual_review`, mediante un
comando idempotente y un `reason_code` cerrado, los estados operativos que ya
admiten esa transición. Este cierre no depende de que la autoridad o la
representación sigan vigentes: precisamente sirve para inmovilizar la tarea si
una de ellas caduca o se revoca. No reabre ni reenvía la acción.

`outcome_unknown` nunca vuelve a abrir `released` ni crea un segundo intento.
Primero se reconcilia por consulta humana de solo lectura usando el mismo hash e
idempotency key. `prepare` usa la transición C1 `QUEUED` de forma efímera para
abrir el attempt sintético, pero A1S no incluye dispatcher/worker, cola de
despacho automática, retry automático ni transporte.

## Contrato HTTP para el frontend

El prefijo es `/ops/connect/human-filings`. Todas las rutas exigen
`Authorization: Bearer <sesión individual>`, quedan fuera de OpenAPI y
responden con `Cache-Control: no-store`. Con el feature gate cerrado el prefijo
se comporta como inexistente. El `tenant_id` nunca concede acceso por sí solo:
la sesión debe tener membership activa en ese tenant.

| Método y ruta relativa | Uso y resultado |
|---|---|
| `GET /tenants` | Bootstrap de las memberships A1S activas de la propia sesión; nunca enumera tenants ajenos. |
| `GET /context?tenant_id=...` | Membership del actor y participantes activos del mismo tenant, sin correos ni secretos. |
| `GET /preparation-options?tenant_id=...` | Bindings, representación y autoridad sintética ya aprovisionados y revalidados; no reclama ni modifica nada. |
| `GET /?tenant_id=...` | Cola paginada, filtrable por estado, ejecutor y vencimiento. |
| `GET /{task_id}?tenant_id=...` | Detalle seguro: manifiesto, approvals, metadatos de artefactos, timeline y ayudas `allowed_actions`. |
| `GET /{task_id}/receipt-options?tenant_id=...` | UUID y SHA-256 de fixtures de recibo elegibles del mismo expediente; sin contenido ni referencias B2. |
| `POST /` | Congela paquete y crea la tarea `prepared`. |
| `POST /{task_id}/assignments` | Supervisor asigna un executor y produce `assigned`. |
| `POST /{task_id}/reviews/start` | El executor inicia `reviewing`. |
| `POST /{task_id}/reviews/attest` | El executor atesta el hash y produce `ready_for_release`. |
| `POST /{task_id}/verification-preapprovals` | El verifier independiente preaprueba el hash exacto. |
| `POST /{task_id}/releases` | El releaser independiente libera la tarea. |
| `POST /{task_id}/executions/start` | Solo el executor fijado inicia la simulación humana. |
| `POST /{task_id}/outcomes` | Declara `submitted` con referencia sintética o `unknown` sin referencia aportada; nunca reenvía. |
| `POST /{task_id}/receipts` | Liga una fixture JSON de recibo sintético del mismo expediente. |
| `POST /{task_id}/verifications` | El verifier preaprobado valida E4 sintética y cierra. |
| `POST /{task_id}/reconciliations/start` | Abre consulta humana de solo lectura desde `outcome_unknown`. |
| `POST /{task_id}/reconciliations/resolve` | Mantiene UNKNOWN, escala o falla; un recibo hallado sigue por E4 sin nuevo intento. |
| `POST /{task_id}/manual-reviews` | Un supervisor inmoviliza una incidencia con motivo allowlisted aunque autoridad/representación hayan cerrado; sincroniza CORE y no produce efectos. |

`POST /` exige `Idempotency-Key`. Toda mutación de una tarea exige además
`If-Match: W/"<task_id>:<status_version>"`; la respuesta devuelve el siguiente
`ETag`. La clave tiene formato `rtma1s:<sha256>` y queda ligada a tenant, actor,
operación y material canónico. Un replay idéntico es seguro y un mismo valor con
otro material devuelve conflicto.

Las respuestas de éxito y los errores de dominio emitidos después de construir
el contexto A1S usan `{"ok":true,"request_id":"...",...}` o
`{"ok":false,"request_id":"...","error":{"code":"...",` seguido de
`"message":"...","retryable":false}}`. Los rechazos previos de FastAPI
(por ejemplo UUID/body inválido o una dependencia 401/503) conservan por ahora
su envelope `detail`; el frontend debe normalizar ambos formatos. Esta
limitación está congelada y no debe interpretarse como un error retryable.
`allowed_actions` es una ayuda de UI:
`allowed_actions_authoritative=false` y `commands_revalidate=true` obligan al
frontend a tratar siempre la respuesta del comando como autoridad final.

Las frases sintéticas exactas son `A1S_HUMAN_REVIEW_CONFIRMED`,
`A1S_VERIFIER_PREAPPROVED`, `A1S_RELEASE_APPROVED` y
`A1S_SYNTHETIC_RECEIPT_VERIFIED`. No son secretos ni sustituyen una firma; solo
evitan confirmaciones ambiguas en este ensayo.

## Evidencia y artefactos sintéticos

El expediente requiere evidencia de representación sintética tipada:
`synthetic_power_of_attorney`, `synthetic_signed_authorization` o
`synthetic_legal_representative_attestation`. El paquete se canonicaliza y se
congela antes de aprobarlo. Cada artefacto conserva SHA-256, tipo,
clasificación sintética, actor y timestamps; los eventos y aprobaciones son
append-only.

Los tipos admitidos son `authority_snapshot`, `representation_evidence`,
`filing_package`, `human_review_attestation`, `release_attestation`,
`verification_preapproval_attestation`,
`synthetic_submission_report`, `synthetic_receipt`,
`verification_attestation` y `reconciliation_attestation`.

Un informe de entrega generado por la fixture es E3 sintética; solo una fixture
de recibo validada contra el contrato congelado puede representar E4 sintética.
Ninguna de ellas es un recibo real, acredita una presentación real ni puede
reutilizarse como prueba ante una Administración.

El recibo es una **salida posterior**, no uno de los documentos de entrada del
paquete. Su SHA-256 debe ser distinto de todos los hashes congelados de la
acción. El backend lista las fixtures elegibles en `receipt-options` y, después
del handoff, el detalle publica un `receipt_summary` acotado con
`document_sha256`, referencia y hash de paquete para que el verifier independiente
pueda completar E4 sin recibir el payload interno del artefacto.

Los `witnessed_at` son declaraciones sintéticas con zona horaria, no timestamps
de confianza emitidos por un tercero. A1S no acredita su orden contra un reloj
de proveedor; esa garantía pertenece al futuro contrato real y su E4 auténtica.

## Persistencia permitida

La migración es aditiva e idempotente y crea exclusivamente:

- `rtm_connect_a1s_tenants`;
- `rtm_connect_a1s_memberships`;
- `rtm_connect_a1s_case_bindings`;
- `rtm_connect_a1s_representation_evidence`;
- `rtm_connect_a1s_human_tasks`;
- `rtm_connect_a1s_approvals`;
- `rtm_connect_a1s_artifacts`;
- `rtm_connect_a1s_events`;
- `rtm_connect_a1s_idempotency`.

La migración no publica rutas, no crea operadores, tenants o expedientes, no
siembra conectores y no modifica tablas C0–C8. La aplicación exige la frase
exacta `STAGING_CONNECT_A1S_SCHEMA_ONLY`. El ledger de migraciones no se
reescribe si ya existe una entrada con la misma versión.

El runtime sí coordina el kernel CONNECT sintético ya existente: al preparar
registra/reutiliza el conector sintético, encola la acción y crea un intento C1;
los resultados, reconciliación y E3/E4 actualizan las tablas CORE de acción,
attempt y evidence dentro de la misma transacción. Son mutaciones locales de
staging, no efectos ante proveedor o Administración. En A1S el intento C1 queda
`EXECUTING` desde `prepare`, antes de la asignación y las aprobaciones humanas;
esta es una limitación semántica/observacional del sobre sintético y debe
corregirse antes de cualquier fase real.

Por tanto, desplegar el código y aplicar el DDL no hace utilizable el flujo por
sí solos. Una preparación de staging separada, revisada y auditable deberá crear
las identidades y fixtures sintéticas mínimas y sus memberships/bindings. A1S
no incorpora un endpoint de autoalta ni acepta que el frontend invente esas
relaciones.

El repositorio de aplicación vuelve a calcular el SHA-256 canónico de paquetes
y artefactos antes de insertarlos. PostgreSQL protege scope, campos, estados e
inmutabilidad, pero no recalcula de forma independiente la canonicalización
Python. Esa defensa adicional queda pendiente de una fase posterior.

## Prohibiciones no negociables

A1S no puede:

- contactar proveedor, Administración, sede, registro, webhook o endpoint;
- importar o invocar transports HTTP, sockets, DGT o Registro General;
- acceder a B2, emitir presigned URLs o habilitar B2B;
- resolver secretos o aceptar credenciales de Administración;
- usar datos de cliente, expediente o documentación real;
- ejecutar efectos jurídicos, producción, canary, worker o retry;
- confundir paquete preparado con presentación, ni E3 con E4;
- sobrescribir eventos, aprobaciones o evidencias históricas.

## Secuencia operativa

```text
sesión individual
  → membership tenant
  → ownership expediente sintético
  → evidencia de representación sintética
  → paquete canónico congelado
  → asignación explícita a membership ejecutora
  → releaser aprueba + verifier preaprueba
  → liberación a ejecutor independiente
  → simulación humana fuera de RTM
  → declaración/recibo sintético
  → el verifier preaprobado verifica E4 sintética
  → cierre o reconciliación sin reenvío
```

Comandos de auditoría:

```powershell
python -I -S -B scripts\rtm_connect_a1s_preflight.py --archive "C:\rtm\RTM_CONNECT_G2_BASE_b0bc7dd.zip" --compact
python -I -S -B scripts\rtm_connect_a1s_smoke.py --archive "C:\rtm\RTM_CONNECT_G2_BASE_b0bc7dd.zip" --compact
python -I -B scripts\rtm_staging_connect_a1s_schema.py --compact
python -I -B scripts\rtm_staging_connect_a1s_schema.py --apply --confirmation STAGING_CONNECT_A1S_SCHEMA_ONLY --compact
python -B -m unittest discover -s tests -p "test_rtm_connect_a1s_*.py"
```

El preflight y el smoke anteriores son auditorías estáticas offline: no abren
PostgreSQL y declaran expresamente `database_constraints_executed=false` y
`workflow_scenario_executed=false`. El comando de esquema sí carga la
configuración de la base y abre la conexión staging autorizada. La ejecución
transaccional completa en PostgreSQL debe verificarse en esa base aislada antes
de habilitar el feature flag.

## Lo que falta para datos reales

A1S no es una antesala automática de producción. Un gate posterior y separado
debe acreditar como mínimo MFA/AAL2 real; sesiones revocables y auditables;
cierre de PIN/token compartido y de rutas legacy; aislamiento tenant probado en
base y API; almacenamiento cifrado por tenant con malware scan, checksum,
retención, borrado y trazabilidad; base legal y minimización; representación
vigente; proveedor/Administración identificados; origen, protocolo y egress
allowlist; custodia de secretos; idempotencia remota; consulta y reconciliación;
recibo E4 auténtico; rollback/compensación; observabilidad; kill switch; SLO;
aprobaciones ligadas a hash; y canary autorizado sin autoexpansión.

Hasta que ese dossier exista y sea aprobado, se mantienen: `real_data_used=false`,
`provider_contacted=false`, `administration_contacted=false`,
`provider_network_used=false`, `administration_network_used=false`,
`b2_used=false`, `b2b_enabled=false`, `external_effects_executed=false` y
`production_authorized=false`.

`network_used=false` dentro de paquetes/eventos A1S significa ausencia de red
operativa hacia terceros; no niega la conexión PostgreSQL staging que utiliza
el comando de esquema o el runtime.
