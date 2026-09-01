# RTM Presenter — requisitos de seguridad y protección de datos

## Estado, alcance y advertencia

Este documento convierte el requisito de presentación humana desde un
expediente RTM en controles técnicos y organizativos verificables. Su objetivo
es reducir copias locales, limitar la extracción por operadores y mantener una
traza fiable de qué versión documental se presentó, ante qué destino y por qué
persona autorizada.

Este documento **no certifica cumplimiento del RGPD ni de ninguna otra norma**,
no determina por sí solo una base jurídica y no sustituye una evaluación de
protección de datos. Es una
mejora técnica propuesta. Antes de usar datos reales deberán validarse con el
DPD y la asesoría, como mínimo, finalidades, legitimación, información a las
personas, destinatarios, encargados y transferencias, plazos de conservación,
derechos, medidas laborales y la necesidad de una EIPD.

La ubicación de este documento en `staging/` no habilita datos reales en
staging. Continúan vigentes la matriz de entorno y la exigencia de fixtures
sintéticas. Ningún control descrito aquí constituye por sí solo un `GO` de
producción.

### Estado implementado a 28 de agosto de 2026

El MVP está cerrado por defecto y, cuando se habilita, solo admite `staging`,
expedientes sintéticos, sin datos reales, efectos externos ni acceso directo al
almacenamiento. El estado efectivo es:

Todas las rutas Presenter exigen simultáneamente el bearer de una sesión
individual y la prueba del dispositivo asociado (cookie o header cuyo secreto
solo se compara como SHA-256). Esa prueba, el estado de la sesión y el acceso
al expediente se resuelven usando la misma transacción que ejecuta la acción;
el PIN compartido de OPS no concede acceso a Presenter.

| Superficie | Autorización contractual | Estado actual |
|---|---|---|
| Consultar metadatos/versiones del expediente | `presenter.documents.read` | Disponible solo en el MVP sintético; no entrega bytes, preview, URL ni referencia de almacenamiento. |
| Incorporar un documento externo o una nueva versión | `presenter.documents.ingest` | Disponible solo en el MVP sintético mediante sesión individual. Queda bajo custodia RTM como `review/pending`; sin evidencia real de análisis no es elegible para un paquete. |
| Congelar un paquete inmutable | `presenter.package.freeze` | Disponible solo en el MVP sintético; congela selección, versiones y hashes. |
| Entregar documentos a una sede mediante extensión | No concedida al operador actual | **Cerrado**: el router no produce una atestación gestionada y, por tanto, no libera bytes al puente remoto. |
| Exportación administrativa excepcional | rol exacto `rtm.admin` **y** permiso independiente `ops.documents.export_exceptional` | **Cerrado**: además exige concesión individual, motivo, evento de reautenticación posterior al login y no más antiguo de cinco minutos; el router actual no carga esa concesión ni dispone de motor de marcado. |

No está operativa una presentación real ante DGT, ayuntamientos u otras sedes.
La extensión local sintética puede servir como prototipo de interfaz, pero no
equivale a un puente remoto autorizado ni produce efectos externos.
Los apartados siguientes distinguen controles ya aplicados de requisitos para
abrir en el futuro el puente remoto, la exportación excepcional o datos reales.
Un criterio de aceptación sin marcar es trabajo pendiente, no evidencia de que
el control ya exista.

Tampoco debe confundirse el modelo versionado de Presenter con una migración ya
terminada: la incorporación de todos los documentos legacy del expediente, los
perfiles de destino verificados y la conciliación del justificante requieren
integraciones adicionales antes de un flujo completo.

## Decisión de diseño

El expediente es la fuente de verdad. Los documentos no se organizan en
carpetas permanentes fuera de RTM para poder presentarlos. Cada intento crea una
**sesión de presentación** con una instantánea inmutable de:

- tenant y expediente;
- destino, procedimiento y origen web exacto autorizados;
- condición de la persona presentadora y evidencia de representación aplicable;
- slots documentales requeridos;
- identificador, versión, nombre seguro, tipo, tamaño y SHA-256 de cada archivo;
- actor solicitante, aprobaciones, caducidad y estado;
- resultado, referencia y justificante cuando existan.

Una mejora de un recurso crea una versión nueva; nunca sustituye en silencio la
versión ya congelada. Cambiar cualquier documento, destino, representación o
procedimiento invalida las aprobaciones y capacidades pendientes.

