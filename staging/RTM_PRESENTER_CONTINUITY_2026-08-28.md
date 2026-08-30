# RTM de continuidad — Presentador y custodia documental

**Corte:** 28 de agosto de 2026
**Estado:** implementación sintética preparada para revisión; no operativa con
datos reales ni sedes externas.

## Addendum 30/08/2026 · puesto local de firma v1

Este addendum fija la primera frontera ejecutable del puesto de Ramón y
sustituye cualquier descripción anterior que confunda la cola del operador con
una capacidad de firma.

- Se añade el canal de cliente `signer_station` y el rol separado `rtm.signer`.
  Su conjunto de permisos debe ser exactamente `ops.view`,
  `presenter.signing.queue` y `presenter.signing.claim`. No hereda lectura
  general de documentos, alta, preparación, congelación, exportación ni envío.
- Una cuenta de operador no puede convertirse en firmante, ni una cuenta de
  firmante reutilizarse como operador, mediante la provisión sintética. Debe
  existir una cuenta distinta: `rtm-staging-signer-ramon@example.com`.
- La cola del puesto local no es global. Solo muestra tareas sintéticas de casos
  con binding A1-S, membership y asignación activa y aceptada para ese firmante.
  La futura cola central de todos los casos pagados exige un enrutador explícito
  y todavía no está implementada.
- `rtm_presenter_signer_station_v1_0` permite consultar la cola, tomar una tarea,
  recuperar la toma de la misma sesión y liberarla. La toma dura 30 minutos, se
  serializa con advisory lock y queda en el ledger append-only. Otra sesión solo
  ve que la tarea está ocupada; no recibe la identidad del firmante.
- La caducidad evita bloqueos permanentes. Una clave idempotente repite la misma
  toma activa, pero no puede resucitar una toma caducada o liberada. Una tarea
  con más de una toma activa se rechaza como historial inválido.
- La proyección local queda ligada a la huella del manifiesto, perfil y versión
  de destino, representación, hoja del trámite y cada documento individual con
  orden, campo, nombre, tipo, tamaño, versión y SHA-256.
- La toma no entrega bytes ni coordenadas de custodia. Tampoco abre navegador,
  crea una sesión de sede, lee certificado, firma, pulsa submit, usa red externa
  o produce efectos jurídicos.
- Se añade la superficie `/ops/presenter/signer`. El bearer permanece solo en
  memoria, la sesión exige posesión del dispositivo y la pantalla rechaza roles
  o permisos distintos del perfil mínimo de firmante.
- La UI muestra cola, toma/liberación, hoja preparada y documentos uno a uno.
  **Abrir sede** aparece deshabilitado con el texto «activación local pendiente»;
  no existe un botón de firmar o enviar.
- `rtm_presenter_delivery_v1_3` incorpora el modo de representación a la tarea
  sellada. La autorización sigue siendo un documento independiente cuando el
  perfil y el modo representante la exigen.
- La provisión de staging admite `--role signer`. El acceso al caso sintético se
  audita o aplica con `--access-kind signer` y la cuenta exacta anterior; usa una
  asignación `supervisor` sin ocupar el slot `reviewer` del operador. Ninguno de
  esos comandos se ha ejecutado en un entorno remoto en este corte.
- No hay cambio de esquema: las tomas se conservan como eventos inmutables en el
  ledger existente.
- Evidencia local: 128 pruebas de la familia Presenter, 34 pruebas de provisión
  y acceso sintético, 38 pruebas Node, 16 contratos frontend y build Vite, todo
  OK. La revisión React confirmó carga abortable, recuperación paralela, cierre
  de sesión al desmontar y defensa frente a respuestas tardías de otra sesión.
- Publicación verificada con árboles idénticos a los cortes locales: backend
  `374d60c2a9fc3cfcda52c000edeefbf71799fa00` y frontend
  `b53bd398dd2a74e44f2ebeb8c2877f16b80a9197`, ambos únicamente en
  `rtm-ops-controlled-delivery-2026-08-29`; `main` no se ha modificado.
- Estado externo: el frontend `b53bd398dd2a74e44f2ebeb8c2877f16b80a9197`
  figura `success` en los proyectos Vercel principal y staging. El backend
  `8d844d44e6ba5beeedddbaf4f92f376c06f82efa` se desplegó manualmente en
  Render mediante `dep-daa2jetg1s2s73bulor0` y quedó `Live` tras superar el
  preflight aislado y el control interno de salud. Siguen deshabilitados pagos
  finales, correo saliente y presentación externa. No se han creado cuentas ni
  asignaciones remotas, no se ha abierto REG y no se ha realizado ninguna
  presentación.
