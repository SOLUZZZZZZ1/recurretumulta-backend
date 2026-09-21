# Prueba local de Bancos — 18 de septiembre de 2026

Expediente creado una sola vez desde el navegador:
`0f244a70-b1ae-4e1b-9018-7a0153a47550`.

## Comprobado en Windows

- Alta con los datos ficticios de LOCAL_WORKFLOW y ambos PDF de identidad.
- Descarga efectiva en Downloads de
  `rtm_0f244a70-b1ae-4e1b-9018-7a0153a47550.pdf` (2506 bytes).
  Texto comprobado: `PRUEBA LOCAL SIN VALIDEZ`, identidad ficticia y mismo case_id.
- Candidato `04_candidato_autorizacion_prueba.pdf` recibido por la interfaz como
  pendiente de revision, sin firma verificada ni habilitacion de pagos.
- Cuatro objetos custodiados: dos identidades, PDF emitido y candidato.
- GET autenticado public-status: HTTP 200, authorized=false,
  payment_status vacio, status=authorization_pending.
- Ese resumen comun devuelve authorization_evidence_status=not_submitted:
  todavia no proyecta la evidencia generica local del candidato. No interpretar
  ese campo como perdida del archivo ni como verificacion de firma.

## Fallo encontrado y correccion preparada

La subida de `03_comision_bancaria_prueba.pdf` rechazo la peticion con
external_capability_unavailable, antes de custodiarla: intake_router exigia B2.
La ruta ahora admite el perfil generico local, comprueba identidad de la base y
expediente sintetico de consumo tanto antes de subir como bajo lock al persistir.
Fuera del perfil local conserva la exigencia de B2. No activa servicios externos.

Se corrigio ademas la ruta Windows del test ops_local_presenter_boundary usando
fileURLToPath. El lanzador V2 incluye ambas correcciones y las nuevas pruebas;
su validacion de huellas informa cero cambios pendientes. Copia previa en
RTM_LOCAL_OPS_RESPALDO/RTM_LOCAL_OPS_V2.before_bancos_append.py.

Validacion: 62 pruebas backend (2 omitidas), 37 frontend (el unico fallo de ruta
corregido y vuelto a ejecutar correctamente), y 10 pruebas de subida/atomicidad
tras la correccion. git diff --check sin errores.

## Pendiente para continuar

Tras el reinicio confirmado por el usuario (uvicorn PID 16024), se reintento
el documento adicional sobre el mismo expediente. La interfaz confirmo
`Documentacion recibida correctamente` y navego al resumen. Hay cinco objetos
en custodia: las dos identidades, PDF emitido, candidato y documento principal.
La correccion de subida queda comprobada en Windows con el servidor real.

1. Confirmar en la sesion OPS del usuario que aparece en Bancos. El navegador
   automatizado tiene otra sesion y requiere login individual.
2. Revisar la proyeccion del candidato generico local en el resumen comun:
   sigue solicitando completar la autorizacion pese al candidato recibido.
   No repetir el alta ni interpretar este resumen como firma verificada.

Stripe y el segundo cobro siguen pendientes y desactivados. No se han creado
productos, precios, sesiones o enlaces ni realizado despliegues, commits o push.

## Consultas repetidas del resumen

El registro aportado por el usuario muestra consultas public-status repetidas
hasta recibir 429, mientras login, workspace y documentos OPS responden 200.
ResumenExpediente hacia hasta cuatro consultas por ciclo cada cinco segundos
cuando no habia pago ni autoridad verificada. Se elimina ese sondeo: consulta
al abrir y al pulsar Revisar de nuevo, con exclusion de peticiones simultaneas
y cancelacion al salir o cambiar de expediente. Un 429 no activa fallback.

Verificado en el navegador real: doble clic, una sola peticion (429 por el
limite ya acumulado) y ninguna repeticion durante 12 segundos. Las 38 pruebas
seleccionadas de contratos frontend/intake/vehicle pasan. Vite carga el cambio
sin reiniciar backend. Lanzador actualizado y huellas verificadas.
La proyeccion del candidato local sigue pendiente; este cambio no modifica
pagos, firma ni presentacion.

## Correccion de la proyeccion preparada

Se incorpora has_pending_local_candidate al resumen CORE, exclusivamente en el
perfil local y para el tipo documental del candidato generico. Comprueba el
snapshot vigente, la emision verificada, la evidencia HMAC de recepcion, su
vinculo con el PDF/nonce emitidos y el registro documental. Solo proyecta
pending_review; nunca autoridad verificada. Evidencia alterada, de otro caso,
obsoleta o sin documento no acredita recepcion vigente.

27 pruebas de candidato, autorizacion generica, subida y atomicidad pasan.
Lanzador V2 actualizado, con huellas validadas y copia previa conservada.
Tras reiniciar V2 y confirmar OPS_LOCAL_LISTO, comprobado contra el servidor
real: public-status devuelve HTTP 200, status=documents_received,
authorization_evidence_status=pending_review y authorization_candidate_received=true.
authorized, signed_authority_verified, ready_for_review_payment y review_paid
siguen false; payment_status sigue vacio. El navegador muestra Autorizacion
pendiente de revision, Pendiente de revision humana y Pago Pendiente. Ya no
solicita volver a completar la autorizacion ni ofrece un boton de pago.

## Comprobaciones posteriores en navegador

- Resumen de Bancos: doble clic en Revisar de nuevo produce una sola peticion
  HTTP 200, sin repeticion durante 12 segundos y sin errores de navegador.
  Se mantiene candidato pendiente de revision y pago pendiente. Esto completa
  la comprobacion anterior que aun recibia el 429 acumulado.
- Las entradas de Energia, Telecomunicaciones y Seguros muestran su familia
  correcta, tipo Consumo, aviso de datos ficticios y alta generica local.
  Solo se comprobo la entrada; no se crearon expedientes de esas familias.
- OPS en el navegador automatizado sigue en login. Se solicito al usuario
  confirmar familia Bancos y cinco documentos en su propia sesion, sin pedir
  credenciales. Esa confirmacion sigue pendiente.

## Confirmacion del usuario y fase juridica

El usuario confirma en su OPS: Documentos 5, Eventos 6, Hechos pendientes,
Familia juridica pendiente, Sin especialista y Previa Juridica pendiente.
Queda confirmada la visibilidad documental en OPS; familia juridica no equivale
al area publica Bancos.

Revision del codigo: create_validated_facts exige payment_status=paid (402 en
caso contrario) y authorized=true (409 si falta). Resolver familia exige hechos
activos y congelados; generar previa exige familia activa bloqueada y esos
mismos hechos. El caso local conserva pago pendiente y autoridad no verificada,
por lo que no procede avanzar esos estados ni simular un pago en la base.

Validacion aislada: 40 pruebas correctas de especialistas Bancos, Energia,
Telecomunicaciones, Seguros y familia CORE. Otras 4 pruebas de integracion del
gateway se omiten porque requieren una base PostgreSQL temporal configurada
expresamente. No se han ejecutado contra rtm_local ni alterado el expediente.