La sesión de una sede no es un borrador RTM. Si REG caduca o descarta el
formulario, RTM conserva la instantánea anterior, pero no su cookie, credencial
ni material de sesión. La recuperación exige autenticación humana nueva y una
comparación exacta de la huella de tarea antes de reconstruir campos y mapa
documental. Nunca se reintenta automáticamente una firma o presentación cuyo
resultado sea incierto.

Un archivo elaborado fuera de RTM entra una sola vez mediante el ingreso
documental del expediente, con identidad individual, hash calculado por el
backend y procedencia auditada. Puede registrarse como documento lógico nuevo o
como sucesor exacto de una versión existente. Subirlo no lo convierte en apto
para presentar: hasta disponer de un resultado real y verificable de
antimalware/CDR permanece en revisión y no puede seleccionarse al congelar un
paquete. La existencia de la sucesora invalida inmediatamente la elegibilidad de
las versiones anteriores de ese linaje, también mientras siga `review/pending`,
para evitar una presentación obsoleta. La ruta legacy con token OPS compartido
no es un canal admisible para este ingreso y la capacidad pública de descarga no
resuelve documentos `external_revision`.

El operador ya puede seleccionar metadatos y congelar un paquete en el MVP
sintético. La entrega de bytes desde RTM mediante un puente controlado es el
objetivo, pero el puente remoto permanece cerrado. Si llega a habilitarse, la
firma, el certificado, el PIN, Cl@ve, CAPTCHA y la decisión final de registrar
permanecerán bajo control humano y fuera del backend RTM.

## Definiciones vinculantes

### No exportar

Para un operador, `no-export` significa denegar en backend, y no solo ocultar
en la interfaz, estas capacidades:

- descargar el original o una copia equivalente;
- obtener una URL reutilizable o presignada de propósito general;
- exportar en bloque, sincronizar, imprimir o copiar el contenido completo;
- enviar documentos a un origen, correo, almacenamiento o aplicación arbitrarios;
- consultar el bucket, la ruta física o credenciales de almacenamiento.

`no-export` no puede significar que los bytes nunca abandonen RTM cuando se
habilite una presentación. Entregar un archivo a una sede es una comunicación al
tercero y queda sujeta a finalidad, autorización, minimización y auditoría.
Además, algunas sedes pueden empezar a subir el archivo en cuanto se asigna al
control HTML, antes de pulsar el botón final de registro. Por ello, «adjuntar»
puede revelar o subir datos aunque todavía no equivalga a firmar o registrar.
El puente remoto actual no entrega bytes; antes de abrirlo deberá limitar cada
entrega a los documentos concretos de una sesión y al origen exacto ligado a
ella.

### Exportación administrativa excepcional

La exportación administrativa no es una función ordinaria ni se hereda de un
rol genérico de administrador. La política exige simultáneamente el rol exacto
`rtm.admin` y el permiso independiente
`ops.documents.export_exceptional`, concedido de forma individual y temporal
por necesidad. También exige una reautenticación posterior al login y no más
antigua de cinco minutos.

La ruta remota permanece cerrada: el actor construido por el router no recibe
la concesión excepcional individual y el servicio no tiene inyectado un motor
de marcado. El modelo y las pruebas de política no convierten esta operación en
una función disponible.

El flujo mínimo es:

`requested -> approved_not_executed -> executed -> downloaded|expired -> purged`

Debe exigir dos identidades distintas para solicitar y aprobar, autenticación
reforzada antes de aprobar y antes de ejecutar, expediente y documentos
explícitos, finalidad y destinatario documentados, caducidad corta y
notificación auditable. La aprobación **no genera ni descarga automáticamente**
el archivo. Una persona autorizada debe ejecutar después una confirmación
separada. La exportación masiva de un tenant queda denegada y exige un proceso
formal distinto, revisado con DPD y asesoría.

Una exportación excepcional no evita que el receptor pueda conservar su copia.
La entrega debe usar un canal aprobado, limitar accesos y purgar el artefacto
temporal conforme a la política aplicable.

## Fronteras de confianza y flujo de datos objetivo

El flujo completo siguiente es el objetivo de seguridad. En el ámbito sintético
ya se exige pertenencia A1-S y una asignación al expediente activa, aceptada y
con rol operativo admisible. El puente remoto, la presentación externa y la
equivalencia de ese control para expedientes reales siguen siendo condiciones
de apertura futura.

1. El backend autentica una sesión individual y resuelve tenant, expediente,
   roles y permisos sin confiar en valores de autoridad enviados por el cliente.
2. El backend crea una sesión de presentación y congela versiones y hashes.
3. Las aprobaciones se vinculan al hash canónico de esa sesión.
4. Tras una acción explícita del operador, el backend emite una capacidad de
   adjunto de un solo uso, con audiencia, origen, expediente, documentos y
   vencimiento cerrados.
