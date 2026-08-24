# RTM CONNECT post-C8 · G0 · puerta offline de decisión

## Decisión

G0 **no es C9** y no amplía el runtime. Es una fotografía reproducible de la
decisión posterior al cierre inerte de C8. Su veredicto único es:

```text
gate_status = blocked
live_verdict = no_go
ok = false
safe = false
production_safe = false
audit_ok = true
offline_review_reproduced = true
production_authorized = false
live_canary_percent = 0
blockers = [<uno o más bloqueos concretos>]
expected_exit_code = 2
```

Una auditoría satisfactoria de G0 conserva `ok=false`, `safe=false`,
`production_safe=false`, `audit_ok=true`,
`offline_review_reproduced=true`, `blockers` poblados y termina con exit `2`.
`audit_ok` solo confirma que la revisión offline y su NO-GO congelado pudieron
reproducirse: no declara seguridad productiva. Exit `0` nunca significa
aprobación y no es una salida satisfactoria de este G0.
No significa que la puerta se haya despejado.

Completar G0 no autoriza producción, no crea una autorización y no convierte
un dry-run C8 en evidencia de un acto externo. El siguiente paso posible será
un pack específico y versionado de proveedor con un ADR nuevo y revisión
independiente. Ese futuro trabajo queda fuera de G0.

El código normalizado del siguiente paso es
`provider_specific_pack_and_new_adr_required`.

La regla C0 sigue intacta:

**CORE autoriza; CONNECT ejecuta el alcance exacto; la evidencia confirma; solo
entonces CORE puede cambiar el estado jurídico.**

## Base congelada

G0 solo evalúa la base exacta cerrada después de C8:

| Identidad | Valor |
|---|---|
| Commit | `a0ecdebd4575d54f7e89c69b9871a29039370d22` |
| SHA-256 del `git archive` | `5832b0acd854e0dc5d864521a5a9350e44802facb74eea6d28cc15f44dbbd14f` |
| Entradas / ficheros del ZIP | `524 / 505` |
| Snapshot crítico normalizado | `cc819ed72839500946910b643b30a181018a9665bc1fb3c37b67228697a116a5` |
| `evaluated_at` congelado | `2026-08-24T17:15:00Z` |
| Fingerprint reproducible | `f3e50831c3f3aa4d06382a32636a3a635524b3000f2611beeb6d7ad0f835c2a0` |

El hash de transferencia y el comentario del ZIP ligan la inspección al commit.
No acreditan por sí solos firma de supply chain, autoría, SBOM ni procedencia de
los artefactos externos representados por SHA-256.

El fingerprint se reproduce sobre el material canónico congelado, incluido
`evaluated_at=2026-08-24T17:15:00Z`; cambiar el instante, una identidad, el
inventario o un bloqueo cambia el resultado. El hash externo del ZIP entregado
aún debe conservarse y atestiguarse fuera del propio ZIP como
`delivery_zip_sha256`. También será obligatorio congelar el commit/hash futuro
del overlay G0 como `git_commit_sha40` cuando se materialice en Git; ninguno de
esos requisitos queda satisfecho por una referencia autocontenida.

## Evidencia positiva observada y no atestada

El registro operativo aportado indica que C8 terminó como plano inerte; G0 no
atestigua estas observaciones externas:

- suite completa observada: `1040` pruebas correctas y `8` omitidas;
- esquema C8 listo después de la migración aditiva;
- preflight C8 seguro, cero conectores y cero residuo C8;
- smoke C8 con `19` checks, rollback y cero residuo;
- despliegue Render live en el commit exacto y `/health` con `{"ok":true}`;
- ausencia de wiring C8, proveedor, secreto, transporte y activación live;
- `assert_live_activation_unavailable` incondicional.

El manifest observado `RTM_CONNECT_POST_C8_G0_EVIDENCE.json` se clasifica como
`observed_unattested`: estas observaciones no están atestadas ni firmadas
dentro del ZIP y solo cierran C8. No son evidencia validada, un recibo E4
auténtico de proveedor ni una autorización de producción.

## Seis dominios bloqueados

### 1. Seguridad

