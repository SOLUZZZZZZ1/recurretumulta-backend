# ADR-0001 · Límite de autoridad CORE ↔ CONNECT

**Estado:** Aceptado y congelado en C0.

CORE es la autoridad que decide y congela la actuación. CONNECT no puede elegir
familia, especialista, estrategia, fundamento, plazo, importe autorizado ni si
debe presentarse. CONNECT verifica la autorización, ejecuta el alcance exacto y
devuelve un resultado técnico respaldado por evidencia.

Una respuesta HTTP 200 no cambia por sí sola el estado jurídico. CORE solo puede
hacerlo después de un resultado `confirmed` y de la evidencia exigida.