5. El puente recibe únicamente los bytes seleccionados, verifica contexto y
   hash y los asigna al slot autorizado. No crea una descarga general.
6. El operador revisa destino, condición de representación y correspondencia
   slot-documento. RTM no acciona automáticamente la firma o el registro final.
7. El justificante se incorpora como artefacto posterior y se contrasta con la
   sesión. La ausencia de justificante produce estado desconocido o revisión
   manual, nunca reenvío automático.

Una vez enviado un documento a una sede de terceros, RTM no controla su
conservación, uso posterior, disponibilidad ni medidas de seguridad. El
destinatario y esa comunicación deben estar incluidos en la evaluación y en la
información aplicable.

## Catálogo de controles verificables

### SP-01 — Finalidad limitada

- Finalidades admitidas: gestionar el expediente, preparar y realizar una
  presentación autorizada, conservar evidencia de esa actuación, atender
  incidencias y proteger el servicio.
- Quedan prohibidos el entrenamiento de modelos con contenido real por defecto,
  la publicidad, el perfilado no relacionado, la analítica de producto con
  texto documental y la reutilización automática en otro expediente.
- Un uso de asistencia de IA debe ser explícito, usar un proveedor y configuración
  aprobados, enviar solo el fragmento mínimo necesario y registrar procedencia y
  revisión humana. El resultado entra como borrador y nueva versión; nunca
  reemplaza el original silenciosamente.
- Cada endpoint y job declara una finalidad técnica allowlisted. Una finalidad
  ausente o incompatible bloquea el tratamiento.

**Prueba:** tests de política deniegan finalidades desconocidas y telemetría de
contenido; el inventario de tratamientos enlaza cada operación permitida con
una finalidad validada.

### SP-02 — Minimización

- La cola operativa muestra metadatos mínimos y no texto completo, direcciones,
  identificadores oficiales ni miniaturas innecesarias.
- El operador solo ve el documento cuando el slot o una revisión concreta lo
  requiere; no existe listado transversal de documentos de otros expedientes.
- El adaptador solicita solo los archivos y campos exigidos por el procedimiento.
- Logs, métricas, trazas, errores y analítica no contienen cuerpos documentales,
  secretos, cookies, tokens, URLs firmadas ni parámetros sensibles.
- Nombres de archivo y etiquetas visibles evitan incluir más datos personales de
  los necesarios. Internamente se usan identificadores opacos y hashes.
- OCR, indexación, previsualizaciones y derivados solo se crean cuando tengan una
  finalidad documentada y heredan clasificación y borrado del original.

**Prueba:** revisión automática de schemas y logs, pruebas con canarios
sintéticos y consultas negativas entre tenant y expediente.

### SP-03 — Acceso por necesidad y menor privilegio

- Todo acceso exige identidad individual, MFA conforme al riesgo, membership
  activa, vínculo al expediente y permiso para la acción concreta.
- Presenter liga la sesión al dispositivo: un bearer aislado no basta. El
  secreto del dispositivo no se incluye en SQL, logs ni respuestas; solo se
  compara su digest dentro de la misma transacción de autorización y servicio.
- El MVP separa `presenter.documents.read`, `presenter.documents.ingest` y
  `presenter.package.freeze`. El permiso de ingreso solo autoriza incorporar
  una pieza al contenedor bajo cuarentena/revisión; ninguno autoriza bytes de
  salida, previsualización, descarga, ZIP, handoff o exportación.
- El rol administrativo de configuración no concede por sí solo lectura de
  contenido ni exportación excepcional. Esta última exige el rol exacto
  `rtm.admin` y el permiso independiente
  `ops.documents.export_exceptional`, además de las condiciones de step-up y
  concesión individual.
- En Presenter los permisos privilegiados son temporales, revisables y
  revocables, y la sesión es individual. Algunas vistas OPS legacy todavía leen
  un token compartido del navegador; ese mecanismo no autoriza Presenter y debe
  migrarse antes de considerar completa la retirada de credenciales compartidas.
- Las respuestas entre tenants o expedientes son indistinguibles de un recurso
  inexistente. Los identificadores aportados por el cliente no conceden acceso.
- Las identidades de servicio tienen audiencia y operaciones cerradas y no pueden
  enumerar o exportar expedientes.
- Altas, cambios y bajas de rol se auditan y se revisan periódicamente; una baja
  o revocación invalida sesiones y capacidades activas.

