# RTM Presenter · contrato de entrega controlada

Fecha de corte: 29/08/2026  
Versión: `rtm_presenter_delivery_v1_3`
Entorno autorizado en este corte: staging sintético, sin efectos externos.

## 1. Objeto

Una entrega no crea otro almacén documental. Conserva identificadores de
versión, SHA-256, campo y nombre de carga, pero el momento en que se fija la
selección depende del canal.

Canales previstos:

1. `portal`: el contenedor permanece suelto. El operador fija un manifiesto
   interno con la versión y huella que corresponde a cada requisito, pero no
   crea un ZIP, carpeta ni fichero compuesto. En el puesto local cada control de
   la sede recibe exclusivamente su documento mediante una entrega individual.
2. `email` / **RTM Correspondencia**: preparación controlada desde custodia de
   destinatario, texto y una selección fijada de adjuntos. El envío servidor a
   servidor es una fase posterior y permanece bloqueado en este corte.

El justificante, acuse o recibo nunca es un documento de salida. Solo puede
nacer o incorporarse después de la actuación externa como evidencia candidata.

## 2. Estado implementado de Correspondencia

La operación `prepare`, reservada en este corte a la selección fijada de
Correspondencia:

- exige sesión individual, acceso activo al expediente y el permiso específico
  `presenter.delivery.prepare`;
- valida que el paquete siga congelado, vigente y ligado a un perfil activo;
- rechaza cualquier versión sustituida, no activa o sin escaneo limpio;
- conserva orden contiguo, campo, versión y huella de cada documento;
- es idempotente y serializa reintentos concurrentes mediante advisory lock;
- registra `presenter.delivery.prepared` en el ledger append-only;
- no lee bytes, no abre SMTP, no emite ticket y no produce efecto externo;
- devuelve `authoritative_submission=false`,
  `automatic_retry_allowed=false` y `receipt_required=true`.

La UI valida la respuesta contra la selección fijada que mantiene en memoria. Una
respuesta con elementos, hashes, destino o flags distintos se descarta.

El canal portal usa el contrato de sesión individual y tickets sueltos. Desde
`rtm_presenter_delivery_v1_2`, el operador completa además una hoja de trámite
definida por el perfil, confirma destino, interesado, representación, texto y
adjuntos, fija las versiones exactas y crea una tarea
`awaiting_signature`. El prototipo sintético puede registrar intención, adjunto
y justificante candidato, pero el puente hacia sedes externas permanece cerrado.

`rtm_presenter_delivery_v1_3` añade a la instantánea el modo de representación
validado del paquete. El puesto local puede así distinguir interesado y
representante sin inferirlo del nombre de un archivo. En representación, la
autorización continúa ligada como una versión documental separada.

La cola `rtm_presenter_signature_queue_v1_0` solo muestra tareas de expedientes
que siguen dentro del scope A1-S y de una asignación activa de la cuenta. Permite
cambiar de expediente sin cerrar la sesión individual de OPS. No concede firma,
no comparte una sesión de sede y no expone bytes ni coordenadas de custodia.

## 3. Registro interno de destinos

OPS busca perfiles activos mediante texto literal sobre organismo, nombre y
código. Los resultados mantienen la regla de cuatro ojos: creador y verificador
deben ser operadores distintos y debe existir fecha de verificación.

No se permite que un operador pegue una URL de sede. En Correspondencia puede
escribir una dirección, pero nunca se convierte por ello en verificada: queda
marcada `operator_entered_email_pending_verification` y exige confirmación
independiente antes de cualquier ejecución. En staging solo se admiten dominios
reservados sintéticos.

El Centro de destinos no resuelve solo una dirección. Conserva entidad jurídica,
papel del destinatario, materias admitidas, canal, fuente oficial, fecha de
verificación, alternativa probatoria y política para adjuntos sensibles. Un
perfil con `form_required` o canal alternativo no habilita envío por correo.

