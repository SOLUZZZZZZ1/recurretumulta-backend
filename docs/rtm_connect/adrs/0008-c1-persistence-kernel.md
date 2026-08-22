# ADR-0008 · Persistencia del Kernel C1

**Estado:** Aceptado para RTM CONNECT C1.

C1 separa siete registros: conectores, acciones, autorizaciones, intentos,
evidencia, transiciones e idempotencia. La acción mantiene el estado actual,
pero el historial autoritativo se conserva append-only en transiciones.

La base impide transiciones que contradigan la máquina de estados de C0. Las
autorizaciones son inmutables; la evidencia y el historial tampoco se editan.
Un intento técnico nunca equivale por sí solo a confirmación jurídica.

C1 no publica rutas y no ejecuta conectores. Su smoke utiliza un conector
`synthetic.echo`, trabaja dentro de una transacción y termina con rollback.