**Prueba:** matriz de autorización con casos positivos y negativos, incluyendo
IDOR, cambio de tenant, replay, sesión revocada y escalada administrativa.

### SP-04 — No-export del operador

- No existe para el operador un endpoint funcional de descarga de originales.
- La autorización se comprueba en el backend aunque la UI o el puente sean
  modificados por el usuario.
- El MVP actual tampoco ofrece previsualización, URL de objeto, ZIP ni entrega
  de bytes al operador.
- El contrato de capacidad futura limita la vida a un máximo de cinco minutos y
  prevé nonce de un solo uso, sesión, actor, versión documental, hash, slot,
  origen y procedimiento. Esa capacidad no puede emitirse remotamente mientras
  falte la atestación gestionada.
- Antes de abrir el puente se deberá probar que no sigue redirecciones hacia otro
  origen, valida frames y popups contra el destino allowlisted, no conserva
  documentos ni credenciales en almacenamiento local, IndexedDB, logs o
  telemetría y libera buffers al completar o cancelar.
- La navegación, recarga, cierre, revocación o cambio de versión deberá invalidar
  la capacidad; todo replay deberá fallar de forma cerrada.

**Prueba:** un operador recibe denegación en descarga, URL firmada, exportación,
impresión soportada y envío a origen distinto. La prueba de adjunto exacto a
una sesión vigente será un gate de apertura futuro; no describe una entrega
remota operativa hoy.

### SP-05 — Exportación administrativa excepcional y no automática

- `ops.documents.export_exceptional` está separado de administración, soporte,
  auditoría y presentación y solo es válido junto al rol exacto `rtm.admin`.
- La reautenticación debe ser un evento verificable posterior al login y estar
  dentro de una ventana máxima de cinco minutos. El heartbeat de sesión no
  renueva esa ventana.
- Se exige solicitud motivada, alcance documental explícito, doble control,
  autenticación reforzada, caducidad, confirmación de ejecución y registro de
  descarga o expiración.
- Ningún webhook, aprobación, cambio de estado, job periódico o reintento genera
  o descarga la exportación automáticamente.
- La entrega es de un solo uso. El artefacto se cifra durante almacenamiento y
  tránsito, no aparece en backups de propósito general si puede evitarse y se
  purga según una política corta validada.
- Umbrales de volumen, repetición, horario o destino anómalos bloquean o escalan
  la operación. No se permite comodín de documentos ni de tenants.
- Soporte técnico no puede ejecutar la excepción mediante impersonación.

**Prueba:** tests de separación de funciones, step-up, expiración, no
automatización, alcance exacto, single-use, purga y alertas de anomalía.

**Estado:** la política y el esquema validan rol, permiso y reautenticación,
pero la exportación remota está cerrada porque faltan la concesión individual
resuelta por el servidor y el motor de marcado requerido.

### SP-06 — Integridad documental y entrada externa

- Cada documento es inmutable por versión y se identifica con SHA-256 calculado
  por el backend. El manifiesto conserva la versión concreta presentada.
- Un documento externo se pone en cuarentena hasta validar tamaño, tipo real por
  contenido, nombre, malware y estructura. Macros, contenido activo o formatos
  no permitidos se rechazan o neutralizan mediante un proceso aprobado.
- El ingreso sintético implementado calcula hash y tamaño, restringe formatos,
  liga documento, versión, expediente y actor y deja el resultado
  `review/pending`. La validación estructural básica no equivale a un análisis
  antimalware: mientras no exista un recibo real de scanner/CDR, el documento
  no pasa a `active/clean` y Presenter no permite congelarlo.
- La aplicación limita la lectura a 25 MiB más un byte y rechaza longitudes
  multipart excesivas. Aun así, el parser ASGI o el proxy pueden recibir o
  volcar parte del cuerpo antes de ejecutar el handler. Antes de datos reales
  deben existir límites de cuerpo en proxy y servidor, temporales cifrados y
  acotados, limpieza verificable y una prueba de subida `chunked`; el control
  de aplicación por sí solo no elimina ese riesgo residual.
- Se registra origen, actor, fecha, relación con el expediente y, cuando proceda,
  herramienta de generación. El contenido generado o mejorado se etiqueta como
  borrador hasta revisión humana.
- La autorización de representación se clasifica como evidencia sensible, se
  valida para sujeto, alcance y vigencia y no se reutiliza automáticamente.
- Un cambio de bytes, metadatos relevantes, representación o destino invalida
  capacidades y aprobaciones anteriores.

**Prueba:** fixtures sintéticas benignas y maliciosas verifican cuarentena,
rechazo, hash, versionado, procedencia e invalidación.

