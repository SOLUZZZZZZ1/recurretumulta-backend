# RTM CONNECT post-C8 · G1 · admisión offline de proveedor

## Decisión

G1 **no es C9**, no selecciona proveedor y no amplía el runtime. Congela la
identidad de entrega del overlay G0 y somete a admisión las tres superficies
legacy que podrían confundirse con un proveedor productivo. Las tres quedan
rechazadas.

El resultado único de una revisión satisfactoria es:

```text
gate_status = blocked
live_verdict = no_go
ok = false
safe = false
production_safe = false
audit_ok = true
offline_review_reproduced = true
provider_selected = false
provider_pack_present = false
provider_pack_admissible = false
live_canary_percent = 0
expected_exit_code = 2
```

`audit_ok=true` acredita únicamente que la revisión offline pudo
reproducirse. No acredita proveedor, autorización, seguridad productiva ni un
efecto externo. Exit `0` no es una salida satisfactoria de G1.

La regla C0 sigue intacta:

**CORE autoriza; CONNECT ejecuta el alcance exacto; la evidencia confirma; solo
entonces CORE puede cambiar el estado jurídico.**

## Base G0 congelada

| Identidad | Valor |
|---|---|
| Commit declarado y comentario del ZIP | `eedd521ecf1703c9b5e20196651da04557900e74` |
| SHA-256 externo del ZIP | `8d69d66573d92b675be26d391c1d03a74ff62a514bdf369dfce817db396ba3f3` |
| Entradas / ficheros | `533 / 514` |
| Snapshot crítico normalizado | `04bbab064c06e58da288e43a2918f57e37ff3eca0f00ece5b81cfdd5f0bc903d` |
| `evaluated_at` congelado | `2026-08-25T05:35:21Z` |
| Fingerprint G1 | `16e3a23fed9e9771fae4a3ce75079a8e7e3d0764aa6ee9fd03268acf3158d253` |

El SHA-256 recibido coincide exactamente con la comprobación externa y el
comentario del ZIP coincide con el commit declarado. Esto congela la identidad
de entrega necesaria para revisar G1, pero no permite reconstruir ni atestiguar
el objeto commit de Git, la autoría, una firma de supply chain, una SBOM o la
procedencia de artefactos externos. G1 conserva explícitamente esa limitación.

El G0 contenido en la base conserva `blocked/no_go`,
`production_effects_available=false`, `live_activation_available=false` y
`live_canary_percent=0`. G1 no lo sustituye ni lo despeja.

## Tres candidatos legacy rechazados

### 1. `legacy.dgt_client_placeholder`

Fuentes congeladas: `dgt_client.py`, `ops_automation.py` y
`ops_automation_router.py`.

Se rechaza porque no identifica entidad, tenant, origen o protocolo; considera
configurado cualquier valor no vacío de `DGT_ENABLED`, incluido `0`; termina en
`NotImplementedError`; no aporta autoridad CONNECT, idempotencia remota,
lookup, reconciliación ni E4 auténtica.

### 2. `legacy.dgt_dev_xml_submitter`

Fuente congelada: `submitter_dgt.py`.

Se rechaza porque contiene un endpoint de desarrollo no verificado, identidad
de solicitante codificada y datos de titular de ejemplo, firma mediante un
proceso Java no atestado y ausencia de contrato CONNECT, idempotencia, fencing,
recibo verificable, reconciliación y rollback remoto.

### 3. `legacy.registro_general_generic`

Fuentes congeladas: `submitters/registro.py` y `submitters/base.py`.

Se rechaza porque `REG_PROVIDER_URL` no identifica una entidad ni un tenant,
acepta una URL arbitraria y un bearer token opcional, envía el PDF completo en
base64 sin contrato de tamaño ni idempotency key, y acepta un justificante
base64 sin un verificador E4. Tampoco define lookup read-only, fencing,
reconciliación UNKNOWN o rollback remoto.

Que una superficie tenga un guard parcial o devuelva un PDF no la convierte en
un pack específico de proveedor. Los tres candidatos mantienen:

```text
status = rejected
provider_specific = false
production_eligible = false
```

