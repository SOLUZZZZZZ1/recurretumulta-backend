# RTM CONNECT C8 · admisión inerte de producción controlada

## Decisión de alcance

C8 v1 **no activa producción**. Añade un plano de admisión y una outbox
simulada para comprobar, en staging sintético, si un futuro pack de proveedor
podría ser revisado sin rebajar los invariantes C0–C7.

El resultado real de producción permanece **NO-GO** hasta que exista un pack
específico y versionado con proveedor, tenant, protocolo, origen HTTPS,
credencial, idempotencia remota, reconciliación, evidencia auténtica, ADR y
pruebas propios. C8 no amplía `controlled.sandbox/v1.0`, no transforma
`assisted.legal/v1.0` y no cambia `synthetic_only` de ningún conector existente.

La regla de autoridad continúa congelada:

**CORE autoriza; CONNECT ejecuta el alcance exacto; la evidencia confirma; solo
entonces CORE puede cambiar el estado jurídico.**

En C8 v1 CONNECT ni siquiera ejecuta: solo registra y evalúa una simulación.

## Ausencias deliberadas

C8 v1 no contiene ni acepta:

- proveedor, endpoint, origen, tenant o ruta remota;
- secreto, token, clave privada, credencial o resolver de credenciales;
- transporte, DNS, socket, proxy, redirect, webhook o polling externo;
- ruta HTTP, worker, scheduler o import de un submitter legacy;
- datos reales, expedientes, documentos o destinos reales;
- activación live, efecto legal, financiero o externo;
- retry automático o mecanismo capaz de convertir una simulación en envío.

`assert_live_activation_unavailable` es una barrera no configurable y siempre
falla. Una variable de entorno no puede convertir el overlay en producción.

## Candidato de admisión congelado

`ProductionAdmissionCandidate` conserva exclusivamente identificadores,
límites y digests no sensibles:

- UUID de candidato y solicitante;
- commit SHA-40 exacto;
- SHA-256 del artefacto, manifest de conector, contrato de proveedor propuesto,
  política de egress, referencia de credencial, snapshot de esquema e informe
  de pruebas;
- timestamps UTC de creación y expiración;
- canary positivo y no superior al 5 %;
- concurrencia exacta `1`;
- una sola simulación total por release y por día, payload máximo de 1 MiB y
  vigencia máxima de 86 400 segundos;
- flags exactos `simulation_only=true`,
  `external_effects_allowed=false`, `live_activation_allowed=false` y
  `human_activation_required=true`.

No se conserva el valor de un endpoint o secreto. Incluso la futura referencia
de credencial aparece solo como SHA-256 dentro del candidato.

El SHA-256 canónico del candidato liga la acción y la autorización CORE. Un
cambio de commit, artefacto, política, prueba, límite o flag cambia el digest y
anula la aprobación previa.

## Autoridad R4/E4 y doble control

La única acción admisible usa:

```text
capability  = connect.production.admission.simulate
satellite   = rtm.connect.production.admission
target_type = production.admission.candidate
target_ref  = synthetic-c8-admission
risk         = R4_critical_regulated
mode         = assisted
evidence     = E4_receipt_verified
```

El payload es una allowlist sintética exacta con el digest del candidato y los
cuatro flags inertes. No admite expediente, correlación o hashes documentales.

CORE aporta un grant congelado del emisor
`rtm.core.authorization/rtm_core_authority_v1`, con exactamente dos aprobadores
distintos del solicitante y vigencia acotada. El grant exige E4 y modo assisted,
pero mantiene `legal_effect_authorized=false`: autoriza revisar una simulación,
nunca presentar ni activar.

La admisión operativa separa además las aprobaciones humanas de seguridad y
operaciones. Son atestaciones distintas, hash-bound, vigentes y emitidas por
operadores diferentes. Ninguna de ellas es un permiso live.

## Outbox simulada

La outbox C8 solo conserva identidad y auditoría. Su máquina coincide con el
workflow de admisión:

```text
prepared → claimed → dry_run_confirmed
                 ↘ unknown → manual_review
prepared | claimed → cancelled
```

También se admite `manual_review` como clasificación expresa. Cada intención
declara siempre:

```text
simulation_only=true
external_effects_allowed=false
network_call_performed=false
secret_resolution_performed=false
blind_retry_allowed=false
```

`unknown` y `manual_review` conservan
`reconciliation_required=true`. Nunca vuelven a `claimed` mediante retry
automático. La reconciliación C8 solo compara ledgers y atestaciones locales;
no consulta ni llama un proveedor. Un dry-run confirmado no es E2, E3 o E4 de
un acto real y no permite que CORE confirme un efecto jurídico.

Cada claim tiene un único token, fence y lease máximo de 300 segundos. Al
expirar no se reclama ni se recicla: conserva su identidad y debe clasificarse
como `unknown` con el fence original o derivarse a revisión manual. Para el
cálculo de concurrencia solo cuentan leases todavía vigentes; un lease
expirado no autoriza repetir su intención.

La identidad semántica también se cierra en PostgreSQL: existe como máximo una
fila de outbox por release. Cambiar UUID, action/grant recreados, hashes o keys
no permite crear una segunda intención ni reintentar un resultado UNKNOWN.

## Puertas de release fail-closed

El plano puede declarar una simulación admisible únicamente si todas las
puertas siguientes aportan evidencia hash-bound:

1. commit, artefacto, manifest y suite exactos;
2. contrato propuesto de proveedor y política de egress representados solo por
   SHA-256, sin material ejecutable;
3. staging aislado C6/C7: identidad, namespace, base, rol, `search_path`, rama,
   datos sintéticos y capacidades externas desactivadas;