- Siguiente corte real: cliente instalado y atestado en el PC de Ramón, adaptador
  REG, apertura local de la sede, tickets de bytes por documento y parada en el
  paso final. La elección de certificado, firma y submit seguirán siendo humanos;
  después se capturará el justificante como evidencia candidata.

## Addendum 30/08/2026 · preparación por operador y cola local de firma

Este addendum sustituye cualquier descripción incompatible del canal portal y
resuelve la frontera entre el trabajo del operador y el certificado de la
empresa.

- El operador ya no recibe un botón para abrir la sede. Completa dentro de OPS
  la hoja exacta definida por el perfil (`Asunto`, `Expone`, `Solicita` en la
  fixture REG), elige las versiones documentales y realiza cinco comprobaciones.
- En portal se fija un manifiesto interno de versiones y huellas. No se crea un
  ZIP, una carpeta ni un archivo compuesto: los documentos permanecen separados
  y el futuro puente los entregará uno a uno cuando la sede muestre cada casilla.
- La preparación crea el estado `awaiting_signature`, con
  `authoritative_submission=false`. La interfaz lo rotula **EN COLA · NO
  PRESENTADO** y nunca lo confunde con un envío.
- Se añade `rtm_presenter_signature_queue_v1_0`. Solo devuelve tareas de casos
  sintéticos con binding, membership y asignación activa para la cuenta. La UI
  permite cambiar entre esos expedientes sin cerrar la sesión individual de OPS.
- El certificado y su clave privada no se almacenarán en Render, RTM, B2,
  variables de entorno ni terminales de operador. La sesión autenticada de REG
  tampoco se comparte con operadores y no se habilita escritorio remoto.
- El diseño del puesto firmante es local: abre la sede, rellena los pasos
  previos, solicita tickets de un solo uso para cada documento y se detiene en
  la revisión/firma final. Firma, certificado, Cl@ve, CAPTCHA y submit siguen
  siendo humanos.
- La cola transversal ya se registra y consulta. La activación del puesto local
  y el adaptador REG siguen bloqueados en staging; el botón de apertura aparece
  deshabilitado y no se han entregado bytes.
- Después de la firma, el justificante seguirá entrando como evidencia
  candidata. Solo su conciliación y verificación podrán activar seguimiento; no
  se inferirá una presentación desde el clic, la cola o la ausencia de error.
- La UI no muestra las acciones de captura de justificante mientras la tarea
  siga `awaiting_signature`; solo podrán aparecer desde `awaiting_receipt` o
  `completed`, evitando pedir un justificante antes de presentar.
- Perfil sintético actualizado de v3 a v4 de forma append-only e idempotente. La
  v4 añade la hoja de preparación y el modo representante. En ese modo, el
  perfil exige vincular una autorización PDF independiente antes de fijar la
  tarea; las versiones históricas 1, 2 y 3 se conservan exactamente.
- Contrato vigente: `rtm_presenter_delivery_v1_3`, puesto local
  `rtm_presenter_signer_station_v1_0` y
  `staging/RTM_CONTROLLED_DELIVERY_CONTRACT_2026-08-29.md`.
- Evidencia local de este incremento: 120 pruebas backend Presenter, 10 pruebas
  de la fixture append-only, 34 pruebas Node del modelo/API, 15 contratos
  frontend y build Vite, todos OK.
- Estado de publicación: cambios locales sobre backend `3681617` y frontend
  `7684543`; no se han publicado, desplegado ni aplicado fixtures remotas.

## Addendum 30/08/2026 · directorio administrativo DIR3/SIR

Este addendum incorpora una capa real de identificación administrativa sin
convertirla en un catálogo ficticio de sedes o procedimientos.

- Se añade un compilador offline y un snapshot cerrado de cinco listados DIR3,
  SIR y catálogos territoriales aportados para este corte. No usa red, base de
  datos ni almacenamiento documental.
- El snapshot contiene 35.841 organismos/unidades, 32.705 con oficina presente
  en el listado SIR, y queda ligado a los hashes de cada fuente y a un hash
  canónico propio.