No se mantiene un desplegable completo de miles de sedes. El backend limita
cada búsqueda y devuelve únicamente la proyección necesaria para la operación.

Desde el corte 30/08/2026 la búsqueda devuelve dos colecciones estrictamente
separadas:

1. `destinations`: perfiles RTM de procedimiento activos, con cuatro ojos y
   seleccionables únicamente si además cumplen el canal solicitado;
2. `directory_results`: identidades administrativas DIR3/SIR de referencia,
   siempre no seleccionables y sin inferencia de procedimiento o competencia.

El snapshot DIR3/SIR se compila offline a partir de los listados públicos
aportados, se liga a sus hashes y no se actualiza por red durante la operación.
Que una unidad figure en SIR significa solamente que constaba asociada a una
oficina en esa fotografía y permite considerarla candidata para remisión por
REG. No decide el destinatario de un recurso ni prueba que el órgano siga
integrado o sea competente.

El REG permite presentar escritos a órganos AGE y remitirlos a CCAA/EELL
integradas en SIR cuando no exista un procedimiento electrónico o formulario
normalizado. Si un régimen especial exige otra vía, el envío general puede ser
rechazado. RTM modelará por ello un perfil verificado único de REG con destino
DIR3/SIR dinámico, comprobación de vigencia y confirmación humana; no un perfil
independiente por cada municipio.

El perfil REG seguirá el orden observado en el portal: datos del solicitante,
datos de solicitud/destino, documentación y firma. En representación habrá una
presentación separada por cada interesado. Los documentos se vincularán y
entregarán individualmente en el tercer paso; la firma y el submit no se
automatizan. Después, el justificante descargado deberá incorporarse como
evidencia candidata y conciliarse con unidad, fecha, escrito y huellas antes de
activar seguimiento.
El contrato específico está en
`staging/RTM_PRESENTER_DIRECTORY_CONTRACT_2026-08-30.md`.

En el staging actual solo existe un recorrido genérico `synthetic.example`. Si
una búsqueda no coincide, la UI conserva los recorridos sintéticos disponibles
y permite continuar la prueba con una acción explícita. No los rotula como DGT,
Madrid u otra sede real. El catálogo real continúa pendiente de alta y doble
verificación.

Tras una búsqueda sin coincidencias, un operador con permiso específico puede
proponer un nombre y un enlace. La propuesta queda en auditoría con estado
`pending_independent_verification`: no crea un perfil, no abre la URL y no puede
usarse para presentar. En este staging solo se aceptan hosts reservados
`synthetic.example`, HTTPS, sin credenciales, query ni fragmento.

Para multas se catalogará primero el organismo sancionador y después el
procedimiento. La identidad del agente denunciante no determina por sí sola el
destino. En particular:

- la vía específica de la DGT para alegaciones y recursos se modelará como una
  entrada principal única; utiliza el número de expediente y la propia
  aplicación determina la admisibilidad del trámite;
- la Jefatura territorial o el CTDA serán datos de enrutamiento cuando se use
  correo u otra vía que los exija, no una elección provincial obligatoria en el
  trámite específico;
- si la sanción pertenece a un ayuntamiento o comunidad autónoma, RTM debe
  seleccionar ese organismo y no DGT;
- el Registro Electrónico General queda como vía genérica para escritos sin
  procedimiento normalizado, eligiendo el órgano destinatario competente.

Fuentes oficiales verificadas en este corte:

- <https://sede.dgt.gob.es/es/multas/presentacion-de-alegacion-o-recurso-a-una-multa/>;
- <https://www.dgt.es/nuestros-servicios/multas-y-sanciones/quien-puede-multarte/>;
- <https://sede.administracionespublicas.gob.es/pagina/index/directorio/registro_rec>.

## 4. Canal sede electrónica

El contrato refleja la realidad de las sedes: cada portal puede pedir primero
la autorización, después el escrito y luego la prueba, o cualquier otro orden
verificado. No se presume que admita un ZIP ni un paquete completo.

