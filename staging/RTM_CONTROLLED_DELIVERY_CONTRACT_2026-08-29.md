# RTM Presenter · contrato de entrega controlada

Fecha de corte: 29/08/2026  
Versión: `rtm_presenter_delivery_v1_0`  
Entorno autorizado en este corte: staging sintético, sin efectos externos.

## 1. Objeto

Una entrega se deriva de un paquete Presenter ya congelado. No crea otro
almacén documental: conserva los identificadores de versión, SHA-256, orden,
campo y nombre de carga del paquete original.

Canales previstos:

1. `portal`: carga documento a documento, en el orden del perfil verificado de
   la sede electrónica.
2. `email`: envío servidor a servidor desde custodia, únicamente a una
   dirección y plantilla incluidas en un perfil verificado.

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

No se permite que un operador pegue una URL o escriba libremente un destinatario
de correo. Si no existe resultado, el destino queda pendiente de alta y doble
verificación.

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

## 5. Canal correo

La preparación de correo solo se admite si el perfil verificado contiene:

- destinatario exacto;
- marca `verified=true`;
- código de plantilla;
- versión de plantilla.

En este corte no se compone ni envía correo. La ejecución futura deberá exigir,
además del permiso de preparación, permiso de ejecución separado, step-up,
capacidades runtime `outbound_email` y `external_submission`, idempotencia,
adjuntos leídos en servidor directamente desde custodia y registro del mensaje
o evidencia equivalente dentro del expediente.

Un timeout o respuesta SMTP incierta producirá `outcome_unknown`. Nunca habrá
reintento automático cuando pueda haberse producido un efecto externo.

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

