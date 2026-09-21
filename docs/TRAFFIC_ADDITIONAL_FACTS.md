# Incorporación de datos documentales de multas

Revisión: 21/09/2026.

El panel «Hechos del expediente» permite al supervisor incorporar un campo admitido que todavía no existe en la versión activa. El selector agrupa los datos de expediente, lugar y fechas, estacionamiento e importes. Cada incorporación exige valor explícito, original vinculado, página, fragmento y motivo, además de la confirmación personal del supervisor.

La petición conserva el contrato de revisión existente y añade `operation: "add"`. La operación predeterminada sigue siendo `correct`. Añadir un campo existente o corregir uno inexistente devuelve conflicto. La versión esperada, autorización firmada, pago y permisos se comprueban en el servidor. El bloqueo del expediente y una única transacción conservan la versión anterior y revierten la operación completa ante un fallo.

Los campos booleanos no tienen respuesta predeterminada. `false` significa «No», y es distinto de un dato pendiente. El pago reducido corresponde a la multa ante la Administración, separado del pago del servicio RTM.

La nueva versión conserva autor y procedencia. El evento de revisión distingue `added_fields` y `corrected_fields`. Los cambios de expediente, sesión, rol o autorización descartan la edición; un error de guardado exige recargar antes de volver a escribir.

Los datos opcionales confirmados mediante revisión humana de un original se incorporan al borrador local y a su PDF. La guía se actualiza con la nueva versión de hechos. Los borradores anteriores mantienen su historial y quedan desactualizados cuando cambia su origen. La incorporación no cierra hechos ni aprueba o presenta un recurso. La preparación del borrador continúa limitada a la multa ficticia local.

## Verificación

- 73 pruebas Python de revisión, borrador, estacionamiento, familia y alcance de acceso.
- 10 pruebas Node del contrato de datos y respuestas del formulario.
- Seis integraciones con PostgreSQL efímero: persistencia, historial, concurrencia, rollback, guía, borrador y lectura de PDF. Informe `integration-2ecc0b1a0f4c.json`; ninguna prueba usa la base `rtm_local`.
- Cinco recorridos de navegador con API sintética: incorporación, corrección posterior, Sí/No, revocación, conflicto, cambio de expediente/rol y móvil de 390 px. Sin errores de navegador.

La revisión de autorización y de los datos de expedientes corresponde al usuario. Las pruebas no han confirmado datos en su nombre.