Antes de activar el puente remoto faltan:

- atestación criptográfica de extensión gestionada;
- host permission opcional para el origen exacto;
- ticket de un solo uso por elemento y campo;
- evidencia de que el campo recibió el archivo;
- estados `in_progress`, `awaiting_receipt`, `completed` y
  `outcome_unknown` derivados de hechos verificables;
- conciliación del justificante con paquete, destino, versiones y huellas.

Firma, certificado, PIN, Cl@ve, CAPTCHA y submit final siguen siendo humanos.
Adjuntar un archivo puede constituir ya una comunicación al tercero y debe
avisarse antes de cada entrega de bytes.

### 4.1 Frontera entre operador y firmante

El operador no abre la sesión autenticada del firmante. Su tarea termina al
dejar en la cola:

- destino y perfil verificados;
- modo de actuación e interesado;
- Asunto, Expone, Solicita u otros campos definidos por el perfil;
- versión y SHA-256 de cada documento, incluida la autorización cuando proceda;
- cinco confirmaciones humanas y la identidad del operador preparador.

El firmante trabaja desde un puesto local gestionado. Ese puesto abrirá la sede,
rellenará los pasos previos y entregará cada documento por un ticket de un solo
uso. Debe detenerse en la revisión y firma final. El certificado no se guarda en
RTM, Render, variables de entorno, B2 ni el navegador de los operadores. Tampoco
se comparte la cookie o sesión autenticada de la sede ni se exige escritorio
remoto.

En este corte están implementadas la hoja sellada, la cola asignada y la toma
exclusiva descrita a continuación. La apertura de sede, el cliente atestado que
entrega bytes y el adaptador REG continúan bloqueados y la UI lo declara
expresamente. `signature_queue_ready=true` significa únicamente que la tarea
está preparada para esa fase, nunca que el puente o la firma estén activos.

### 4.2 Puesto local v1: identidad y toma exclusiva

`rtm_presenter_signer_station_v1_0` añade una superficie separada para el puesto
de Ramón:

- exige el cliente `signer_station`, el rol exacto `rtm.signer` y exactamente los
  permisos `ops.view`, `presenter.signing.queue` y
  `presenter.signing.claim`;
- rechaza reutilizar una cuenta operativa como firmante;
- conserva el scope del caso: binding y tenant A1-S sintéticos, membership activa
  y asignación aceptada `responsible`, `reviewer` o `supervisor`;
- lista únicamente metadatos resumidos antes de tomar una tarea;
- crea una toma exclusiva de 30 minutos bajo advisory lock y evento inmutable;
- permite reabrir el workspace exacto desde la misma cuenta y sesión sin
  duplicar eventos;
- permite que una sesión nueva adopte el último intento solo mediante una
  acción explícita, desde la misma cuenta, dispositivo e instalación y con la
  misma huella de tarea;
- permite liberarla de forma idempotente y deja caducar una toma abandonada;
- no revela a otra sesión quién mantiene una tarea ocupada;
- rechaza un ledger con dos tomas simultáneas no caducadas y nunca sustituye
  una toma activa ajena al origen exacto de la recuperación.

Después de la toma se proyectan la hoja y los documentos sueltos, ligados a
manifiesto, perfil, versión y huellas. La respuesta sigue siendo metadata-only:
no incluye bytes, bucket, key, URL presignada, cookie de sede, certificado ni
clave privada.

La ruta frontend `/ops/presenter/signer` mantiene el bearer en memoria, cierra
la sesión al desmontar y muestra **Abrir sede · activación local pendiente**
deshabilitado. Por tanto, esta v1 prueba identidad, routing y exclusión mutua,
pero no es todavía el puente REG.

