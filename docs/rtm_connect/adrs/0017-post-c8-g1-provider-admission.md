# ADR-0017 · G1 como admisión offline de proveedor

**Estado:** Aceptado como revisión estática; producción real NO-GO.

## Contexto

G0 congeló seis dominios bloqueados y exigió un pack específico y versionado
de proveedor antes de cualquier revisión productiva. El overlay G0 fue
materializado en el commit declarado
`eedd521ecf1703c9b5e20196651da04557900e74` y entregado como ZIP con SHA-256
`8d69d66573d92b675be26d391c1d03a74ff62a514bdf369dfce817db396ba3f3`.

El repositorio ya contenía tres superficies que podían inducir a error:
`dgt_client.py`, `submitter_dgt.py` y `submitters/registro.py`. Ninguna acredita
un proveedor productivo. Adoptar una por similitud nominal rompería la
frontera de autoridad y convertiría placeholders o adaptadores genéricos en
una supuesta integración homologada.

## Decisión

Se crea G1 como unidad offline, aditiva y no cableada. G1:

1. liga su revisión a la base G0 exacta;
2. conserva la decisión G0 `blocked/no_go`;
3. clasifica y rechaza los tres candidatos legacy;
4. define las catorce secciones mínimas de un dossier futuro;
5. no acepta un dossier aportado por el llamante ni contiene transición GO;
6. mantiene `live_canary_percent=0` y todos los permisos productivos falsos.

La coincidencia entre SHA-256 externo, comentario del ZIP y commit declarado
congela la identidad de entrega, no la autoría ni el objeto commit Git. Supply
chain, firma, SBOM y provenance siguen sin atestar.

## Reglas

1. G1 no es C9 y no modifica C0–C8 ni G0.
2. No modifica `app.py`, `rtm_connect/__init__.py`, routers, conectores,
   schemas, workflows, cron o configuración de Render.
3. No publica rutas, workers, schedulers, webhooks o polling.
4. No usa red, DNS, sockets, proxies, secretos, B2, correo, Stripe o pagos.
5. No abre base de datos ni crea DDL/DML.
6. No usa datos reales ni contacta DGT, REG, SIR, GEISER o intermediarios.
7. Los tres candidatos tienen `status=rejected`,
   `provider_specific=false` y `production_eligible=false`.
8. No se admite selección de proveedor ni pack inyectable en G1.
9. Un PDF o referencia no constituye E4 sin verificador auténtico e
   independiente.
10. UNKNOWN no admite reintento ciego; el dossier futuro debe aportar lookup
    read-only, fencing y reconciliación.
11. G0 permanece NO-GO y no puede ser sobrescrito.
12. `assert_g1_live_activation_unavailable` falla incondicionalmente.
13. Preflight y smoke se invocan con `python -I -S -B` y solo aceptan
    `--archive` y `--compact`.
14. Los scripts leen miembros del ZIP en memoria, pero nunca los extraen.
15. Una revisión satisfactoria conserva `ok=false`, `safe=false`,
    `production_safe=false`, `audit_ok=true`,
    `offline_review_reproduced=true` y exit `2`.
16. Exit `0` nunca representa aprobación.

## Consecuencias

G1 elimina la ambigüedad operativa sobre los adaptadores legacy y deja un
contrato verificable para solicitar la información correcta a un proveedor.
No reduce los bloqueos de seguridad, operaciones, privacidad, canary o rollback
de G0. El siguiente paso es un G2 específico para un proveedor real, con otro
ADR y revisión independiente. Hasta entonces producción permanece NO-GO.