4. feature C8 default-off y ausencia total de variables live o dormidas;
5. candidato vigente, canary ≤ 5 %, concurrencia `1`, una intención total y
   diaria, payload ≤ 1 MiB y vigencia ≤ 24 horas;
6. acción y grant exactos R4/E4, dos aprobadores CORE y solicitante separado;
7. aprobación de seguridad y aprobación de operaciones separadas;
8. idempotencia reclamada antes de crear la intención simulada;
9. UNKNOWN sin retry ciego y revisión manual trazable;
10. rollback y cero residuo sintético comprobados.

El trigger PostgreSQL bloquea action y grant con `FOR SHARE`, bloquea la
release padre con `FOR UPDATE` y captura un reloj nuevo después de adquirir
los locks. Vuelve a aplicar identidad única por release, cuota, tamaño de
payload, concurrencia uno, vigencias y TTL del claim. Un dry-run solo puede
confirmarse mientras su lease siga vigente; UNKNOWN conserva el fence
original.

Todas las funciones califican el esquema `public` y fijan un `search_path`
cerrado con `pg_temp` al final. Las cuatro tablas bloquean `DELETE` y
`TRUNCATE`; releases, outbox y metadatos/eventos usan allowlists y flags
inertes. La secuencia de cada ledger debe coincidir además con la versión del
padre y cada tipo de evento fija transición, actor, razón y payload exactos.
Así una tabla temporal homónima, metadata adicional o un evento espurio no
pueden ampliar el scope ni borrar la identidad idempotente.

El código de release queda ligado al prefijo de su binding. C8 todavía no
convierte el rol propietario de la base en un rol de ejecución sin DML ni
recalcula dentro de PostgreSQL todos los SHA-256 canónicos; ambas son puertas
explícitas que un futuro pack real deberá cerrar antes de cualquier GO.

Una puerta ausente, desconocida, expirada o contradictoria bloquea la
simulación. Aunque todas pasen, el veredicto live continúa `no_go` y
`production_effects_available=false`.

## Evidencia

La evidencia C8 prueba únicamente la admisión simulada: candidato canónico,
hashes de artefacto/política/pruebas, acción, grant, aprobaciones separadas,
intención, transiciones y resultado dry-run. Es append-only y debe ligarse a
los UUID y SHA-256 exactos.

No se presenta un HTTP 200, una referencia sintética ni una atestación local
como recibo de proveedor. Un futuro pack real deberá definir evidencia E4
auténtica ligada a acción, intento, autorización, tenant, request hash,
referencia externa, receipt hash, almacenamiento inmutable, firma o consulta
independiente y verificador separado.

## Egress y secretos

La política C8 rechaza antes de DML cualquier flag live o configuración
dormida de proveedor, endpoint, proxy, tenant, credencial, token, secreto,
clave, release token o activación. Después vuelve a exigir la frontera completa
de staging C7/C6.

Staging debe añadir defensa de infraestructura: identidad sin acceso a secretos
de producción y egress default-deny. En C8, las inspecciones del grafo runtime,
imports y código confirman que no existe cliente, transporte ni llamada de
red; el smoke solo ejercita DML transaccional. El futuro pack real deberá añadir
instrumentación efectiva de DNS, sockets y clientes. Un rollback SQL no se
considera prueba de ausencia de efecto externo.

## Idempotencia, UNKNOWN y reconciliación

La clave C0 y el hash de solicitud preceden a la intención. La misma clave y
scope reutilizan el mismo expediente lógico; un contenido distinto es
conflicto. El identificador de intención no entra en el material idempotente y
la unicidad por release impide sustituir los digests para eludirla.

Una caída o incoherencia local puede producir `unknown`, pero nunca habilita
envío, nueva clave o segundo intento externo. Solo se permite reconciliación
local sobre la intención original o `manual_review`.

Un futuro proveedor real deberá añadir outbox durable comprometida antes del
socket, effect key estable, fencing, deduplicación remota y lookup read-only.
C8 v1 no afirma que esas condiciones ya existan.

## Rollback

El rollback C8 revierte únicamente registros sintéticos y preserva auditoría.
Debe demostrar cero candidatos, aprobaciones, intents y eventos residuales
desde una conexión nueva. La ausencia estructural de transporte y resolución
de secretos se audita por separado; un futuro pack con red deberá demostrar
además cero DNS, sockets y accesos a secretos fuera de allowlist.

En producción real un rollback de código no deshace un efecto. El futuro pack
deberá detener claims, revocar egress/secretos, convertir lo enviado o ambiguo
en UNKNOWN, reconciliar y autorizar cualquier compensación como una acción
nueva.

## Amenazas que mantienen el NO-GO

- confusión staging/producción por variables de entorno;
- bypass directo de funciones internas o rutas futuras;
- mutación de acción, grant, candidato, manifest o límites;
- colapso del doble control o uso de identidad compartida;
- replay, concurrencia, caída o restore que duplique una actuación;
- SSRF, redirects, proxy ambiental o exfiltración de credenciales;
- evidencia falsa, cruzada o seleccionada como “la última”;
- retry ciego desde UNKNOWN;
- asumir que rollback local revierte un efecto remoto.

C8 v1 elimina la posibilidad de efecto en vez de aceptar riesgo residual.

## Criterio de cierre

C8 inerte se cierra cuando contratos y política son puros, default-off y
staging-only; los docs congelan el NO-GO; la outbox usa la máquina acordada; las
mutaciones de hashes, flags, R4/E4, doble control y vigencia fallan cerradas; el
smoke deja cero residuo; y las pruebas confirman la ausencia estructural de
resolver de secretos, cliente de red, ruta, worker, proveedor y efecto externo.

Este cierre **no es autorización de producción real**.
