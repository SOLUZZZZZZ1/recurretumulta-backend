# RTM CONNECT A1-S Runtime — cierre de validación sintética en staging

## Estado y veredicto

**Ejecución Runtime: `completed_synthetic_staging`.**

**Gate de entrega: `passed_synthetic_staging`.**

**Gate de producción: `blocked`; veredicto live: `NO-GO`.**

El workflow A1-S completó el 25 de agosto de 2026 un smoke HTTP transaccional
con PostgreSQL y fixtures exclusivamente sintéticas en Render sobre el commit
final `9e0a26777f19efeb2c54b093e771570493a3de0e`. La salida observada declaró
`ok=true`, `safe=true` y `blockers=[]`, completó el happy path y la rama
UNKNOWN/manual-review, y revirtió la transacción.

Esta ejecución cierra el trabajo Runtime sintético. El preflight offline
ejecutado anteriormente tenía
como sujeto la base `a94dcd314c67880e40aa333dc679ef98b80a1956` y es anterior a
tres hotfixes Runtime. Por ello no aplica al sujeto final. El gate de entrega
se declara `passed_synthetic_staging` bajo una condición procedimental
obligatoria: este overlay de cierre solo puede entregarse o commitirse después
de que el nuevo preflight de evidencia audite con éxito el ZIP final exacto.
Si el preflight bloquea, el overlay no es admisible y no debe commitirse.

Nada de lo anterior autoriza datos reales, presentación jurídica, proveedor,
Administración, B2, rutas públicas persistentes, frontend, E4 auténtica ni
producción. La salida del smoke fue copiada desde consola por el operador: no
está firmada y no se dispone de su fichero bruto con SHA-256 congelado.

## Identidad de diseño y sujeto final

| Elemento | Identidad o estado |
|---|---|
| Base de diseño Runtime | `a94dcd314c67880e40aa333dc679ef98b80a1956` |
| ZIP base de diseño | `RTM_CONNECT_A1S_RUNTIME_BASE_a94dcd3.zip` |
| SHA-256 base de diseño | `4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21` |
| Commit final ejecutado | `9e0a26777f19efeb2c54b093e771570493a3de0e` |
| ZIP final de evidencia | `RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip` |
| SHA-256 ZIP final | `038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e` |
| Comentario Git del ZIP final | `9e0a26777f19efeb2c54b093e771570493a3de0e` |
| Preflight final de entrega | obligatorio y satisfactorio antes del commit de este cierre |
| Firma del ZIP o del commit | no verificada |
| Provenance de supply chain | no verificada |

El hash y el comentario de un `git archive` fijan una identidad de entrega,
pero por sí solos no prueban autoría del commit, firma ni provenance.

## Cadena exacta de commits y entregas

La evolución observada desde G1 hasta el sujeto final es:

| Commit | Contenido |
|---|---|
| `b0bc7ddfad9278e601dce8dd69083472662874b5` | gate G1 de admisión de proveedor post-C8 |
| `37a4479022519d34d1a220cb1ac6380ea7b9f238` | workflow A1-S de presentación humana sintética |
| `a94dcd314c67880e40aa333dc679ef98b80a1956` | hotfix de ejecución del schema PostgreSQL |
| `aaf040b1c8d35ee61aa3720a4dc4b8cf1822b827` | validación Runtime A1-S sintética |
| `d546b1368eeacf34bb50dea5820ab1ed27f93053` | hotfix de consultas JSONB Runtime |
| `407ced9acbeffdd6c727264e8c9ac26e3cd110fa` | hotfix de reloj transaccional Runtime |
| `9e0a26777f19efeb2c54b093e771570493a3de0e` | hotfix de canonicalización del evento de release |

Las entregas congeladas que componen esa cadena son:

| Archivo | SHA-256 |
|---|---|
| `RTM_CONNECT_G2_BASE_b0bc7dd.zip` | `4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e` |
| `RTM_CONNECT_A1S_HUMAN_FILING_OVERLAY_b0bc7dd.zip` | `8ade7dbef0559e4e6bd739946ea97799a9dfeb1e1f3cf4ba3b77b937340466ac` |
| `RTM_CONNECT_A1S_SCHEMA_HOTFIX_37a4479.zip` | `f9014d3faff19a73328a088798b5988aaef5ff7231e32eee8ef1268959b2bfb4` |
| `RTM_CONNECT_A1S_RUNTIME_BASE_a94dcd3.zip` | `4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21` |
| `RTM_CONNECT_A1S_RUNTIME_OVERLAY_a94dcd3.zip` | `0f44d10543c777fd1ef36b20357934cafd4605c8a296d469af3b1af6b56c0e24` |
| `RTM_CONNECT_A1S_RUNTIME_PREFLIGHT_HOTFIX_a94dcd3.zip` | `c3b26ed8c85393f7c45d0957805a00a4394d295b3705bb8259e6e0a90f0ceb2f` |
| `RTM_CONNECT_A1S_RUNTIME_SQL_HOTFIX_aaf040b.zip` | `e1c497a3e65aa8462f8f50050f11275ab30337b6d7203d6d76c16c4c3cbd3ebb` |
| `RTM_CONNECT_A1S_RUNTIME_CLOCK_HOTFIX_d546b13.zip` | `4756378882a385e409ec3b8c8617c252039e2131e10ca7afdda97d535afb13fb` |
| `RTM_CONNECT_A1S_RUNTIME_EVENT_HOTFIX_407ced9.zip` | `6bad4f4e5f7fcd5a39b30c14e934aa67a001c97a2d43fe68a6a969020170ddea` |
| `RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip` | `038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e` |

