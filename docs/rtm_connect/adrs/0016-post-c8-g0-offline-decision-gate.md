# ADR-0016 · G0 post-C8 como puerta offline NO-GO

**Estado:** Aceptado como inventario de decisión; producción real NO-GO.

## Contexto

C0 ordenaba C0→C8 y C8 cerró el plano de admisión sintética sin ruta, proveedor,
secreto, transporte o permiso live. Interpretar el final de esa secuencia como
autorización de producción rompería la regla de autoridad. También sería
incorrecto diseñar una C9 genérica antes de identificar un proveedor y sus
contratos reales.

La inspección post-C8 confirmó además superficies legacy fuera de CONNECT:
automatización OPS/DGT publicada, submitters con red dormidos, compatibilidad
fail-open cuando falta `RTM_ENV`, correo legacy y un activo de firma embebido.
C8 permanece correctamente inerte, pero una evaluación de producción debe
abarcar el servicio completo y no solo los tokens C8.

## Decisión

Se crea G0 como puerta offline de decisión, distinta de C9. G0 liga su
inventario al commit
`a0ecdebd4575d54f7e89c69b9871a29039370d22`, al ZIP SHA-256
`5832b0acd854e0dc5d864521a5a9350e44802facb74eea6d28cc15f44dbbd14f`
y al snapshot crítico
`cc819ed72839500946910b643b30a181018a9665bc1fb3c37b67228697a116a5`.
La evaluación fija `evaluated_at=2026-08-24T17:15:00Z` y el fingerprint
reproducible
`f3e50831c3f3aa4d06382a32636a3a635524b3000f2611beeb6d7ad0f835c2a0`.
El fingerprint cubre el material canónico congelado; cambiar timestamp,
identidad, inventario o bloqueo produce otro valor. El hash externo de entrega
aún debe conservarse y atestiguarse fuera del ZIP como `delivery_zip_sha256`, y
el commit/hash del overlay G0 (`git_commit_sha40`) solo podrá congelarse cuando
se materialice en Git.

La implementación offline reside en el módulo raíz
`rtm_connect_post_c8_g0.py`; no se integra en `rtm_connect/__init__.py` ni en el
runtime de `app.py`.

G0 congela seis dominios —seguridad, operaciones, privacidad, proveedor,
canary y rollback— y todos permanecen `blocked`. Solo admite:

```text
gate_status=blocked
live_verdict=no_go
ok=false
safe=false
production_safe=false
audit_ok=true
offline_review_reproduced=true
live_canary_percent=0
blockers=[<uno o más bloqueos concretos>]
expected_exit_code=2
```

No acepta un manifest aportado por el llamante que pueda convertir un digest o
un booleano en autorización. Cuando exista un pack real, otro ADR deberá
versionar el mecanismo de revisión y sus identidades.

## Reglas

1. G0 no modifica el manifiesto C0 congelado ni se añade como C9.
2. No modifica `app.py`, `rtm_connect/__init__.py`, C8, routers, middleware,
   conectores, esquemas, workflows, cron o Render.
3. No publica ruta, worker, scheduler, webhook, polling o capability.
4. No crea ni aplica DDL/DML y no abre conexión de base de datos.
5. No admite proveedor, tenant, endpoint, origen, proxy, token, secreto o
   referencia de credencial como entrada.
6. No usa red, DNS, socket, transporte, B2, correo, Stripe, pagos o
   presentación externa.
7. No usa datos reales ni presenta una actuación ficticia ante una
   Administración.
8. Todos los flags de autorización, runtime y efecto son falsos; read-only,
   offline-only y review-only son verdaderos.
9. Un dry-run C8 nunca es E4 auténtica.
10. Canary live es exactamente cero; `1..5` no es aceptable en G0.
11. `assert_g0_live_activation_unavailable` falla incondicionalmente.
12. Preflight y smoke se invocan exclusivamente con `python -I -S -B`.
13. No extraen miembros al filesystem; sí pueden descomprimirlos y leerlos en
    memoria para verificar contenido, nombres y hashes.
14. Una auditoría satisfactoria conserva `ok=false`, `safe=false`,
    `production_safe=false`, `audit_ok=true`,
    `offline_review_reproduced=true`, `blockers` poblados y termina con exit
    `2`; `audit_ok` solo acredita la reproducción offline del NO-GO.
15. Exit `0` nunca significa aprobación y no es una salida satisfactoria de G0.
16. El manifest observado `RTM_CONNECT_POST_C8_G0_EVIDENCE.json` es
    `observed_unattested`: no es evidencia validada, firmada ni E4.

Estas reglas gobiernan los seis dominios y no crean un séptimo. Un pack futuro
requiere aprobaciones hash-bound y vigentes de security, operations, privacy,
legal/compliance, owner de proveedor/integración y service owner accountable:
`security_owner`, `operations_owner`, `privacy_owner`,
`legal_compliance_owner`, `provider_owner` y `service_owner`. La cadena mínima
es `CORE + requester + independent activator + independent verifier`,
materializada como `core_authorizer`, `requester`,
`independent_release_activator` e `independent_evidence_verifier`: se conserva
el doble control CORE de C8, no se admiten cuentas genéricas ni autoaprobación y
activador y verificador deben mantener independencia de funciones. Todo
artefacto y aprobación declara `approval_timestamp`, `expires_at`,
`revocation_status`, owner y `evidence_freshness`, con binding a commit y
artefacto; el menor vencimiento gobierna el pack, una revocación actúa
inmediatamente y cualquier estado ausente, stale, expirado, revocado o
desconocido mantiene NO-GO.

## Bloqueos que otro ADR deberá cerrar

- pack específico, tenant, protocolo y schemas de proveedor;
- egress default-deny, secretos por workload identity y supply chain firmada;
- idempotencia remota, fencing, lookup read-only y UNKNOWN;
- E4 auténtica con verificador independiente;
- rol DB runtime de mínimo privilegio y hashes canónicos recalculados en DB;
- privacidad, retención, base jurídica y custodia del activo de firma;
- inventario y cierre de todos los bypasses legacy de efectos externos;
- SLO, alertas, on-call, kill switch, restore y rollback remoto;
- canary legítimo, específicamente autorizado y sin expansión automática.

El inventario legacy es mínimo y no exhaustivo. Incluye además
`vehicle_removal_router.py` por su inicio potencial de Stripe Checkout,
`ops_operator_submit_router.py` por descarga arbitraria de `document_url` y
`force`, `dgt_test.py` por ejecutar un `POST` a nivel superior y `README.md` por
documentar cron desatendido/operación “sin humanos”. La omisión de otra
superficie no equivale a aprobación.

## Consecuencias

G0 produce una decisión comprobable sin crear una superficie de activación.
Su cierre documenta por qué producción no puede comenzar y qué deberá aportar
un futuro pack. El coste deliberado es que ninguna evidencia añadida a G0 puede
despejar el NO-GO: esa transición exige una revisión arquitectónica nueva.
Una reproducción correcta informa `audit_ok=true` únicamente para la revisión
offline y sigue siendo NO-GO con exit `2`.