- La fecha `30/06/2026` corresponde a la modificación mostrada en la página
  oficial de descargas para el listado SIR. Es una referencia congelada, no una
  afirmación de vigencia permanente.
- La búsqueda admite nombre, localidad, DIR3 y alias acotados. Se han comprobado
  Manresa, la DGT central y las Jefaturas de Barcelona, Lleida y Badajoz con sus
  códigos DIR3 y oficinas SIR de referencia.
- Los resultados se muestran en un bloque **Directorio administrativo** separado
  del selector. Todos llevan `reference_only=true` y
  `usable_as_destination=false`; no pueden abrir sede, adjuntar ni presentar.
- La presencia en DIR3 identifica; la presencia en SIR indica constancia en el
  snapshot y permite tratar la unidad como candidata para remisión por REG;
  ninguna decide por sí sola competencia, vigencia o procedimiento.
- El REG sí es una vía general práctica: permite dirigir escritos a órganos AGE
  y a CCAA/EELL integradas en SIR cuando no exista procedimiento electrónico o
  formulario normalizado. Si hay vía especial, el REG puede rechazar el escrito.
- Arquitectura acordada para el siguiente incremento: un perfil verificado
  único `REG — escrito general`, con selección dinámica de la unidad DIR3/SIR y
  comprobación de vigencia al abrir, en vez de crear miles de perfiles idénticos.
- El portal REG real observado ordena la actuación en cuatro pasos: datos del
  solicitante, datos de la solicitud, documentación y firma. Si se actúa como
  representante exige una solicitud por cada interesado; RTM no permitirá
  mezclar varios clientes en un registro.
- La documentación del REG se atenderá archivo a archivo desde el contenedor.
  La firma y el envío final seguirán siendo humanos, y el justificante posterior
  deberá capturarse y conciliarse antes de activar plazos.
- La captura real utilizada para comprender el recorrido contiene datos
  identificativos y no se incorpora al código, al snapshot ni a las fixtures.
- Solo un perfil RTM activo, con origen y procedimiento exactos y doble
  verificación, podrá ser seleccionable. Ese catálogo práctico de trámites DGT,
  REG y ayuntamientos continúa pendiente.
- DEHú se mantiene separado: sirve para notificaciones/comunicaciones entrantes
  de organismos integrados y no se tratará como canal universal de envío.
- Si el directorio reconoce el organismo pero falta el procedimiento, la UI lo
  dice expresamente y conserva la propuesta de enlace pendiente de revisión
  independiente; no abre ni activa la URL.
- El frontend aplica una segunda allowlist y rechaza resultados con correos,
  URLs de trámite, estados seleccionables o campos fuera de contrato.
- Contrato detallado:
  `staging/RTM_PRESENTER_DIRECTORY_CONTRACT_2026-08-30.md`.
- Evidencia: 115 pruebas backend Presenter, 26 pruebas de scripts staging, 15
  contratos frontend, 22 pruebas del modelo/API y build Vite, todos OK. El
  navegador cloud no pudo alcanzar `localhost`; no se declara verificación E2E
  visual.
- Este incremento permanece local: no se ha publicado, desplegado, migrado ni
  abierto ninguna sede.

## Addendum 30/08/2026 · flujo híbrido, salida de búsqueda y justificantes

Este addendum sustituye cualquier descripción incompatible del paquete único,
del alta documental o del justificante.

- En sede o portal no se congela un paquete previo. El expediente permanece
  como documentos individuales y la extensión pedirá uno desde RTM cuando la
  sede muestre cada control «Elegir archivo».
- RTM Correspondencia sí fija una selección concreta antes de preparar el
  borrador, porque destinatario, texto y adjuntos deben quedar ligados a sus
  versiones y huellas.
- El alta documental pasa a un diálogo inmediato junto al contenedor. Usa un
  nombre libre reconocible para el operador, un tipo interno controlado y un
  nombre seguro de adjunto; conserva además el nombre original, versión y
  SHA-256.
- `submission_receipt` queda excluido de toda selección de salida. El perfil
  sintético v3 ya no lo declara como campo solicitado; continúa custodiado en
  una zona separada de evidencias posteriores.
- La búsqueda sin coincidencias ya no vacía el recorrido. Mantiene visibles los
  perfiles sintéticos compatibles y ofrece un botón directo para continuar la
  prueba, sin presentarlos como DGT o ayuntamientos reales.
