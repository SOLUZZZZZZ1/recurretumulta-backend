# RTM de continuidad — Presentador y custodia documental

**Corte:** 28 de agosto de 2026
**Estado:** implementación sintética preparada para revisión; no operativa con
datos reales ni sedes externas.

## Addendum 29/08/2026 · entrega controlada y Documento 2

- Rama de trabajo: `rtm-ops-controlled-delivery-2026-08-29`.
- Se incorpora `rtm_presenter_delivery_v1_0`: preparación idempotente y
  auditada de entrega desde un paquete congelado, sin leer bytes ni producir
  efectos externos.
- La sede se busca dentro de un registro de perfiles activos y verificados; el
  operador no puede introducir una URL o dirección de correo libre.
- El paquete se presenta documento a documento en el orden propio del perfil.
- El canal email queda modelado, pero solo acepta destinatario y plantilla
  verificados y no ejecuta envío en este corte.
- Se añade el permiso mínimo `presenter.delivery.prepare`; no concede ejecución
  externa.
- Se añade la finalidad documental `prejudicial_authorization` y la preferencia
  opcional de toma de datos `prejudicial_counsel_requested`; no constituyen por
  sí solas autorización o mandato.
- Contrato detallado: `staging/RTM_CONTROLLED_DELIVERY_CONTRACT_2026-08-29.md`.
- Continúan cerrados: puente remoto sin atestación, correo real, datos reales,
  scanner/CDR, firma, Cl@ve, CAPTCHA, submit final y reintento tras resultado
  incierto.

## Resultado de este corte

RTM dispone de un flujo aislado de **Presentador** para preparar, en el orden
exigido por cada sede, un paquete formado por versiones concretas de los
documentos de un expediente. El operador no recibe originales, ZIP, URL
presignada ni coordenadas del almacenamiento.

También se ha añadido la entrada de un archivo elaborado fuera de RTM. Puede
incorporarse como documento lógico nuevo o como versión sucesora de la última
versión de un documento existente. El backend calcula su hash, conserva la
traza y lo deja siempre en `review/pending`. No puede seleccionarse ni congelarse
en un paquete hasta que exista un resultado real de scanner/CDR y una activación
autorizada; esa segunda parte todavía no está implementada.

No se ha habilitado una presentación real, no se ha aplicado una migración a una
base compartida, no se ha cargado una fixture en staging remoto y no se ha
desplegado esta rama en Vercel/Render.

## Decisiones vinculantes

1. El expediente RTM es la fuente de verdad. No se mantienen carpetas de trabajo
   permanentes en terminales de operadores.
2. La UI de operador trabaja con metadatos, hashes e identificadores de versión;
   no contiene controles de descarga, preview, ZIP o exportación.
3. Un paquete congela destino, origen exacto, representación, orden, slot,
   versión documental, nombre de portal, tamaño, tipo y SHA-256.
4. Mejorar un recurso crea otra versión. No se sustituye en silencio una versión
   congelada ni se versiona desde un predecesor que ya no sea el último absoluto.
   Desde que existe cualquier sucesora, incluso `review/pending`, la versión
   anterior deja de ser elegible para congelar o entregar.
5. Presenter exige sesión individual y posesión del dispositivo asociado. El
   bearer y el digest del dispositivo se verifican en la misma transacción que
   autoriza el expediente y ejecuta la operación.
6. El PIN compartido de OPS no autoriza Presenter ni la entrada documental.
7. La exportación de operador permanece denegada. La excepción administrativa
   futura seguirá siendo un canal distinto, temporal, motivado, con doble control,
   reautenticación y marcado; hoy está cerrada incluso para admin.
8. La firma, Cl@ve, PIN, certificado, CAPTCHA y el botón final de registro
   pertenecen a la persona en la sede. RTM no los automatiza.
9. El MVP continúa limitado a `staging`, caso A1-S sintético, sin datos reales,
   sin efectos externos y sin acceso directo al almacenamiento.
10. Estos controles reducen exposición y copias accidentales; no constituyen una
    certificación RGPD ni garantizan impedir capturas en un dispositivo no
    administrado.

## Repositorios y ramas

