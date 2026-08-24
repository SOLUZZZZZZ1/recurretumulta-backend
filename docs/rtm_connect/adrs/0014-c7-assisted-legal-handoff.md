# ADR-0014 · C7 handoff jurídico asistido

**Estado:** Aceptado para staging sintético.

## Contexto

C3 demostró un handoff manual R3 y C6 una frontera HTTP R1 controlada. C7 debe
demostrar el modo `assisted` en una actuación R4 sin afirmar que RTM presenta
ante una Administración ni convertir el panel supervisor GET-only en un
runtime de ejecución.

Reutilizar `manual.handoff` ocultaría diferencias esenciales: C7 exige doble
aprobación CORE, gate humano final, revisión y liberación separadas, paquete
hash-bound y una rama UNKNOWN auditable.

## Decisión

Se congela `assisted.legal/v1.0`, modo assisted, capacidad
`administration.submit.legal.assisted`, riesgo exacto R4 y evidencia mínima E4.
Solo opera con datos sintéticos, sin expediente, red, credencial, ruta o efecto
externo. El acto final queda reservado a una persona y el paquete declara
literalmente `HUMAN_FINAL_SUBMIT_REQUIRED`.

C7 añade `rtm_connect_assisted_tasks` y
`rtm_connect_assisted_events`. No modifica las tablas C3. CORE debe emitir la
acción y el grant congelados; el workflow los valida antes de DML y el kernel
C1 los persiste dentro de la misma transacción que prepara el handoff.

## Reglas

1. CORE aporta una autorización R4/E4 para el único modo assisted, con efecto
   legal autorizado y dos aprobadores distintos del solicitante.
2. CONNECT verifica acción y grant persistidos; no decide estrategia,
   Administración, plazo, texto o si debe presentarse.
3. El paquete solo contiene identificadores, hashes, checklist fijo, plazo y
   gate humano. Queda congelado mediante SHA-256.
4. Existe una tarea única por acción e intento y un evento secuencial
   append-only por transición.
5. El asignado revisa; un segundo operador libera; un tercero verifica. Estas
   tres identidades son distintas.
6. Liberar no equivale a presentar. Empezar el paso humano no abre una sede ni
   crea un efecto externo desde RTM.
7. La atestación sintética produce E3; verificar hash, referencia, paquete y
   gate produce E4.
8. CORE confirma con el `evidence_id` E4 concreto ligado a la tarea e intento.
9. Una ambigüedad produce UNKNOWN. No se crea otro intento ni se repite el acto
   a ciegas.
10. La reconciliación C7 es humana, sin red, y reutiliza el intento original;
    puede aportar recibo, mantener UNKNOWN o clasificar revisión manual/fallo.
11. Package, assignment, attestations y evidence links son inmutables o
    write-once; los replays exactos son idempotentes y los cambios conflictivos
    se bloquean.
12. C7 no publica rutas, no modifica `app.py`, no se siembra y permanece
    default-off.
13. El smoke es transaccional; no deja conector, acción, intento, tarea, evento,
    operador, rol o evidencia sintética.
14. La allowlist C5 no cambia: un tuple C7 persistente sigue siendo un fallo
    cerrado.

## Consecuencias

C7 prueba una cadena asistida R4 más estricta que C3 sin presentar realmente.
La base conserva quién preparó, revisó, liberó, realizó el paso humano y
verificó la atestación, además de la rama UNKNOWN sin retry ciego.

La fase no hace utilizable el conector por HTTP ni acredita compatibilidad con
DGT, OEPM u otra sede. Publicar operaciones, usar documentos reales, almacenar
artefactos fuera del esquema sintético o ejecutar una presentación exige C8,
un nuevo ADR, permisos mutadores, procedencia documental, almacenamiento,
egress, evidencia real y despliegue controlado.
