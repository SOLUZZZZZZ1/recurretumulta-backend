# Preparación de multas: conservación de pendientes y procedencia

Fecha: 20 de septiembre de 2026.

## Cambio preparado

El adaptador Reanalysis -> ValidatedFacts pasa a
`rtm_reanalysis_to_validated_facts_v1_1`. Su versión declarada se actualiza en
`rtm_core/versioning.py`.

- Los campos declarados ausentes o no resueltos se conservan aunque no haya
  valor candidato. Se combinan los avisos del paquete y del evento, con la
  misma lista permitida de campos documentales.
- Los conflictos declarados sin candidato se conservan como conflictos,
  sin inventar valor, confianza, documento ni página. Cero puntos sigue
  siendo un valor en conflicto y no se sustituye por ausencia.
- `fecha_notificacion`, `notification_date` y `fecha_recepcion` se trasladan
  a un campo propio, separado de la fecha de emisión. Una lectura de IA
  sigue pendiente de revisión humana incluso con confianza 1.0.
- La página de origen solo se indica cuando el paquete conserva una única
  página inequívoca de un único documento. Un paquete multipágina o con
  varios documentos no asigna la última página a todos los hechos.
- La proyección de orientación OPS recibe los pendientes tras serializar
  el contrato de hechos. No se calculan vencimientos a partir de fechas
  de notificación ni se otorga autoridad para generar o presentar.

## Verificación

Antes de cambiar el código, las 41 pruebas iniciales existentes pasaban.
La nueva batería reprodujo pérdida de datos pendientes, pérdida de la fecha
de notificación y atribución incorrecta de páginas en el código anterior.

La batería ampliada de la copia candidata ejecutó 88 pruebas, todas correctas,
sin omisiones: 15 nuevas regresiones y 73 existentes. Incluye contratos,
adaptación de lecturas, repositorio de hechos, espacio OPS, clasificación,
previa jurídica, generación, registro de versiones y atomicidad de Reanalysis.

Pruebas añadidas: `tests/test_reanalysis_review_gaps.py`.
Son pruebas de código y proyección con datos sintéticos; no constituyen una
prueba completa contra PostgreSQL ni una comprobación visual del navegador.

## Límites y continuidad

Este cambio afecta a nuevas previsualizaciones/borradores. No reescribe hechos
ya guardados ni altera aprobaciones anteriores. Las versiones existentes deben
seguir su procedimiento explícito de revisión e invalidación si procede.

El recorrido operativo local de multas sigue pendiente de completar y probar.
La autorización local y la carga complementaria actualmente preparadas para
Consumo no equivalen a un circuito de autorización de multas. El Reanalysis
operativo exige pago y autorización y rechaza test_mode; esas barreras no se
han retirado. Para las pruebas siguientes hace falta un circuito sintético
aislado y explícito, sin marcar pagos o autorizaciones como verificados.

También quedan el presupuesto final de multa ligado al expediente (39 EUR
totales, 10 EUR iniciales descontables y 29 EUR de saldo cuando corresponda),
la comprobación completa del recurso y anexos, y la presentación manual con
justificante y versión exacta. Véase `TRAFFIC_FINAL_PAYMENT_HANDOFF.md`.

No se modifica la base de datos, no se llama a proveedores de IA, no se cobran
pagos y no se despliega. La aplicación local necesitará reiniciarse si conserva
un proceso que cargó la versión anterior del adaptador.