| Componente | Repositorio | Rama de trabajo | Base vinculante |
|---|---|---|---|
| Backend | `SOLUZZZZZZ1/recurretumulta-backend` | `rtm-presenter-no-export-2026-08-28` | `rtm-hardening-presented-authority-settlement-2026-08-28` · `501e049153061e4e4955402de9f51a7a05d47a5b` |
| Frontend | `SOLUZZZZZZ1/recurretumulta-frontendweb3-8-26` | `rtm-presenter-no-export-2026-08-28` | `rtm-frontend-case-capability-2026-08-28` · `02882580de940804deda17699ce244fd2b1696bc` |

Producción/main no se ha modificado. La rama frontend local se creó sobre un
commit con árbol equivalente y debe publicarse rebasada sobre el commit de base
vinculante indicado arriba.

### Estado de publicación

- Frontend publicado como PR en borrador:
  `https://github.com/SOLUZZZZZZ1/recurretumulta-frontendweb3-8-26/pull/2`,
  commit remoto `e08ac2b67d1b28c9780b5cbba29d5125f0081376`.
- Backend permanece en el commit local de la rama indicada. El push por Git no
  dispone de credenciales y la integración GitHub devuelve `403 Resource not
  accessible by integration` al crear blobs en
  `SOLUZZZZZZ1/recurretumulta-backend`. Para publicar sin reconstruir trabajo,
  habilitar en ese repositorio permiso **Contents: read and write** para la
  integración y reanudar desde esta rama; no se ha creado una rama backend
  remota parcial.

## Implementado en backend

- Contratos inmutables y versionados de documento, perfil de destino, paquete,
  item, ticket, auditoría y exportación excepcional cerrada.
- Schema/migración Presenter versionado y comprobación runtime fail-closed de
  tablas, columnas, tipos, índices, constraints, triggers y funciones.
- Scope exacto por caso: caso sintético, binding A1-S activo, tenant y membership
  sintéticos, y asignación activa, aceptada y no liberada del operador.
- Permisos separados:
  - `presenter.documents.read`
  - `presenter.documents.ingest`
  - `presenter.package.freeze`
  - handoff/export separados y cerrados.
- Sesión individual ligada a dispositivo mediante cookie/header; no se envía el
  secreto bruto a SQL y no basta con robar el bearer.
- Paquetes congelados con manifiesto y hash canónicos; creación idempotente con
  `Idempotency-Key` y serialización transaccional de linajes.
- Handoff modelado con tickets opacos, de un solo uso, ligados a actor, sesión,
  extensión, origen, paquete, item y caducidad. El router remoto no puede emitir
  bytes porque no dispone de atestación gestionada.
- Rutas legacy de documentos OPS reducidas a metadatos y descarga de operador
  denegada. La antigua entrada externa compartida devuelve `410` y no lee el
  cuerpo; indica el endpoint Presenter individual.
- Entrada externa Presenter multipart:
  - permiso dedicado y confirmación sintética literal;
  - PDF, DOCX, JPEG o PNG; máximo 25 MiB;
  - nombre, extensión, MIME y firma de contenido coherentes;
  - controles estructurales básicos para DOCX y límites de expansión;
  - hash calculado por backend;
  - objeto B2 y filas DB coordinados con cleanup ante fallo de PUT, endpoint,
    transacción o commit;
  - nuevo linaje o sucesor de la última versión absoluta;
  - invalidación inmediata de versiones anteriores para freeze, ticket y bytes,
    aunque la sucesora continúe pendiente;
  - estado obligatorio `review/pending`, `external_revision`, no elegible;
  - auditoría sin bucket, key, URL ni contenido.
- Proyección pública/cliente existente endurecida para resolver descargas por
  `case_id + document_id + case capability` en servidor, con TTL corto y sin
  revelar bucket/key. Los documentos `external_revision` de Presenter quedan
  excluidos de esa capacidad. La capacidad pública no concede autoridad a OPS.
- Provisionamiento sintético endurecido para roles/permisos Presenter, con
  locks, JSONB tipado, invalidación de sesiones y protección frente a mutadores
  concurrentes/no sintéticos.

## Implementado en frontend