La cola sigue siendo asignada, no global. Para staging puede provisionarse la
cuenta sintética separada con `--role signer` y vincularse a la fixture mediante
`--access-kind signer`; ambos pasos conservan confirmaciones literales y no se
han ejecutado remotamente en este corte. El enrutamiento futuro de todos los
expedientes pagados a una cola central requiere una decisión y un contrato
adicional.

### 4.3 Recuperación cuando REG pierde el formulario

REG no se considera un sistema de borradores. La caducidad observada por
inactividad, aproximadamente a los 15–20 minutos, puede destruir el formulario
no presentado. RTM debe poder reconstruirlo sin conservar una cookie de la sede
ni automatizar su autenticación.

El contrato recuperable mantiene dos capas separadas:

1. La entrega `awaiting_signature` es la fuente durable: fija destino, origen,
   representación, campos, valores, documentos, versiones, nombres, orden y
   huellas.
2. El workspace registra únicamente el estado de un intento bajo una toma
   exclusiva: `ready`, `reg_session_expired` y una nueva vuelta a `ready` tras
   solicitar reautenticación. No contiene un borrador de REG ni material de su
   sesión.

`rtm_presenter_workspace_recovery_v1_0` expone dos operaciones distintas:

- `GET /signer/installations/{installation_id}/workspace-recoveries` descubre
  los últimos intentos por entrega del mismo operador, dispositivo e
  instalación. Devuelve como máximo 50 elementos, 20 por defecto, y solo
  metadatos suficientes para distinguir `current_session`, una adopción
  posible, una toma activa que bloquea o un rollback de sesión bloqueado. El
  GET no toma, libera, sustituye ni adopta.
- `POST /signer/tasks/{delivery_id}/workspace-recovery` es la confirmación
  explícita. Exige `Idempotency-Key`, el `installation_id`, el
  `source_workspace_id` y el `expected_task_fingerprint_sha256` exactos. En la
  misma sesión reabre el workspace sin otro evento; tras un nuevo login crea
  una toma y workspace nuevos desde la instantánea durable.

La adopción entre sesiones solo es posible para la misma cuenta firmante,
dispositivo validado, instalación candidata, entrega y huella. Si la toma de
origen exacta continúa activa, se registra primero
`presenter.signer_station.superseded`; después se conservan la toma nueva y
`presenter.signer_workspace.recovered`. La sustitución y la procedencia son
append-only: no se reescribe el intento anterior. Una toma activa de otro actor,
sesión o claim, un workspace que ya no sea el último o una huella divergente
bloquean la operación.

La cadena de procedencia se comprueba de forma recursiva, sin ciclos y con un
límite de 64 saltos. Solo puede avanzar A→B→C: una sesión histórica A no
puede adoptar un intento descendiente B para producir B→A, aunque la toma de B
haya caducado. La misma clave idempotente reproduce el resultado ya registrado;
una clave distinta no puede bifurcar un origen obsoleto.

Descubrimiento y adopción son metadata-only y no dependen de almacenamiento del
navegador. No exponen ni persisten bytes, bucket, key, URL presignada, cookie o
credencial de REG, certificado ni clave privada. En todos los casos el firmante
vuelve a autenticarse humanamente en REG. El cliente gestionado que en el futuro
abra y rellene la sede, la entrega de bytes, la firma y el submit permanecen
bloqueados en este corte sintético y ninguna de estas operaciones produce
efectos externos.

Cuando se incorpore un documento al contenedor, el operador podrá darle un
nombre reconocible. RTM mantendrá separadamente el tipo documental controlado,
el nombre seguro que se ofrecerá a la sede, el nombre original de origen, la
versión y la huella. Un nombre libre nunca modifica por sí solo el tipo interno.

Tras la presentación, la captura o incorporación del justificante debe quedar
ligada al expediente y al intento de envío. Su mera presencia no activa plazos:
primero debe verificarse y conciliarse con destino, fecha, procedimiento y
documentos. Si la unión automática no puede acreditarse, la interfaz debe
mostrarla como pendiente y nunca fingir que quedó unida.

