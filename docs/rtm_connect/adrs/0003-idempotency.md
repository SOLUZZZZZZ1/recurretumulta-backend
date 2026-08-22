# ADR-0003 · Idempotencia anterior al efecto

**Estado:** Aceptado y congelado en C0.

La clave idempotente deriva de versión, autoridad, capacidad, destino, hash del
payload y hashes documentales. Debe calcularse antes de toda llamada externa.
Cambiar el payload o un documento cambia la clave. La idempotencia no sustituye
la reconciliación cuando el resultado es desconocido.