Faltan egress de producción default-deny medido, aislamiento de secretos,
workload identity, rol runtime de base de datos separado del propietario,
recomputación PostgreSQL de hashes canónicos, SBOM, provenance, SAST/SCA y
secret scan firmados.

La auditoría también localizó superficies legacy de presentación fuera de
CONNECT. Antes de cualquier pack real deben eliminarse, quedar en cuarentena o
migrarse completamente al contrato C0–C8.

### 2. Operaciones

Faltan SLO, métricas, alertas probadas, on-call, kill switch real, parada y
drenaje de claims, restore drill y reconciliación posterior. Los entrypoints,
cron, workflows y configuración efectiva de Render no forman parte completa
del ZIP y requieren inventario independiente.

### 3. Privacidad y control jurídico

Faltan inventario de datos, minimización, base jurídica, condiciones de
encargado, residencia, retención, redacción, DSAR y clasificación inequívoca de
fixtures frente a datos o activos reales.

`templates/firma.png` se usa en autorizaciones como firma del representante.
G0 lo clasifica como posible activo jurídico sensible. Debe retirarse del
artefacto de código y pasar a custodia con autorización, trazabilidad y
revocación, o sustituirse por un fixture inequívocamente sintético. Mientras no
se decida y pruebe, es un bloqueo.

### 4. Proveedor

No existe pack específico con entidad, tenant, origen HTTPS, protocolo,
schemas de request/receipt, errores, SLA, idempotencia remota, lookup read-only,
fencing, reconciliación UNKNOWN ni verificador E4 auténtico. Los hashes C8 de
proveedor, egress y referencia de credencial prueban formato, no contenido ni
procedencia.

### 5. Canary

El tope sintético C8 de 5 % no es un canary real. G0 fija
`live_canary_percent=0`. Faltan selección de una actuación legítima y
específicamente autorizada, denominador, cohorte, métricas, umbrales de aborto,
ventana de observación, promoción manual y prohibición demostrable de
autoexpansión.

### 6. Rollback

El rollback SQL C8 solo prueba cero residuo local sintético. No detiene un
efecto remoto. Faltan parada de claims, revocación de egress y secretos,
clasificación de lo ambiguo como UNKNOWN, restore/replay sin duplicación y
compensación como nueva acción R4 autorizada.

## Gobernanza transversal obligatoria

Esta gobernanza aplica a los seis dominios anteriores y no constituye un
séptimo dominio. Cualquier pack futuro debe incluir aprobaciones explícitas,
hash-bound y vigentes de security (`security_owner`), operations
(`operations_owner`), privacy (`privacy_owner`), legal/compliance
(`legal_compliance_owner`), el owner del proveedor o integración
(`provider_owner`) y el service owner accountable (`service_owner`).

La cadena mínima es `CORE + requester + independent activator + independent
verifier`, materializada como `core_authorizer`, `requester`,
`independent_release_activator` e `independent_evidence_verifier`. La
autorización CORE mantiene el doble control de C8; no se admiten cuentas
genéricas ni autoaprobación. Requester, activador y verificador deben quedar
identificados por separado. El activador es independiente del requester y de
quienes aprueban; el verificador es independiente del requester, del activador
y del ejecutor del proveedor.

Cada aprobación y artefacto declara `approval_timestamp`, `expires_at`,
`revocation_status`, owner y `evidence_freshness`, además de binding con commit
y artefacto. El menor vencimiento gobierna el pack y una revocación es
inmediata. Evidencia ausente, expirada, revocada, stale o de freshness
desconocida mantiene el NO-GO por defecto.

## Inventario de superficies legacy

Este inventario es un mínimo de seguridad, deliberadamente no exhaustivo. La
ausencia de una superficie no implica aprobación. G0 no modifica estas
superficies; las inmoviliza como bloqueos verificables:

