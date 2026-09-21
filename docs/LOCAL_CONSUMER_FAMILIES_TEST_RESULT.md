# Pruebas locales adicionales de Consumo

18 de septiembre de 2026. Datos sinteticos, documento RTMTEST001 y correos
@example.com. Se conserva Bancos sin repetir su alta.

| Familia | Expediente | Resultado |
| --- | --- | --- |
| Energia | e67211ba-a988-4575-9576-766a6b612753 | Alta, PDF descargado, candidato y documento adicional; resumen pendiente de revision, pago pendiente |
| Telecomunicaciones | 416ee975-f054-4a08-aad2-deb9e42b7bb2 | Alta, PDF descargado, candidato y documento adicional; resumen pendiente de revision, pago pendiente |
| Seguros | 35567a72-bb37-40e3-9456-8bc8fb9cb4f9 | Alta y tres objetos locales; sesion de navegador perdida antes de completar candidato y documento adicional |

Energia y Telecomunicaciones conservan cinco objetos cada una. Se leyeron sus
PDF descargados y se comprobo la marca PRUEBA LOCAL SIN VALIDEZ y el case_id
correspondiente. Los resumenes no ofrecen pago ni dan la firma por verificada.

En Seguros se perdio la conexion del navegador de automatizacion. Tras abrir
Edge de nuevo, la capacidad de acceso del expediente ya no estaba disponible:
el resumen informa Falta el acceso valido para este expediente. Hay tres
objetos en su carpeta de custodia; no se encontro el PDF en Downloads. No se
repite el alta, no se falsifica acceso y no se modifican objetos internos.
Pendiente recuperar acceso por un flujo autorizado o continuar desde OPS.

Archivos de prueba adicionales en D:/rtm/PRUEBAS_RTM:
05_documento_prueba_energia.pdf, 05_documento_prueba_telecomunicaciones.pdf y
05_documento_prueba_seguros.pdf. Son documentos genericos ficticios con marca
de prueba, no facturas ni contratos reales. fill_consumer_test.js facilita el
relleno del formulario local y no envia el alta por si mismo.

No se han habilitado Stripe, proveedores documentales externos, firma ni
presentacion. Tampoco se ha confirmado la visibilidad OPS de estas tres altas.

## Recuperacion local preparada

El usuario confirma Seguros en OPS con tres documentos y tres eventos: dos
identidades y PDF emitido, sin candidato ni original. Se prepara POST
/ops/core/cases/{case_id}/recover-local-access, limitado al perfil local,
supervisor con sesion individual, base local verificada y snapshot sintetico
de Consumo apto. Audita local_case_access_recovered sin guardar el token y
responde no-store. No modifica pago ni autoridad; tampoco revoca accesos previos.

OPS incorpora Retomar prueba local de Consumo: recupera acceso en sessionStorage
del navegador actual, prepara emision idempotente, permite descargar PDF y
recibir candidato, y enlaza a la subida del documento principal del mismo caso.
No muestra tokens ni genera enlaces externos. El componente esta limitado al
perfil local y al supervisor; el backend decide si el caso es apto.

38 pruebas backend y 32 frontend correctas, incluyendo rechazos de recuperacion
y compilacion/renderizado del panel local. Lanzador actualizado y huellas
validadas. Pendiente reiniciar V2 y probar el boton desde la sesion OPS del
usuario; no se ha recuperado aun el acceso de Seguros en un navegador real.