## Alcance original del sobre Runtime

El overlay Runtime quedó limitado a estos doce paths exactos:

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

El módulo y los scripts no incorporan transport a proveedor o Administración,
worker, webhook, B2 ni envío jurídico real.

## Sobre exacto de cierre

El cierre documental y su preflight final quedan restringidos a seis paths:

- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md`;
- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json`;
- `docs/rtm_connect/adrs/0019-a1s-runtime-validation.md`;
- `tests/test_rtm_connect_a1s_runtime_docs_contract.py`;
- `scripts/rtm_connect_a1s_runtime_evidence_preflight.py`;
- `tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py`.

Este sobre no modifica el Runtime ni amplía capacidades. El nuevo preflight
debe ser stdlib-only, read-only y offline: no extrae el ZIP, no abre
PostgreSQL, no usa red, no resuelve secretos y no importa el Runtime.

## Frontera que rigió la ejecución

La validación se ejecutó bajo una frontera fail-closed de staging sintético:

- `RTM_ENV=staging` y confirmación `RTM_STAGING_ISOLATED`;
- rama esperada `rtm-core-consolidation-2026-08-08` y commit esperado exacto;
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
- `RTM_ENABLE_CONNECT_A1S_HUMAN_FILING=0` al inicio;
- `RTM_ENABLE_OPERATOR_AUTH_V1=0` al inicio.

El smoke habilitó sus dos flags Runtime únicamente dentro del proceso y los
restauró al terminar. La evidencia no acredita un endpoint de login ni una
ruta A1-S pública o persistentemente habilitada.

## Cohorte sintética persistente

La fixture `runtime-a94dcd3-v1` quedó auditada como sintética,
creation-only e insert-only para las filas A1-S. Se ligó a tres operadores
sintéticos distintos:

- requester: `0a558d35-01b0-4c74-8640-c690ec21d52c`;
- releaser: `cd2d8df3-9e67-4c86-8f4e-1df55fb67b44`;
- verifier: `9cae8979-f25f-4350-b3e7-1c8c7bd9c62b`.

El audit posterior reportó `ready=true`, `preexisting_rows_mutated=false`,
`database_mutated=false` en modo de auditoría y separación exacta de las tres
memberships. El smoke derivó sus operadores de esa cohorte y no los recibió
por CLI.

## Secuencia de admisión: resultado observado

1. **Preflight offline de diseño.** El preflight v1.1 auditó correctamente la
   base `a94dcd3`, pero queda marcado como histórico y no aplicable al commit
   final porque después entraron los hotfixes JSONB, clock y event.
2. **Audit de schema y fixtures.** PostgreSQL staging informó schema ready y
   la cohorte persistente exacta fue re-auditada read-only.
3. **Provisioning confirmado.** La fixture fue creada/auditada con la
   confirmación `STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY`, tres operadores
   sintéticos distintos y política creation-only.
4. **Smoke E2E transaccional.** La aplicación ASGI in-process completó happy
   path y UNKNOWN/manual-review con sesiones bearer individuales y sin salida
   de red.
5. **Rollback y verificación independiente.** La salida declaró rollback,
   conexión fresca, snapshot de conteos posterior igual al baseline y cero
   sesiones efímeras remanentes.
6. **Cierre de entrega.** El nuevo preflight debe ejecutarse sobre el ZIP final
   `9e0a267` y terminar satisfactoriamente antes del commit. El estado
   `passed_synthetic_staging` no autoriza entregar un overlay que no haya
   superado esa comprobación exacta.

El comando de cierre previsto es:

```powershell
python -I -S -B scripts\rtm_connect_a1s_runtime_evidence_preflight.py --archive "C:\rtm\RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip" --compact
```

## Resultado del smoke final

La consola Render observada para el commit final declaró:

```text
authority=rtm_connect_a1s_runtime_smoke
version=rtm_connect_a1s_runtime_smoke_v1_0
ok=true
safe=true
blockers=[]
http_in_process_asgi=true
database_rolled_back=true
fixture_baseline_restored=true
synthetic_only=true
legal_submission_executed=false
routes_published=false
workers_started=false
production_authorized=false
production_safe=false
live_verdict=no_go
```