| Superficie | Hallazgo |
|---|---|
| `/ops/automation/tick` | Está publicada por `app.py`; llega a `dgt_client.submit_pdf` fuera de la autoridad CONNECT y sin guard obligatorio `external_submission`. |
| `DGT_ENABLED` | Cualquier texto no vacío, incluso `0`, se considera configurado. |
| `submitter_dgt.py` | Contiene POST a un origen DGT de desarrollo fuera del pack CONNECT. |
| `submitters/registro.py` | Contiene POST a `REG_PROVIDER_URL` con token opcional, sin el contrato C0–C8 de idempotencia, fencing, lookup y E4. |
| `runtime_capabilities` | Mantiene compatibilidad fail-open si falta `RTM_ENV`; el startup efectivo debe fallar cerrado. |
| SMTP legacy | `cases.py`/`partner.py` conservan efectos de correo no cubiertos exhaustivamente por el guard global y algunos fallos se silencian. |
| Cron y workflow | `cron_tick.sh` y el workflow sintético usan red fuera de la superficie C8. |
| `vehicle_removal_router.py` / Stripe | Superficie montada que puede iniciar Stripe Checkout sin guard global suficiente. |
| `ops_operator_submit_router.py` | Superficie legacy/dormant con descarga arbitraria de `document_url` y opción `force`. |
| `dgt_test.py` | Ejecuta un `POST` a nivel superior si alguien invoca el fichero. |
| `README.md` | Documenta cron desatendido y operación “sin humanos”; la documentación no constituye autorización. |

Por ello G0 no afirma “cero red global”. Solo reconoce que la superficie C8
auditada carece estructuralmente de transporte real.

## Contrato de código G0

`rtm_connect_post_c8_g0.py` reside en la raíz, usa biblioteca estándar, no se
importa desde `app.py` ni desde `rtm_connect/__init__.py` y no acepta evidencia
capaz de despejar la puerta. Construye los seis findings fijos y solo permite
`blocked/no_go`.

Todos los permisos siguientes son constantes:

```text
review_only=true
offline_only=true
read_only=true
production_authorized=false
authorization_created=false
routes_allowed=false
workers_allowed=false
provider_contact_allowed=false
network_allowed=false
secret_access_allowed=false
database_access_allowed=false
database_ddl_allowed=false
database_dml_allowed=false
real_data_allowed=false
external_effects_allowed=false
live_activation_allowed=false
production_effects_available=false
production_safe=false
approval_matrix_satisfied=false
authority_chain_satisfied=false
evidence_freshness_satisfied=false
revocation_status_verified=false
c8_dry_run_is_authentic_e4=false
```

No existe enum o valor `go`, `approved`, `ready_for_production` o `go_live`.
`assert_g0_live_activation_unavailable` siempre lanza.

## Preflight y smoke

Los dos comandos G0 son offline y de solo lectura. Deben invocarse en modo
aislado, sin site packages y sin generar bytecode. Exigen el ZIP base mediante
`--archive`, calculan primero su SHA-256, validan comentario, CRC, nombres,
duplicados case-insensitive, symlinks, conteos, miembros requeridos y hashes.
Nunca extraen miembros al filesystem ni importan código del ZIP; sí pueden
descomprimirlos y leerlos en memoria para verificar su contenido.

```text
python -I -S -B scripts/rtm_connect_post_c8_g0_preflight.py --archive <ZIP> --compact
python -I -S -B scripts/rtm_connect_post_c8_g0_smoke.py --archive <ZIP> --compact
```

Una ejecución satisfactoria reproduce el NO-GO con `ok=false`, `safe=false`,
`production_safe=false`, `audit_ok=true`,
`offline_review_reproduced=true`, `blockers` poblados y exit `2`. Exit `0` no
es aprobación ni una salida satisfactoria de G0. El exit code `2` es el
resultado esperado de la reproducción bloqueada. No existe `--apply`,
migración, conexión PostgreSQL, variable de activación, ruta, worker, red,
secreto ni dato real.

## Criterio de cierre G0

G0 se cierra cuando:

1. el ZIP y commit exactos se verifican sin extracción;
2. el inventario de seis dominios es determinista y hash-bound;
3. las superficies legacy y el activo de firma quedan documentados como
   bloqueos;
4. cualquier mutación de identidad, permiso, canary o veredicto falla cerrada;
5. las pruebas confirman que no existe camino de runtime desde G0;
6. el resultado permanece `blocked/no_go`;
7. el hash externo de entrega queda conservado y atestiguado;
8. el commit/hash futuro del overlay G0 queda congelado tras materializarse en
   Git.

El cierre de G0 congela la decisión. **No elimina los bloqueos ni autoriza
producción.**