### SP-07 — Seguridad de sesión, puente y destino

Este apartado es un gate de apertura. El router remoto construye actualmente el
actor de extensión sin atestación gestionada y la política lo deniega antes de
liberar bytes.

- Las capacidades son opacas, de uso único, de corta duración, con audiencia
  cerrada y sin datos personales en claro.
- El prototipo local usa permisos mínimos y hosts sintéticos exactos; no solicita
  `activeTab` ni acceso permanente a todas las webs. Cualquier ampliación de
  hosts debe ser explícita, gestionada, documentada y revisada.
- Backend y puente validan el origen efectivo, frame, procedimiento, sesión y
  allowlist. No aceptan destinos arbitrarios aportados por la página de terceros.
- El backend limita tasa y concurrencia, detecta descarga fragmentada o enumeración
  y ofrece un kill switch fail-closed para revocar todas las capacidades.
- No se capturan cookies, contraseñas, certificados, claves privadas, PIN,
  respuestas CAPTCHA ni secretos de la sede.
- Dependencias, extensión y aplicación nativa, si existe, se firman, inventarían
  y actualizan mediante un proceso controlado con posibilidad de revocación.

**Prueba:** replay, redirección, frame malicioso, origen parecido, token robado,
versión revocada y rate limit fallan sin liberar contenido.

### SP-08 — Conservación y borrado

No se fija aquí un número de años o días para el expediente real. El plazo debe
aprobarse por categoría y finalidad con DPD y asesoría. El sistema sí debe
aplicar una política explícita y no permitir conservación indefinida por
omisión.

| Categoría | Inicio del cómputo | Regla técnica mínima |
|---|---|---|
| Originales y versiones | Cierre, sustitución o evento definido para el expediente | Política por finalidad y categoría; bloqueo solo por conservación legal documentada. |
| Sesión de presentación | Finalización, cancelación o expiración | Metadatos probatorios mínimos; eliminar buffers, tokens y temporales inmediatamente. |
| Exportación excepcional | Descarga o expiración | Purga rápida del artefacto temporal; conservar solo evidencia mínima del control. |
| Previews, OCR, índices y derivados | Borrado o pérdida de finalidad del original | Cascada verificable; no deben sobrevivir sin justificación propia. |
| Logs de seguridad y auditoría | Fecha del evento | Plazo separado, acceso restringido y contenido minimizado. |
| Backups | Creación del backup | Expiración por ciclo aprobado; una restauración debe reaplicar tombstones y borrados. |

- El borrado cubre base de datos, objetos, previews, índices, colas, caches,
  temporales, réplicas y sistemas de observabilidad que pudieran contener datos.
- Una retención legal suspende solo los objetos y finalidades identificados,
  requiere actor, motivo, alcance, fecha de revisión y liberación auditable.
- El proceso produce evidencia de borrado sin conservar el contenido eliminado.
- Los fallos se reintentan de forma controlada y escalan; no se marca borrado
  completo mientras quede una copia activa conocida.
- Los backups no se alteran de forma insegura, pero quedan fuera de uso ordinario,
  expiran según ciclo y no restauran datos ya borrados al servicio activo.

**Prueba:** ensayo de borrado de un expediente sintético verifica cascada,
tombstone, restauración de backup, liberación de hold y reporte de excepciones.

### SP-09 — Auditoría y detección

El ledger de auditoría es append-only o dispone de protección equivalente
contra alteración. Registra, con hora sincronizada y `request_id`:

- autenticación, step-up, revocación y fallos relevantes;
- lectura o previsualización de contenido;
- creación, aprobación, uso, expiración y revocación de capacidades;
- documento, versión, hash, slot y origen de cada adjunto;
- creación, aprobación, ejecución, descarga, expiración y purga de exportaciones;
- cambios de rol, allowlist, retención, hold y configuración del puente;
- denegaciones, anomalías, incidentes y uso del kill switch;
- referencia y hash del justificante, sin incluir su contenido completo.

Los eventos no incluyen documentos, tokens, URLs de acceso, cookies, secretos ni
campos personales innecesarios. El acceso al ledger está separado del acceso a
contenido. Se alertan, como mínimo, enumeración, denegaciones repetidas,
volúmenes inusuales, múltiples expedientes, destinos no permitidos, replay y
exportaciones excepcionales.

**Prueba:** integridad del ledger, cobertura de eventos, ausencia de contenido y
tokens, alertas y acceso de solo lectura para auditoría.

### SP-10 — Transparencia y control humano

