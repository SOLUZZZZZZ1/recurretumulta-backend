# RTM Presenter · contrato de entrega controlada

Fecha de corte: 29/08/2026  
Versión: `rtm_presenter_delivery_v1_1`
Entorno autorizado en este corte: staging sintético, sin efectos externos.

## 1. Objeto

Una entrega se deriva de un paquete Presenter ya congelado. No crea otro
almacén documental: conserva los identificadores de versión, SHA-256, orden,
campo y nombre de carga del paquete original.

Canales previstos:

1. `portal`: carga documento a documento, en el orden del perfil verificado de
   la sede electrónica.
2. `email` / **RTM Correspondencia**: preparación controlada desde custodia de
   destinatario, texto y adjuntos. El envío servidor a servidor es una fase
   posterior y permanece bloqueado en este corte.

## 2. Estado implementado

La operación `prepare`:

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

La UI valida la respuesta contra el paquete que mantiene en memoria. Una
respuesta con elementos, hashes, destino o flags distintos se descarta.

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
- `in_progress`: existen pasos de canal acreditados, aún sin resultado final.
- `awaiting_receipt`: la acción externa consta, falta justificante conciliado.
- `completed`: justificante activo y limpio conciliado con la entrega.
- `outcome_unknown`: puede existir efecto externo, pero no hay prueba concluyente.
- `failed_before_external_effect`: fallo acreditado antes de transferir o enviar.
- `cancelled`: cancelación previa a efectos externos.

No se permite inferir `completed` desde un clic, una pantalla de éxito sin
recibo o la mera ausencia de error.
