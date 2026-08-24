# ADR-0015 · C8 como admisión inerte, no activación live

**Estado:** Aceptado para staging sintético; producción real NO-GO.

## Contexto

C0 ordena `C8_controlled_production`, pero C6 demuestra solo HTTP loopback y
declara que un proveedor real necesita código, versión, ADR, origen, protocolo
y evidencia propios. C7 demuestra un handoff R4 sintético cuyo acto final sigue
siendo humano. El repositorio no identifica proveedor, tenant, endpoint,
credencial, semántica de recibo, retención idempotente o API de reconciliación
real.

Interpretar “controlled production” como permiso para inventar esos elementos
rompería la frontera de autoridad y podría crear un efecto irreversible. No
hacer nada, en cambio, impediría probar las puertas que una futura integración
debe superar.

## Decisión

C8 v1 será un plano inerte de admisión con candidatos hash-bound, doble
aprobación humana y outbox de dry-run. Funciona exclusivamente en staging
sintético, permanece default-off y no publica ruta, worker o transporte.

Todo candidato declara literalmente:

```text
simulation_only=true
external_effects_allowed=false
live_activation_allowed=false
human_activation_required=true
canary_percent<=5
concurrency=1
```

Proveedor propuesto, egress y referencia de credencial solo pueden
representarse mediante SHA-256 no reversible. C8 no almacena su valor y no
resuelve secretos.

La acción de admisión es R4, exige E4 y doble control CORE, pero conserva
`legal_effect_authorized=false`. Seguridad y operaciones aportan atestaciones
separadas. La outbox solo usa `prepared`, `claimed`, `dry_run_confirmed`,
`unknown`, `manual_review` y `cancelled`; no tiene operación de envío.

`assert_live_activation_unavailable` siempre lanza. No existe combinación de
flags capaz de cambiar esa decisión.

## Reglas

1. C8 no modifica ni reutiliza como real los tuples C6 o C7.
2. La frontera exhaustiva C7/C6 se valida antes de toda admisión.
3. Todos los flags C8 live deben estar ausentes o falsos; endpoints, tokens,
   credenciales, proxies y release tokens dormidos están prohibidos.
4. El candidato se liga a commit SHA-40 y SHA-256 de artefacto, manifest,
   contrato, egress, referencia de credencial, esquema y pruebas.
5. Canary es positivo y ≤ 5 %; concurrencia, límite total y límite diario son
   `1`; payload es ≤ 1 MiB y vigencia ≤ 86 400 segundos.
6. Acción, grant y payload son exactos; solicitante y dos aprobadores CORE son
   distintos.
7. La aprobación de seguridad y la de operaciones son identidades distintas y
   nunca una activación live.
8. Idempotencia precede a la intención simulada.
9. `unknown` y `manual_review` requieren reconciliación local; blind retry es
   siempre falso.
10. Un lease expirado conserva token y fence, no se reclama y debe
    clasificarse sobre la intención original.
11. PostgreSQL bloquea action/grant/release, usa reloj posterior al lock y
    aplica identidad única por release, cuota, payload, concurrencia uno,
    vigencias y TTL aunque se intente escribir fuera del control Python.
12. El emergency halt valida el snapshot inerte persistido y no depende de que
    el entorno actual siga bien configurado.
13. Un dry-run no constituye evidencia de un acto externo.
14. Rollback elimina datos sintéticos; C8 demuestra ausencia estructural de
    transporte, mientras que un futuro pack deberá instrumentar DNS, sockets y
    secretos.
15. Producción real sigue bloqueada hasta un pack específico revisado y
    versionado.
16. Funciones y relaciones están schema-qualified; `pg_temp` queda al final
    del `search_path`; las cuatro tablas bloquean `TRUNCATE` y los JSONB no
    aceptan claves fuera de las allowlists inertes.
17. La secuencia de eventos coincide con la versión del padre; evento, actor,
    transición, razón y payload forman una matriz cerrada.
18. El código de release se liga al binding. Recalcular en PostgreSQL todos
    los hashes canónicos y separar el rol propietario del rol DML siguen
    siendo requisitos del pack real, por lo que C8 v1 permanece NO-GO.

## Puerta futura de producción

Otro ADR deberá congelar como mínimo: provider/tenant exactos, tuple y manifest
nuevos, request y receipt schemas, HTTPS/mTLS y egress default-deny, secreto por
workload identity, outbox durable y fencing, effect key estable, idempotencia
remota, lookup GET-only, evidencia E4 auténtica, roles DB separados, kill
switch, cuotas, canary, rollback y reconciliación de restore.

Hasta entonces la respuesta de cualquier evaluación live es `no_go`.

## Consecuencias

C8 permite probar revisión, hashes, separación de funciones, límites,
idempotencia y UNKNOWN sin afirmar compatibilidad con un proveedor ni crear un
camino remoto dormido. El coste deliberado es que completar todas las puertas
de la simulación no habilita producción: únicamente produce evidencia de que
el candidato fue evaluado de forma inerte.