## Dossier obligatorio para una unidad futura

G1 congela catorce secciones mínimas. Un futuro dossier deberá aportar todas,
con identidades, contratos, hashes, responsables y evidencia vigente:

1. identidad jurídica del proveedor y owner responsable;
2. tenant y alcance de servicio autorizado;
3. origen HTTPS, protocolo y versión;
4. schema de request y hashing canónico;
5. schema de receipt y verificador E4 auténtico;
6. idempotencia remota, fencing y lookup read-only;
7. reconciliación UNKNOWN y taxonomía de errores;
8. workload identity, custodia de secretos y allowlist de egress;
9. inventario de datos, base jurídica, retención, redacción y DSAR;
10. SLO, alertas, on-call, kill switch y drenaje de claims;
11. cohorte canary, umbrales de aborto y prohibición de autoexpansión;
12. rollback remoto, restore, replay y compensación autorizada;
13. aprobaciones hash-bound, caducidad, revocación y separación de funciones;
14. SBOM, provenance, SAST, SCA y secret scan firmados.

La mera presencia formal de esas secciones no será una autorización. El pack
deberá ser específico, verificable e independiente y tendrá su propio ADR.

## Contrato de código G1

`rtm_connect_post_c8_g1.py` reside en la raíz, usa solo biblioteca estándar y
no se importa desde `app.py` ni desde `rtm_connect/__init__.py`. No recibe un
manifest externo capaz de convertir un dato en GO.

Los únicos hechos positivos son de revisión: la identidad de entrega base se
verificó, G0 se preservó y los tres candidatos se revisaron. Todos los permisos
y capacidades siguientes son falsos:

```text
provider_selected=false
provider_identity_verified=false
provider_pack_present=false
provider_pack_admissible=false
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
authentic_e4_verifier_available=false
remote_idempotency_verified=false
read_only_lookup_verified=false
unknown_reconciliation_verified=false
remote_rollback_verified=false
legacy_candidates_are_provider_pack=false
g0_no_go_overridden=false
```

No existe enum o valor `go`, `approved`, `ready_for_production` o `go_live`.
`assert_g1_live_activation_unavailable` siempre lanza.

## Preflight y smoke

Ambos comandos son offline y de solo lectura. Deben ejecutarse con intérprete
aislado, sin site packages y sin bytecode:

```text
python -I -S -B scripts/rtm_connect_post_c8_g1_preflight.py --archive <ZIP_G0> --compact
python -I -S -B scripts/rtm_connect_post_c8_g1_smoke.py --archive <ZIP_G0> --compact
```

Validan primero SHA-256, comentario, CRC, conteos, nombres seguros, duplicados
case-insensitive y miembros críticos. No extraen archivos del ZIP ni importan
su código. Después comparan el árbol local completo con los 514 ficheros base
y permiten únicamente los nueve ficheros del overlay G1.

Una revisión satisfactoria devuelve `audit_ok=true`,
`offline_review_reproduced=true`, `ok=false`, `safe=false`,
`production_safe=false`, bloqueos poblados y exit `2`.

## Criterio de cierre G1

G1 se cierra cuando:

1. el ZIP G0 exacto queda verificado antes de cualquier extracción;
2. la identidad de entrega G0 queda ligada a commit declarado, comentario y
   SHA-256 externo, con procedencia no atestada expresamente;
3. los tres candidatos legacy quedan congelados y rechazados;
4. el dossier futuro queda definido sin inventar proveedor ni protocolo;
5. cualquier mutación a GO, canary mayor que cero, selección, contacto,
   autoridad o efecto falla cerrada;
6. no existe wiring desde `app.py`, `rtm_connect/__init__.py`, rutas, workers,
   base de datos, red o secretos;
7. preflight, smoke, pruebas específicas y regresión superan sus contratos;
8. el overlay G1 queda posteriormente ligado a su commit y SHA-256 de entrega.

Cerrar G1 no autoriza producción. El siguiente paso normalizado es
`verified_provider_dossier_and_provider_specific_g2_required`; solo puede
comenzar cuando exista un proveedor real identificado y documentación
verificable. No se inicia C9.