Sus 28 checks reportados como verdaderos fueron:

- `completed_visible_through_read_api`;
- `e4_exactly_bound_to_preapproved_verifier`;
- `feature_closes_again_without_restart`;
- `feature_default_off_returns_404`;
- `fresh_connection_observes_baseline_restored_and_ephemeral_zero_residue`;
- `full_http_state_machine_completed`;
- `full_http_unknown_reconciliation_branch`;
- `individual_bearer_session_required`;
- `package_and_receipt_hashes_disjoint`;
- `persistent_fixture_read_only_audited`;
- `postgresql_final_state_completed`;
- `postgresql_schema_ready`;
- `prepare_idempotency_replayed`;
- `sessions_store_only_sha256`;
- `single_hash_only_receipt_fixture`;
- `single_preparation_candidate`;
- `temporary_runtime_flags_restored`;
- `tenant_bootstrap_scoped`;
- `three_distinct_tenant_participants`;
- `transaction_clock_coherent`;
- `transaction_contains_complete_fixture_graph`;
- `two_preoperation_principals_distinct`;
- `unknown_branch_closes_manual_review`;
- `unknown_branch_never_blind_retries`;
- `unknown_fixture_transactionally_provisioned`;
- `unknown_manual_review_visible_through_read_api`;
- `unknown_preoperation_principals_distinct`;
- `zero_external_socket_attempts`.

El cleanup reportó `database_rolled_back=true`,
`ephemeral_sessions_remaining=0` y
`fixture_snapshots_equal_to_baselines=true`. Los IDs de tarea que aparecieron
en la salida fueron efímeros:
`5b6ff858-f3dc-4c4e-8d73-973dc640cd60` para completed y
`6581b755-dba5-419d-8b26-5788039ddde4` para UNKNOWN/manual-review.

## Límite exacto del rollback observado

La afirmación admisible es que la transacción fue revertida, que una conexión
nueva observó el snapshot de conteos posterior igual al baseline registrado y
que quedaron cero sesiones efímeras. No se calculó un hash de contenido de
cada fila persistente, por lo que no se acredita un zero-delta de contenido ni
que la fixture persistente fuese byte-for-byte idéntica.

Para la fixture UNKNOWN, `after == baseline` sí fue observado, pero no se
demostró de forma independiente que ese baseline fuese cero absoluto. Por
tanto, este cierre no formula la afirmación “residuo UNKNOWN absoluto cero”.
Esta precisión no invalida el smoke sintético; delimita lo que su evidencia
puede probar.

## Tests y despliegue observados

En el commit final se informaron estas suites locales:

- 114 tests A1-S: OK;
- 643 tests RTM CONNECT: OK;
- 1227 tests globales: OK, 8 skipped.

Render marcó LIVE los siguientes despliegues de la cadena Runtime:

| Commit | Deploy |
|---|---|
| `aaf040b1c8d35ee61aa3720a4dc4b8cf1822b827` | `dep-da6p0i0ae00c738kqc7g` |
| `d546b1368eeacf34bb50dea5820ab1ed27f93053` | `dep-da6pka15efls73d390sg` |
| `407ced9acbeffdd6c727264e8c9ac26e3cd110fa` | `dep-da6q23h5efls73d3pvj0` |
| `9e0a26777f19efeb2c54b093e771570493a3de0e` | `dep-da6qp1p5efls73d4q0kg` |

Tras el último deploy se observó `/health` con `{"ok":true}`. Un deploy LIVE
y un health correcto no sustituyen el smoke ni acreditan disponibilidad para
producción.

## Garantía de no efectos y límites no negociables

La única red usada por el ejercicio fue la conexión controlada a PostgreSQL
staging. El guard registró cero intentos de socket externo. No se contactó
proveedor ni Administración, no se usó B2, no hubo datos reales ni se ejecutó
una presentación con efecto jurídico. La E4 ejercitada fue sintética y no es
un recibo auténtico de tercero.

```text
gate_status=passed_synthetic_staging
production_gate_status=blocked
operator_console_observed_unattested=true
content-level zero delta=false
E4 autentica=false
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

Un `completed_synthetic_staging` significa exclusivamente que el backend A1-S
recorrió en staging el workflow sintético previsto bajo rollback. No significa
`frontend_ready`, no prueba una presentación real, no acredita E4 auténtica y
no habilita producción.

## Evidencia y siguiente paso

La evidencia machine-readable se encuentra en
`docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json`. Su salida de consola
se clasifica como `operator_console_observed_unattested`: no se afirma firma,
provenance ni hash del reporte bruto.

La admisión `passed_synthetic_staging` queda condicionada a ejecutar con éxito
el preflight final de entrega sobre el ZIP exacto `9e0a267` antes de hacer el
commit de este overlay. El gate de producción y el veredicto live permanecen
sin cambios:
`production_gate_status=blocked` y `live_verdict=no_go`.