## 5. RTM Correspondencia

La preparación de correo solo se admite si el perfil verificado contiene:

- entidad jurídica y papel en la reclamación;
- destinatario exacto;
- marca `verified=true`;
- estado de canal `accepted`;
- fuente oficial y fecha de verificación;
- materias admitidas y advertencia de derivación;
- alternativa probatoria y política de adjuntos sensibles;
- código de plantilla;
- versión de plantilla.

El operador revisa un asunto y cuerpo derivados de una plantilla versionada y
debe confirmar expresamente destinatario, interesado, representación, texto,
versiones de adjuntos y minimización documental. La preparación guarda en el
ledger el texto exacto, remitente previsto `info@recurretumulta.eu`, destinatario,
plantilla, versiones y SHA-256 de adjuntos.

La evidencia de transporte nace vacía: no hay `Message-ID`, respuesta SMTP,
aceptación del servidor, rebote, respuesta, número de reclamación ni prueba de
recepción. La aceptación SMTP futura no se tratará como prueba definitiva de
entrega.

En este corte se compone y audita el borrador, pero no se envía correo. La
ejecución futura deberá exigir permiso separado, step-up, capacidades runtime
`outbound_email` y `external_submission`, idempotencia, adjuntos leídos en
servidor directamente desde custodia y registro del mensaje y sus eventos.

Un timeout o respuesta SMTP incierta producirá `outcome_unknown`. Nunca habrá
reintento automático cuando pueda haberse producido un efecto externo.

Base funcional contrastada:

- la CNMC distingue reclamaciones a comercializadora (tarifa, altas/bajas,
  factura o datos) y a distribuidora (cortes, averías, contador o lecturas):
  <https://www.cnmc.es/facil-para-ti/que-hace-la-cnmc-para-consumidores/comercializacion-y-suministro-electrico>;
- el Sistema Arbitral de Consumo recomienda contactar antes con la empresa y
  dejar constancia de la reclamación:
  <https://justoparaeso.consumo.gob.es/>;
- la política de adjuntos sensibles debe poder exigir cifrado o enlace seguro,
  conforme al análisis de riesgos recogido en la guía de cifrado de la AEPD:
  <https://www.aepd.es/guias/guia-cifrado-autonomos-pymes.pdf>.

## 6. Documento 2

`prejudicial_authorization` es una finalidad documental independiente de
`representation_authorization`.

La toma de datos solo registra una preferencia opcional
`prejudicial_counsel_requested`. Esa preferencia no es consentimiento, mandato
ni apoderamiento. El documento debe explicarse y firmarse por separado.

El borrador jurídico debe excluir, salvo consentimiento específico posterior:

- aceptar acuerdos o indemnizaciones;
- renunciar, desistir o transigir;
- someter el asunto a arbitraje;
- cobrar cantidades del cliente;
- iniciar actuaciones judiciales;
- actuar como mediador neutral y abogado de parte en el mismo procedimiento.

La mediación, la negociación de un acuerdo y la fase judicial son escalones
separados. La redacción final requiere validación de Mario antes de producción.

## 7. Estados reservados

- `prepared`: orden registrada, sin efecto externo.
- `awaiting_signature`: texto y documentos fijados en la cola; la sede no se ha
  abierto y no existe presentación.
- `in_progress`: existen pasos de canal acreditados, aún sin resultado final.
- `awaiting_receipt`: la acción externa consta, falta justificante conciliado.
- `completed`: justificante activo y limpio conciliado con la entrega.
- `outcome_unknown`: puede existir efecto externo, pero no hay prueba concluyente.
- `failed_before_external_effect`: fallo acreditado antes de transferir o enviar.
- `cancelled`: cancelación previa a efectos externos.

No se permite inferir `completed` desde un clic, una pantalla de éxito sin
recibo o la mera ausencia de error.
