# ADR 0019: validación Runtime del workflow A1-S

- Estado de ejecución: `completed_synthetic_staging`
- Estado del gate de entrega: `passed_synthetic_staging`
- Fecha: 2026-08-25
- Base de diseño: `a94dcd314c67880e40aa333dc679ef98b80a1956`
- Sujeto final ejecutado: `9e0a26777f19efeb2c54b093e771570493a3de0e`
- Ámbito: staging sintético
- Gate de producción: `blocked`
- Decisión live: `NO-GO`

## Contexto

ADR 0018 aceptó el contrato backend A1-S para staging sintético. Aquella fase
definió el workflow de intervención humana, sus contratos y el schema, pero no
había demostrado su recorrido Runtime completo contra PostgreSQL desplegado.

La implementación Runtime se construyó sobre
`a94dcd314c67880e40aa333dc679ef98b80a1956` y después necesitó tres correcciones
observadas durante la ejecución real de staging:

1. `d546b1368eeacf34bb50dea5820ab1ed27f93053`, consultas JSONB PostgreSQL;
2. `407ced9acbeffdd6c727264e8c9ac26e3cd110fa`, coherencia del reloj
   transaccional;
3. `9e0a26777f19efeb2c54b093e771570493a3de0e`, canonicalización del evento de
   release.

El último commit fue desplegado en Render y el smoke Runtime final completó
sus dos recorridos sintéticos. En consecuencia, la ejecución externa ya no
está pendiente. El preflight Runtime v1.1 que pasó con anterioridad auditó la
base `a94dcd3`, no el árbol final posterior a los tres hotfixes, y no puede
reutilizarse como evidencia del sujeto final.

## Decisión

Se acepta como completada la validación Runtime **exclusivamente sintética de
staging** del backend A1-S, con estado `completed_synthetic_staging`.

El gate de entrega se declara `passed_synthetic_staging` bajo la condición de
que un preflight nuevo, independiente y offline, audite con éxito el ZIP final
exacto antes de que este overlay se entregue o se committee:

```text
RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip
SHA-256: 038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e
commit/comment: 9e0a26777f19efeb2c54b093e771570493a3de0e
```

Esta decisión no autoriza producción ni transforma una prueba ASGI in-process
en una ruta pública. Tampoco acredita frontend, endpoint de login, datos
reales, contacto con proveedor o Administración, B2, presentación con efecto
jurídico ni recibo E4 auténtico.

## Cadena de decisión y entrega

Los commits relevantes son:

- `b0bc7ddfad9278e601dce8dd69083472662874b5`: gate G1;
- `37a4479022519d34d1a220cb1ac6380ea7b9f238`: contrato A1-S;
- `a94dcd314c67880e40aa333dc679ef98b80a1956`: schema PostgreSQL corregido;
- `aaf040b1c8d35ee61aa3720a4dc4b8cf1822b827`: Runtime sintético;
- `d546b1368eeacf34bb50dea5820ab1ed27f93053`: JSONB;
- `407ced9acbeffdd6c727264e8c9ac26e3cd110fa`: transaction clock;
- `9e0a26777f19efeb2c54b093e771570493a3de0e`: release event final.

Las identidades de overlay/hotfix congeladas fueron:

- A1-S overlay: `8ade7dbef0559e4e6bd739946ea97799a9dfeb1e1f3cf4ba3b77b937340466ac`;
- schema hotfix: `f9014d3faff19a73328a088798b5988aaef5ff7231e32eee8ef1268959b2bfb4`;
- Runtime overlay: `0f44d10543c777fd1ef36b20357934cafd4605c8a296d469af3b1af6b56c0e24`;
- preflight hotfix: `c3b26ed8c85393f7c45d0957805a00a4394d295b3705bb8259e6e0a90f0ceb2f`;
- SQL/JSONB hotfix: `e1c497a3e65aa8462f8f50050f11275ab30337b6d7203d6d76c16c4c3cbd3ebb`;
- clock hotfix: `4756378882a385e409ec3b8c8617c252039e2131e10ca7afdda97d535afb13fb`;
- event hotfix: `6bad4f4e5f7fcd5a39b30c14e934aa67a001c97a2d43fe68a6a969020170ddea`.

Estos hashes identifican entregas; no prueban autoría, firma del commit,
firma del ZIP ni provenance de supply chain.

## Alcance técnico original

La fase Runtime original quedó compuesta por:

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

El cierre actual solo puede modificar esos tres documentos, el test del
contrato documental, y añadir el preflight final y su test:

- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md`;
- `docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json`;
- `docs/rtm_connect/adrs/0019-a1s-runtime-validation.md`;
- `tests/test_rtm_connect_a1s_runtime_docs_contract.py`;
- `scripts/rtm_connect_a1s_runtime_evidence_preflight.py`;
- `tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py`.

No se admite ninguna mutación de los módulos Runtime en este cierre.

## Evidencia aceptada

La salida final observada en Render declaró:

- `authority=rtm_connect_a1s_runtime_smoke`;
- `version=rtm_connect_a1s_runtime_smoke_v1_0`;
- `ok=true`, `safe=true`, `blockers=[]`;
- aplicación HTTP ASGI in-process;
- schema PostgreSQL ready y fixture persistente auditada;
- happy path completo visible por API;
- rama UNKNOWN reconciliada a manual review, sin retry ciego;
- tres participantes sintéticos distintos y separación de funciones;
- idempotencia de prepare y reloj transaccional coherente;
- E4 sintética ligada al verificador preaprobado;
- cero intentos de socket externo;
- transacción revertida y flags temporales restaurados;
- cero sesiones efímeras remanentes;
- snapshot de conteos posterior igual al baseline registrado;
- `legal_submission_executed=false`, `routes_published=false` y
  `live_verdict=no_go`.

El deploy final observado fue `dep-da6qp1p5efls73d4q0kg` para
`9e0a26777f19efeb2c54b093e771570493a3de0e`, seguido de `/health` con
`{"ok":true}`. Los tests del mismo commit informaron 114 A1-S, 643 RTM CONNECT
y 1227 globales OK, con 8 skipped en la suite global.

Esta evidencia se clasifica como `operator_console_observed_unattested`: se
observó en la consola, pero no existe un artefacto bruto hash-bound ni una
firma verificable de la salida. El deploy y el health por separado tampoco
prueban el workflow.

## Provisioning y fixture

La cohorte `runtime-a94dcd3-v1` se provisionó con tres operadores sintéticos
distintos y quedó auditada como creation-only. Las filas A1-S son insert-only y
las filas preexistentes no se corrigen, reciclan ni borran.

El requester fue `0a558d35-01b0-4c74-8640-c690ec21d52c`, el releaser
`cd2d8df3-9e67-4c86-8f4e-1df55fb67b44` y el verifier
`9cae8979-f25f-4350-b3e7-1c8c7bd9c62b`. El smoke derivó esos sujetos de sus
memberships exactas; no se ejercitó ni se acredita un endpoint de login.

## Smoke, rollback y precisión probatoria

El smoke usó una aplicación ASGI temporal y una única transacción PostgreSQL.
Recorrió preparación, asignación, revisión, preaprobación, release, resultado,
recibo y verificación sintética; además recorrió UNKNOWN hasta manual review.
Las sesiones almacenaron solo hashes mientras la transacción estuvo abierta.

El resultado acredita `database_rolled_back=true`,
`fixture_snapshots_equal_to_baselines=true` y
`ephemeral_sessions_remaining=0`. También acredita que una conexión nueva
observó el estado posterior igual al baseline de conteos registrado.

No debe elevarse esa comprobación a una prueba de contenido byte-for-byte: no
se calcularon hashes de contenido de todas las filas persistentes. En
particular, para la fixture UNKNOWN solo se demostró `after == baseline`; no se
demostró independientemente que el baseline fuese cero absoluto. Por ello esta
ADR no afirma zero-delta de contenido, fixture persistente inmutable a nivel de
contenido ni residuo UNKNOWN absoluto cero.

## Gate de cierre

La ejecución Runtime no tiene blockers sintéticos pendientes. La admisión del
overlay de cierre es procedimental: debe auditarse el sujeto final exacto con
`scripts/rtm_connect_a1s_runtime_evidence_preflight.py` antes del commit. Un
resultado fallido impide el commit y anula la admisibilidad de esa entrega.

Ese preflight debe:

1. verificar SHA-256 y comentario exactos del ZIP final sin extraerlo;
2. rechazar miembros inseguros y duplicados casefold;
3. comparar el árbol base completo, tolerando únicamente normalización
   CRLF/LF en texto UTF-8;
4. aceptar exclusivamente los seis paths del cierre;
5. comprobar en el archive las firmas estructurales de los hotfixes JSONB,
   transaction clock y canonicalización del evento;
6. validar la semántica del manifiesto sin importar Runtime;
7. permanecer read-only, sin PostgreSQL, red ni secretos.

Una salida satisfactoria confirma el gate de entrega
`passed_synthetic_staging`. No cambia el gate de producción ni el veredicto
live.

## Consecuencias

El backend dispone de evidencia suficiente para considerar completado su
recorrido Runtime sintético A1-S en staging. Esto permite diseñar después la
adaptación del frontend contra el contrato, pero no declara que el frontend ya
esté preparado ni habilita rutas persistentes.

La producción continúa bloqueada hasta disponer, entre otros requisitos, de
identidad fuerte y autorización aplicable a datos reales, protección
documental, base legal y representación vigente, canal verificado de proveedor
o Administración, E4 auténtica, reconciliación remota, observabilidad, kill
switch, retención, revocación y aprobaciones ligadas a hash.

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
