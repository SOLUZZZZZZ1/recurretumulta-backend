# Preparación de estacionamiento

Revisión: 21/09/2026. Alcance: ayuda documental para el supervisor, sin decisión ni presentación automática.

## Comportamiento

El resolver reconoce una conducta explícita de estacionamiento al comienzo del hecho documental validado. Conserva las referencias de origen y deja la familia sin bloquear. Las menciones incidentales, negaciones y etiquetas de formularios no activan esta regla. Si concurren otras infracciones específicas, se conserva el conflicto.

La guía reúne ocho áreas de revisión: trámite, plazo, lugar y momento del hecho, precepto y ordenanza, condiciones del estacionamiento, pruebas, pago de la multa y motivo/petición. Solo muestra valores validados, sin conflictos y con referencia documental, página y evidencia. Una casilla con datos no significa que la valoración jurídica esté concluida.

El pago del servicio RTM no alimenta el dato `pago_multa_reducido`. Este último requiere un valor booleano documental propio. La fecha del documento no sustituye a la del hecho. La fase no se deduce de una fecha ni de palabras sueltas en OCR. La ausencia de un dato en RTM no se trata como ausencia de prueba administrativa.

En la multa ficticia local, la guía se muestra después de verificar la autorización y los siete hechos. El supervisor puede añadir las tareas a las notas del borrador, preservando los motivos, petición y texto previo. Esto desmarca la confirmación y no guarda automáticamente. El siguiente guardado conserva la guía como parte del evento firmado; su contenido forma parte de la huella del origen. Cambiar hechos, documentos, requisitos o la guía exige revisar la versión. Las versiones anteriores mantienen su historial.

CORE incorpora `traffic.estacionamiento` como preparación estructurada. Exige hechos congelados y familia bloqueada de la misma cadena, con sus huellas verificadas. Produce una previa en borrador, ocho comprobaciones bloqueantes, sin alegaciones ni peticiones inventadas y con el plazo sin confirmar. No modifica hechos, familia, aprobaciones o estados. La redacción, revisión y aprobación jurídica siguen pendientes; registrar este preparador no convierte el caso en listo para generar o presentar.

## Fuentes y fecha aplicable

Consulta realizada el 21/09/2026:

- [Ley de Tráfico, art. 88](https://www.boe.es/buscar/act.php?id=BOE-A-2015-11722#a88): valorar la denuncia y las pruebas según quién denuncie. La falta de fotografía no determina por sí sola la invalidez.
- [Ley de Tráfico, arts. 93–96](https://www.boe.es/buscar/act.php?id=BOE-A-2015-11722#a93): verificar trámite y efectos del pago reducido antes de orientar el escrito.
- [Reglamento General de Circulación, art. 94](https://www.boe.es/buscar/act.php?id=BOE-A-2003-23514#a94): contrastar la prohibición concreta y las condiciones del estacionamiento.
- [Ley 39/2015, art. 53](https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565#a53): acceso del interesado a la documentación del procedimiento.

La consulta del BOE no valida una ordenanza local ni su aplicación al caso. El operador debe comprobar la redacción que corresponde a los hechos; el consolidado puede mostrar modificaciones con entrada en vigor posterior. No se incorporan cálculos de plazos ni conclusiones automáticas de caducidad, nulidad o estimación.

## Validación

- Pruebas de familia, procedencia, datos desconocidos, negaciones, conflictos, pago reducido y fase.
- Regresiones de los especialistas existentes e inventario de versiones.
- PostgreSQL temporal aislado: guía firmada junto al borrador, inclusión de notas en PDF, cambios de origen, concurrencia, integridad, rollback y custodia; sin tocar `rtm_local`.
- Navegador con datos sintéticos: ocho comprobaciones, conservación de textos, ausencia de duplicados, confirmación tras editar, PDF versionado, aislamiento entre expedientes y adaptación móvil.

La comprobación visual usa respuestas ficticias. No acredita revisión jurídica ni aprobación de documentos por parte del supervisor del expediente local.