Antes de presentar, la interfaz muestra en lenguaje claro:

- identidad o rol con el que se actúa y si existe representación;
- destino y procedimiento exactos;
- nombres, categorías y versiones de los documentos seleccionados;
- finalidad del envío y advertencia de que el tercero recibirá una copia;
- qué pasos realiza RTM y cuáles realiza la persona;
- estado incierto cuando no haya justificante verificable.

La información de privacidad aplicable debe ser accesible antes de incorporar
datos reales e incluir responsable y contacto, finalidades, base a validar,
categorías, destinatarios, encargados y transferencias, conservación, derechos,
canales de ejercicio, decisiones automatizadas si existieran y contacto del
DPD. Los cambios sustanciales de finalidad o destinatario requieren revisión y
actualización previa de esa información.

RTM no presenta en silencio: adjuntar no equivale a firmar o registrar. El botón
final permanece en la sede y el operador confirma el mapa documental. Sin
embargo, elegir un archivo puede iniciar su subida o revelarlo a la sede antes
del registro final, según el comportamiento del portal; la interfaz debe
advertirlo antes de asignar los bytes. El estado de éxito solo se alcanza con
evidencia definida; una pantalla aparentemente correcta no sustituye al
justificante.

**Prueba:** pruebas de interfaz y API confirman revisión previa, confirmación
explícita, ausencia de auto-submit, información vigente y tratamiento seguro de
`outcome_unknown`.

## Matriz de rol, capacidad y acción

Esta es la matriz contractual implementada por el MVP; no deben sustituirse
estos identificadores por alias de interfaz:

| Actor | Permiso exacto | Acción efectiva actual | Límite |
|---|---|---|---|
| `rtm.operator` o `rtm.supervisor` | `presenter.documents.read` | Consultar metadatos y versiones sintéticas del expediente. | Sin bytes, preview, URL, ZIP, handoff ni referencias de almacenamiento. |
| `rtm.operator` o `rtm.supervisor` | `presenter.documents.ingest` | Incorporar en staging sintético un archivo externo nuevo o una nueva versión. | Custodia interna `review/pending`; no seleccionable hasta análisis real `clean` y activación autorizada. |
| `rtm.operator` o `rtm.supervisor` | `presenter.package.freeze` | Congelar un paquete sintético con destino, selección, versiones y hashes. | No presenta ni entrega archivos al portal. |
| `rtm.admin` | `ops.documents.export_exceptional` | Ninguna entrega remota en el router actual. | El rol y permiso deben concurrir exactamente; además faltan concesión individual resuelta por servidor, motivo, reautenticación válida dentro de cinco minutos y motor de marcado. |

El permiso `ops.documents.export_exceptional` no se recibe por ser
administrador, desarrollador, soporte, auditor o propietario de
infraestructura. La ruta permanece cerrada aunque el cliente envíe valores que
afirmen tener ese rol o permiso: la autoridad se deriva de la sesión del
servidor. El acceso de emergencia a infraestructura se gobierna fuera de esta
matriz, con controles privilegiados, registro y revisión propios, y no debe
presentarse como una garantía absoluta de inaccesibilidad.

La demo sintética exige, además de pertenencia al tenant, una asignación activa,
aceptada y sintética al expediente como responsable, revisor o supervisor. Para
usar datos reales deberá existir y probarse el contrato equivalente sin los
marcadores sintéticos; los permisos globales anteriores no sustituyen ese
control por expediente.

## Amenazas consideradas y respuesta esperada

