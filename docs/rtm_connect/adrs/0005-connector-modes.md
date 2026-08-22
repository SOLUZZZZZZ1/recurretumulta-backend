# ADR-0005 · Modos de conector

**Estado:** Aceptado y congelado en C0.

CONNECT admite API, webhook, polling, batch, assisted y manual. Todos usan el
mismo contrato y devuelven el mismo resultado normalizado. Un flujo manual no es
una excepción sin trazabilidad: es un conector normalizado con tarea, operador,
evidencia y confirmación.