- Una búsqueda de sede sin coincidencias permite proponer un enlace sintético.
  La propuesta queda pendiente de verificación independiente, no abre la URL,
  no crea un perfil y no autoriza una presentación.
- Para multas, el Centro de destinos se organizará por organismo sancionador y
  procedimiento. La entrada específica «DGT — Alegaciones y recursos» será
  única y resolverá por número de expediente; la Jefatura territorial solo se
  pedirá cuando la notificación o un canal genérico la hagan necesaria.
- El catálogo real de DGT, organismos y ayuntamientos sigue pendiente de alta,
  revisión y verificación. No se permite sustituirlo por nombres o URL que den
  una falsa apariencia de destino verificado.
- La captura de justificante sintético existe como evidencia candidata. Nunca
  acredita por sí sola presentación, fecha, plazo o recepción hasta que sea
  conciliada y verificada.
- Estado local de este corte: cambios sin publicar ni desplegar sobre backend
  `8c12b734d6ad599696b6d901c542c89840df9ff6` y frontend
  `ee0ab3665ce690900ff412429986f9173d8a258c`.
- Evidencia focal: 24 pruebas backend, 27 contratos frontend, 44 pruebas
  Presenter/extensión y build Vite, todos OK. La verificación visual automática
  local continúa no disponible en este entorno y no se declara como ejecutada.

## Addendum 29/08/2026 · contenedor primero y RTM Correspondencia

Este addendum sustituye cualquier afirmación anterior incompatible sobre el
canal de correo o el estado de publicación.

- Rama común: `rtm-ops-controlled-delivery-2026-08-29`.
- Base remota ya igualada en el único PC:
  - backend `a44ec0d79be4d60bf4ec2720225672fbe44b188a`;
  - frontend `4fe4ef042621682e5111771c350bd0f8acbe6dc1`.
- La UI se reorganiza como: contenedor documental → canal → Centro de destinos
  → selección ordenada → paquete fijo → preparación de salida.
- El segundo canal pasa a llamarse **RTM Correspondencia**. Resuelve entidad,
  papel, materias, canal oficial, fuente y alternativa probatoria antes de
  ofrecer destinatario y plantilla.
- Se admite una dirección manual únicamente como pendiente de verificación; en
  este staging solo puede usar dominios reservados sintéticos.
- El operador revisa asunto, cuerpo y seis confirmaciones obligatorias. La orden
  auditada liga el texto exacto a versiones y huellas de los adjuntos.
- El remitente previsto queda fijado en `info@recurretumulta.eu`, pero no se abre
  SMTP ni se envía mensaje en este corte.
- `Message-ID`, respuesta SMTP, aceptación, rebotes, respuesta de la empresa y
  número de reclamación nacen vacíos. La aceptación SMTP no equivale a recepción
  acreditada.
- Perfiles con formulario obligatorio o alternativa preferente no habilitan el
  canal email. Los adjuntos sensibles pueden exigir cifrado o enlace seguro.
- Contrato actualizado: `rtm_presenter_delivery_v1_1`.
- Evidencia local de este addendum: 112 pruebas focales backend, 43 frontend y
  build Vite, todos OK. La comprobación visual automática no se ejecutó porque
  el binario `agent-browser` no está disponible en este entorno; no se presenta
  como evidencia obtenida.
- Estos cambios de Correspondencia aún no están publicados ni desplegados. El
  despliegue anterior continúa siendo sintético y sin efectos externos.

## Addendum 29/08/2026 · entrega controlada y Documento 2

- Rama de trabajo: `rtm-ops-controlled-delivery-2026-08-29`.
- Se incorporó inicialmente `rtm_presenter_delivery_v1_0`: preparación idempotente y
  auditada de entrega desde un paquete congelado, sin leer bytes ni producir
  efectos externos.
- La sede se busca dentro de un registro de perfiles activos y verificados; el
  operador no puede introducir una URL libre.
- El paquete se presenta documento a documento en el orden propio del perfil.
- El canal email inicial quedó modelado sin ejecución; su contrato vigente es el
  de RTM Correspondencia descrito en el addendum superior.
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

RTM dispone de un flujo aislado de **Presentador**. Para sede mantiene las
versiones como documentos sueltos y prevé entregarlas una a una en el orden que
la propia sede solicite. Para Correspondencia fija una selección auditada. El
operador no recibe originales, ZIP, URL presignada ni coordenadas del
almacenamiento.

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