| Amenaza | Control principal | Riesgo residual / límite |
|---|---|---|
| Operador curioso intenta descargar o enumerar | Denegación backend, scope de expediente, rate limit y alertas. | Un operador autorizado puede recordar o transcribir lo que necesita ver. |
| Operador modifica la UI o llama a la API | Autorización server-side y capacidades cerradas. | Un dispositivo comprometido puede observar datos ya mostrados o adjuntados. |
| Sesión o capacidad robada | MFA, TTL corto, audiencia, nonce, vínculo a actor/origen y revocación. | Existe una ventana residual hasta detección o expiración. |
| Administrador abusa de privilegios | Exportación separada, JIT, doble control, step-up y ledger. | La colusión o un superusuario de infraestructura requieren controles organizativos y PAM adicionales. |
| Sede equivocada o dominio parecido | Allowlist exacta, validación de origen/frame y confirmación visible. | Una sede legítima comprometida queda fuera del control directo de RTM. |
| Documento obsoleto o alterado | Versiones inmutables, hash y revocación al cambiar el manifiesto. | La corrección jurídica del contenido sigue necesitando revisión humana. |
| Documento externo malicioso | Cuarentena, validación por contenido, antimalware y formatos permitidos. | Ningún detector garantiza descubrir todas las amenazas nuevas. |
| Puente o extensión comprometidos | Permisos mínimos, firma, inventario, actualizaciones controladas y kill switch. | El código ejecutado en el endpoint forma parte de la base de confianza. |
| Fuga por logs, métricas o soporte | Minimización, redacción, canarios sintéticos y acceso separado. | Los errores de instrumentación deben vigilarse continuamente. |
| Copias temporales y caches | Flujo en memoria, `no-store`, purga y verificación de residuos. | SO, navegador o herramientas de seguridad pueden crear caches fuera del control web. |
| Doble presentación por resultado incierto | Idempotencia, `outcome_unknown`, reconciliación y ausencia de retry automático. | Puede requerirse comprobación humana en la sede. |
| Fallo de borrado o restauración de backup | Orquestación de borrado, tombstones, reintentos y prueba de restauración. | Las copias offline expiran según su ciclo, no necesariamente de forma inmediata. |

## Límites honestos de `no-export`

En un navegador y dispositivo no administrados no existe una garantía técnica
absoluta de que una persona autorizada no copie lo que puede ver. Bloquear el
botón de descarga, el menú contextual, imprimir o el portapapeles mejora la
fricción y evita extracciones accidentales, pero no impide de forma fiable:

- capturas o grabación de pantalla del sistema operativo;
- fotografía o vídeo con otro dispositivo;
- herramientas de accesibilidad, desarrollo, malware o inspección de memoria;
- transcripción manual;
- caches o swap creados por el sistema;
- conservación por la sede de destino después de la presentación.

No debe prometerse “imposibilidad de copia” basándose solo en controles web. Si
el nivel de riesgo exige impedir capturas, descarga, impresión, USB o
portapapeles, se necesita un dispositivo administrado o VDI con políticas del
SO y navegador, DLP/EDR, control de periféricos, sesión restringida, marcas de
agua y supervisión proporcional. Incluso así, una cámara externa requiere
controles físicos y organizativos; la tecnología web no puede detectarla ni
bloquearla con garantías.

El uso de VDI o monitorización laboral deberá evaluarse por necesidad,
proporcionalidad, transparencia y normativa aplicable con DPD, asesoría y las
partes laborales correspondientes.

## Respuesta a incidentes

El flujo debe integrarse en el procedimiento general de incidentes y disponer
de un runbook específico, probado con datos sintéticos:

1. **Detectar y clasificar:** alerta, reporte humano o evidencia de acceso,
   exportación, destino o versión indebidos. No incluir contenido en el ticket.
2. **Contener:** cerrar el feature gate si procede; revocar sesiones,
   capacidades y concesiones JIT; bloquear origen o versión del puente; detener
   exports pendientes; preservar el ledger.
3. **Delimitar:** identificar tenant, expedientes, versiones y hashes, actores,
   destinatarios, tiempos y copias temporales potenciales.
4. **Erradicar y recuperar:** corregir causa, rotar credenciales o claves
   afectadas mediante el procedimiento seguro, validar integridad, limpiar
   residuos y reabrir solo tras una decisión documentada.
5. **Evaluar comunicaciones:** DPD y asesoría determinan obligaciones, plazos,
   destinatarios y contenido de cualquier notificación. El backend no concluye
   automáticamente que una notificación legal sea o no necesaria.
6. **Aprender:** postmortem sin culpa, controles y tests nuevos, revisión de
   accesos y confirmación de que no hubo reenvío automático.

El runbook debe identificar responsables y suplentes, canales fuera de banda,
criterios de severidad y tiempos internos de respuesta aprobados. Los contactos,
tokens y secretos no se documentan en este repositorio.

## Criterios de aceptación

Estos criterios son necesarios pero no suficientes para un `GO` con datos
reales.

### Autorización y no-export

- [ ] **AC-01:** pruebas de API demuestran que un operador no puede descargar,
  imprimir mediante una función backend, obtener URL reutilizable, exportar en
  bloque ni cambiar de tenant/expediente, aunque altere el frontend.
- [ ] **AC-02:** una capacidad de adjunto contiene audiencia y scope efectivos,
  caduca en un máximo de cinco minutos, funciona una sola vez y falla ante replay,
  actor distinto, versión distinta, slot distinto, frame distinto u otro origen.
- [ ] **AC-03:** el puente no persiste bytes, tokens o URLs de acceso y una prueba
  de cierre, cancelación y crash controlado no deja temporales propios conocidos.
