# RTM CONNECT C6 · proveedor sandbox HTTP controlado

## Qué demuestra C6

C6 atraviesa por primera vez un borde HTTP con autenticación por referencia,
idempotencia y reconciliación, pero **no integra ni suplanta a un proveedor u
organismo real**. El único actor probado es un sandbox de referencia controlado
por RTM y alimentado con datos sintéticos.

El conector congelado es:

- `controlled.sandbox/v1.0`;
- modo `api`;
- capacidad única `sandbox.http.probe`;
- riesgo exacto `R1_low_reversible`;
- evidencia máxima `E2_external_reference`;
- `synthetic_only=true`, `network_used=true`;
- idempotencia y reconciliación obligatorias.

Una integración posterior con un proveedor identificado necesitará otro tuple,
manifiesto, ADR, origen, protocolo y validación de evidencia. C6 no acredita
compatibilidad con DGT, OEPM ni ninguna Administración.

## Alcance cerrado del probe

CORE solo puede autorizar una acción sin expediente, documentos, destinatarios,
`correlation_id`, texto libre ni datos personales. El destino y payload son
exactos:

```text
satellite   = rtm.connect.sandbox
target_type = sandbox.probe
target_ref  = synthetic-probe
payload     = {"synthetic_marker":"RTM_C6_SYNTHETIC_ONLY"}
```

La autorización exige modo API, E2, riesgo R1, ningún efecto legal,
`legal_effect_authorized=false` y el emisor exacto
`rtm.core.authorization/rtm_core_authority_v1`. CORE debe haber persistido la
acción y su autorización inmutable **antes** de invocar CONNECT; C6 no crea ni
se autoemite el grant. CONNECT compara todos sus campos con la última
autorización de PostgreSQL y vuelve a validar acción, grant, vigencia, hash,
idempotencia y modo inmediatamente antes del socket. La propia orquestación
comprueba entorno, rama runtime idéntica a `RTM_EXPECTED_BRANCH`, nombre y rol
reales de la base staging, `search_path` efectivo exclusivamente
`pg_catalog,public`, ausencia de schema temporal y transporte loopback sellado
antes de cualquier DML C6. También exige igualdad campo por campo entre la
acción suministrada y la acción CORE persistida, incluidos solicitante,
timestamp y `correlation_id`.

## Protocolo estable

El POST usa `/v1/probes`, `Idempotency-Key` con la clave exacta congelada por
CORE y `X-RTM-Request-SHA256`. El cuerpo canónico es estable entre intentos:

```json
{
  "contract_version": "rtm.c6.controlled_sandbox.probe.v1",
  "client_reference": "<action_id>",
  "request_sha256": "<sha256 C0>",
  "marker": "RTM_C6_SYNTHETIC_ONLY"
}
```

No incluye `attempt_id`, timestamps ni aleatorios. El sandbox deduplica por la
pareja clave/cuerpo: el mismo key y body reutiliza el resultado; la misma clave
con otro cuerpo devuelve conflicto sin segundo efecto. La respuesta debe tener
exactamente versión, `environment=sandbox`, estado, client reference, request
hash y la referencia externa determinista `c6probe-<action_id>`.

Un HTTP 200 aislado nunca confirma. Solo una respuesta válida y correlacionada
crea E2; entonces CORE puede confirmar el probe con el `evidence_id` exacto.

## UNKNOWN y reconciliación

Timeout o reset posteriores al intento de socket, redirect, respuesta
malformada, sobredimensionada o no correlacionada se clasifican como `unknown`.
Un fallo de configuración, secreto o destino detectado antes del POST aborta y
no inventa UNKNOWN. Se conserva la referencia externa determinista, pero no se
finge E2 si el proveedor no fue observado.

No se repite el POST. La reconciliación parte del mismo intento mediante
`begin_reconciliation(..., attempt_id=...)` y hace exclusivamente:

```text
GET /v1/probes/by-client-reference/<action_id>
```

El GET es estrictamente observacional: el smoke cambia el resultado simulado de
forma independiente antes de consultarlo. `accepted` correlacionado aporta E2 y
permite confirmar; `unknown` permanece unknown; `rejected` termina
permanentemente. Una reejecución idempotente de una acción no terminal queda
bloqueada; una terminal se reutiliza sin socket ni nuevo intento.
La resolución actualiza también el intento original: elimina el error ambiguo
al confirmar, o conserva la clasificación vigente para unknown/rejected.

## Transporte y secretos

- el origen `.invalid` queda documentado, pero v1.0 bloquea en código todo
  transporte no loopback antes de DNS;
- el smoke crea un endpoint HTTP con IP loopback literal y puerto efímero;
- sin proxy ambiental, redirects ni retry automático;
- esta versión no implementa transporte HTTPS externo: requerirá otra versión;
- plazo absoluto máximo 10 segundos, vigilado también durante status, headers y
  lectura; petición 8 KiB y respuesta 64 KiB con `Content-Length` único;
- JSON UTF-8 estricto, sin duplicados, NaN, compresión ni campos extra;
- paths y headers fijos, nunca tomados del payload o de una respuesta;
- secreto solo por `env://RTM_CONNECT_C6_SANDBOX_TOKEN` allowlisted;
- el resolver copia exclusivamente la variable allowlisted, exige bearer ASCII
  visible y devuelve un valor opaco, inmutable y no serializable; no se admite
  un resolver alternativo con efectos laterales;
- endpoint, transporte y conector son tipos exactos e inmutables y se
  revalidan inmediatamente antes del borde HTTP;
- valor con `repr`/`str` censurados y nunca persistido, logueado ni reportado;
- excepciones HTTP/JSON crudas no se encadenan al error normalizado.

La seguridad de egress en infraestructura sigue siendo defensa necesaria para
un proveedor real.

## Persistencia y runtime

C6 reutiliza C1: conectores, acciones, autorizaciones, intentos, idempotencia,
evidencia y transiciones. No añade DDL ni migración. La auditoría comprueba C1,
C3 y C4, las columnas de operador requeridas, el enlace/estado/tipo exacto de
los triggers C1 (incluida su columna `UPDATE OF`), el hash normalizado de sus
funciones guard y la definición canónica de columnas, orden, predicado y
unicidad de índices y de cada constraint requerida. El conector existe solo en
la transacción del smoke y
desaparece con rollback; por eso no altera la allowlist persistente de C5.

No se modifica `app.py`, no se publica ruta, worker ni panel de ejecución, no se
siembra conector y no se deja endpoint o credencial persistente. El preflight
combina el hash congelado de `app.py` con la inspección del grafo efectivo de
rutas, subapps, dependencias y middleware. La ejecución externa general sigue
sin publicarse.

## Criterio de cierre

- esquema de solo lectura, cero DDL y snapshot inalterado;
- preflight sin red, feature default-off y cero residuo C6;
- smoke HTTP loopback transaccional;
- idempotencia local y remota antes de efecto, y replay terminal sin llamada;
- timeout a UNKNOWN, bloqueo de POST ciego y reconciliación GET-only;
- E2 exacta, secreto censurado y metadata allowlisted;
- rollback con cero conectores, acciones, intentos y evidencia sintética;
- regresión C0–C5 y `/health` correctos;
- restore remoto verificado.