- Ruta aislada `/ops/case/:caseId/presenter` con autenticación individual.
- Bearer conservado únicamente en memoria; el secreto de dispositivo viaja en
  cookie segura de la sesión y las llamadas usan `no-store`.
- Workspace que rechaza respuestas si exponen referencias de almacenamiento o
  habilitan download, preview, ZIP u handoff de operador.
- Selección de sede/perfil, modo interesado/representante y documentos en el
  orden exacto de los campos verificados del destino.
- Solo se ofrecen para un paquete versiones `active/clean` compatibles con
  purpose, MIME, tamaño y slot, y únicamente si son la última versión absoluta
  de su linaje.
- Congelación idempotente y comprobación de que la respuesta contiene el mismo
  expediente, origen, representación, selección y manifiesto solicitados.
- Panel de documento externo visible solo con
  `presenter.documents.ingest`:
  - documento nuevo o nueva versión;
  - solo última versión absoluta como predecesora;
  - allowlist de purpose y archivo alineada con backend;
  - confirmación sintética obligatoria;
  - sin preview, descarga, Blob URL ni persistencia propia del `File`;
  - limpieza del input/ref y recarga del contenedor tras custodiar;
  - mensaje explícito `pending`, no seleccionable.
- El detalle OPS ya no contiene el formulario legacy ni botones de descarga/ZIP;
  enlaza a Presenter cuando el expediente declara la capacidad.
- Prototipo de extensión MV3 limitado a hosts locales sintéticos, sin permisos
  `downloads`, `storage`, `cookies`, `tabs`, `activeTab` o `debugger`; no firma,
  no resuelve CAPTCHA y no pulsa enviar.

## Evidencia ejecutada

| Superficie | Resultado |
|---|---|
| Backend Presenter/auth/provisioning/scripts | 151 pruebas focales en el pase final: OK; incluyen kill-switch de autenticación, barrera atómica de dispositivo, ingreso externo, invalidación de versión y cleanup. |
| Compilación Python y `git diff --check` backend | OK. |
| Frontend Presenter/no-export | 22 pruebas Python: OK. |
| Modelo/API Presenter frontend | 19/19: OK. |
| Extensión sintética | 17/17: OK. |
| Build Vite | OK. Advertencias no bloqueantes: chunk principal grande y bases de compatibilidad desactualizadas. |
| Suite Python frontend completa | 198/204. Las 6 fallas son hashes/contratos históricos congelados anteriores y no pertenecen a Presenter/no-export; no se ha falsificado esa evidencia para hacerla pasar. |
| Verificación visual local | No obtenida: el navegador cloud no alcanzó los servidores locales por el bridge. Se conserva como gate pendiente; no se sustituye por una afirmación de E2E. |

Las pruebas son unitarias/contractuales y usan datos sintéticos. No prueban una
base staging migrada, B2 staging real, un scanner real, una sede externa ni la
presentación jurídica completa.

## Estado operativo y bloqueos

| Área | Estado | Bloqueo para abrir |
|---|---|---|
| Contenedor, selección y paquete congelado | Código listo para revisión sintética | Aplicar schema/fixture en staging aislado y verificar DB-backed E2E. |
| Entrada de documento externo | Código listo, resultado siempre `review/pending` | Integrar scanner/CDR real, recibo verificable, política de activación/rechazo, reintentos e idempotencia de ingreso. |
| Límite de subida | Aplicación limita 25 MiB + lectura acotada | Configurar también proxy/ASGI, temporales cifrados/acotados y prueba `chunked`; el parser puede recibir/spool antes del handler. |
| Adjuntar en DGT/ayuntamiento | Cerrado | Extensión gestionada y firmada, atestación verificable, perfiles exactos por sede, frame/popup/origin, rate limit, kill switch y pruebas ofensivas. |
| Justificante y resultado | No implementado | Captura controlada, conciliación por hash y `outcome_unknown` sin retry automático. |
| Exportación admin | Cerrada | Workflow JIT con dos personas, grant individual, step-up, watermark, single-use, purga y auditoría. |
| Datos reales / RGPD | Prohibidos | DPD/asesoría: base, información, encargados, transferencias, conservación, derechos, medidas laborales y decisión EIPD; además de todos los gates técnicos aplicables. |
| Garantía fuerte anti-copia | No prometida | Dispositivo administrado o VDI, DLP/EDR/PAM, control de impresión/USB/portapapeles y evaluación proporcional. |
| Credencial compartida OPS legacy | Aún existe fuera de Presenter | Migrar las demás superficies privadas a identidad individual antes de declarar retirada completa. |
| Storage legacy fuera de Presenter | Algunas rutas históricas de análisis/carga/partner todavía construyen o devuelven `bucket/key` | Inventariar consumidores y migrarlas a identificadores/capabilities antes de un `GO` global de privacidad; no forman parte del canal Presenter revisado. |