- [ ] **AC-04:** permisos de operador, supervisor, administrador, auditor y
  servicios cumplen la matriz mediante tests positivos y negativos. Ningún rol
  administrativo hereda exportación excepcional.

### Presentación e integridad

- [ ] **AC-05:** la sesión congela destino, procedimiento, representación,
  documento, versión, tamaño, tipo y SHA-256; cualquier cambio invalida
  aprobaciones y capacidades.
- [ ] **AC-06:** no existe auto-submit, automatización de firma ni captura de
  credenciales. La persona revisa el mapa slot-documento y ejecuta el acto final
  en la sede.
- [ ] **AC-07:** la falta de justificante produce `outcome_unknown` o revisión
  manual y nunca reintento o doble presentación automática.
- [ ] **AC-08:** documentos externos pasan por cuarentena, validación real de
  tipo, límites, antimalware, procedencia, versionado y revisión humana usando
  fixtures sintéticas benignas y adversarias.

### Excepción administrativa

- [ ] **AC-09:** solicitud y aprobación corresponden a identidades distintas;
  aprobación y ejecución exigen step-up; la capacidad JIT expira y no admite
  comodines de tenant o documentos.
- [ ] **AC-10:** aprobar no genera ni descarga nada. Solo una confirmación de
  ejecución posterior crea una entrega single-use; jobs, webhooks, retries y
  cambios de estado no pueden dispararla.
- [ ] **AC-11:** descarga, expiración y purga quedan auditadas; un ensayo verifica
  que el artefacto temporal deja de ser accesible y que los umbrales anómalos
  bloquean o alertan.

### Minimización, auditoría y ciclo de vida

- [ ] **AC-12:** escaneos y canarios sintéticos confirman que logs, errores,
  trazas, métricas y analítica no contienen documentos, tokens, cookies, URLs
  firmadas ni campos personales prohibidos.
- [ ] **AC-13:** el ledger registra todos los eventos SP-09, detecta alteraciones
  o usa protección equivalente y es accesible al auditor sin contenido.
- [ ] **AC-14:** existe inventario de originales, versiones, previews, OCR,
  índices, colas, caches, réplicas, temporales, logs y backups, cada uno con
  propietario y regla de retención aprobada.
- [ ] **AC-15:** una prueba de borrado sintético cubre cascada, fallo parcial,
  retry, hold, liberación, backup restaurado y reaplicación de tombstones, sin
  declarar éxito mientras quede una copia activa conocida.

### Seguridad operativa, incidentes y transparencia

- [ ] **AC-16:** pruebas ofensivas cubren IDOR, replay, origen parecido,
  redirección, iframe, sesión revocada, enumeración, rate limit y versión del
  puente revocada; todas fallan de forma cerrada.
- [ ] **AC-17:** el kill switch revoca capacidades activas y bloquea nuevas
  entregas sin borrar evidencia; el runbook se ensaya con un incidente sintético.
- [ ] **AC-18:** la interfaz informa destino, representación, documentos y
  versiones, finalidad, recepción por tercero, límites de RTM y estado incierto
  antes de la acción irreversible.
- [ ] **AC-19:** si se comunica una garantía fuerte contra copia, el flujo exige
  dispositivo administrado o VDI y existe una evaluación específica de sus
  políticas. Sin esa condición, la documentación y la UI describen `no-export`
  como reducción de riesgo, no como imposibilidad de captura.
- [ ] **AC-20:** no se habilitan datos reales ni producción hasta documentar la
  revisión del DPD y asesoría sobre base, información, destinatarios, encargados,
  transferencias, conservación, derechos, controles laborales y necesidad de
  EIPD, además de superar los gates técnicos aplicables.

## Evidencia mínima para revisión

La decisión de avance debe enlazar, sin incluir secretos ni datos reales:

- diagrama de flujo y registro de tratamientos actualizado;
- matriz de permisos implementada y resultados de tests negativos;
- modelo de amenazas y revisión del puente/extensión;
- inventario de datos, derivados, logs, caches y backups;
- políticas aprobadas de retención, hold y exportación excepcional;
- resultados de pruebas de cuarentena, borrado, restauración e incidente;
- ejemplo completamente sintético del ledger y del manifiesto de presentación;
- textos de transparencia y registro de revisión por DPD y asesoría;
- riesgos residuales aceptados, responsable y fecha de próxima revisión.

La evidencia demuestra que los controles fueron diseñados y probados; no debe
redactarse como una certificación de cumplimiento jurídico o de seguridad.
