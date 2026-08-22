# ADR-0009 · Primer conector: `synthetic.echo`

**Estado:** Aceptado para RTM CONNECT C2.

El primer conector del sistema será determinista, sintético y sin red. Se elige
antes de integrar proveedores para validar la orquestación real del Kernel C1,
la idempotencia, los intentos, la evidencia, `unknown` y la reconciliación.

Una repetición de una acción ya confirmada devuelve el mismo expediente lógico
sin crear otro intento. Una repetición de una acción no terminal se bloquea para
impedir ejecución ciega. `unknown` solo puede avanzar mediante reconciliación.

El conector no se siembra de forma persistente en C2. Preflight exige cero
conectores reales y cero registros persistentes de `synthetic.echo`. El smoke se
ejecuta dentro de una transacción y revierte todos los datos sintéticos.