### Bloqueo específico de despliegue frontend

`vercel.json` reescribe actualmente `/api` a
`https://recurretumulta-backend-1.onrender.com`. No se ha demostrado que sea un
backend de staging aislado. Por ello **no debe desplegarse esta rama de Presenter**
hasta reemplazar ese destino por un backend staging identificado, con base y B2
separados, variables fail-closed y datos exclusivamente sintéticos.

La preview facilitada antes de este corte corresponde a la rama anterior de
capacidad de expediente; no demuestra el flujo Presenter de esta rama.

## Secuencia segura para reanudar

1. Revisar y aprobar los PR de backend/frontend sin mezclar cambios de main.
2. Crear o identificar un backend staging aislado y fijar base Postgres/B2
   exclusivas; confirmar por evidencia que no contienen datos reales.
3. Configurar flags Presenter fail-closed y límites de body en proxy/ASGI.
4. Ejecutar la migración en `--dry-run`; revisar identidad de DB y digest del
   contrato; después aplicar de forma explícita solo en staging.
5. Ejecutar la fixture sintética primero en dry-run y luego con confirmación
   literal; verificar idempotencia y ausencia de red/B2 en la fixture.
6. Conectar el frontend exclusivamente a ese backend staging y desplegar una
   preview nueva de las ramas revisadas.
7. Probar en navegador: login individual + dispositivo, scope/IDOR, carga
   externa, fallo y cleanup B2, carrera de versiones, pending no seleccionable,
   paquete idempotente y ausencia de descarga/ZIP/refs.
8. Integrar scanner/CDR con recibo verificable y transición transaccional a
   `active/clean` o rechazo; repetir pruebas adversarias antes de permitir que
   un externo aparezca como candidato.
9. Implementar y probar perfiles de destino y la extensión gestionada sin
   ampliar hosts de forma genérica. Mantener bytes remotos cerrados hasta
   disponer de atestación real.
10. Añadir justificante/conciliación y manejo `outcome_unknown` sin reintento
    automático.
11. Completar revisión de seguridad, DPD/asesoría y gates del documento
    `RTM_PRESENTER_SECURITY_AND_PRIVACY.md` antes de plantear datos reales.

## Prohibiciones al reanudar

- No usar documentos de clientes ni copiar una muestra real “para probar”.
- No desplegar con el rewrite actual sin identificar el backend destino.
- No cambiar `review/pending` a `active/clean` manualmente para saltar el scanner.
- No abrir el handoff remoto mediante un header autodeclarado de extensión.
- No reintroducir descarga, ZIP, URLs presignadas generales o bucket/key en OPS.
- No declarar cumplimiento RGPD, E2E real o no-export absoluto basándose solo en
  estos tests.
- No automatizar firma, CAPTCHA, Cl@ve, botón final o reintento de presentación.

## Archivos guía

- `staging/RTM_PRESENTER_SECURITY_AND_PRIVACY.md`
- `scripts/rtm_staging_presenter_schema.py`
- `scripts/rtm_staging_presenter_synthetic_fixture.py`
- `rtm_presenter_contracts.py`
- `rtm_presenter_policy.py`
- `rtm_presenter_router.py`
- `rtm_presenter_service.py`
- frontend `src/rtm-presenter/`
- frontend `rtm-presenter-extension/`

Este documento no contiene secretos, credenciales, coordenadas B2 ni datos
personales. Debe actualizarse cuando cambie un gate, se aplique una migración o
se obtenga evidencia operativa nueva.
