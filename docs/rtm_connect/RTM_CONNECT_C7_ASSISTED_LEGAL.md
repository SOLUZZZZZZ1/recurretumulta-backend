# RTM CONNECT C7 · handoff jurídico asistido

## Objetivo

C7 demuestra en staging sintético un conector de modo `assisted` para una
actuación jurídica R4. RTM prepara y congela un paquete de identificadores,
hashes y comprobaciones; tres funciones humanas separadas revisan, liberan y
verifican la atestación. **La presentación final sigue siendo humana y C7 no la
ejecuta.**

La regla C0 permanece intacta:

**CORE autoriza; CONNECT prepara y registra; la evidencia confirma; solo
entonces CORE puede cambiar el estado jurídico.**

C7 no elige Administración, procedimiento, estrategia, fundamento, plazo o
documento. Tampoco abre una sede, redacta contenido jurídico, publica una ruta,
usa credenciales o contacta un organismo real.

## Tuple y autoridad exactos

El único conector C7 v1 es:

- código/versión `assisted.legal/v1.0`;
- modo `assisted`;
- capacidad `administration.submit.legal.assisted`;
- satélite `rtm.legal.assisted`;
- destino `administration.synthetic.filing` / `synthetic-c7-administration`;
- riesgo exacto `R4_critical_regulated`;
- evidencia exigida `E4_receipt_verified`;
- `synthetic_only=true`, sin red, credencial, ruta o efecto externo;
- idempotencia obligatoria y sin reconciliación automática.

CORE debe suministrar la acción y la autorización congelada. La autorización
usa exclusivamente el modo assisted, declara efecto legal, exige E4 y contiene
al menos dos aprobadores distintos. El solicitante no puede ser uno de esos
aprobadores. La acción no contiene expediente, correlación o datos reales;
lleva entre uno y ocho hashes documentales sintéticos y el payload allowlisted
de C7.

`prepare_assisted_legal` valida ambos contratos antes de DML y los persiste por
el kernel C1 dentro de la misma transacción que crea el handoff. Después vuelve
a leer y comparar el grant congelado en PostgreSQL. CONNECT no autoemite una
autorización ni sustituye la doble aprobación CORE.

## Paquete y checklist congelados

El paquete `rtm.assisted.legal.package.v1` contiene únicamente:

- identificadores de acción, intento y autorización;
- hash exacto de la solicitud y hashes documentales;
- fecha operativa `due_at`;
- checklist fijo versionado;
- gate literal `HUMAN_FINAL_SUBMIT_REQUIRED` y su SHA-256;
- marcadores inequívocos de staging sintético y ausencia de efectos.

No contiene cuerpos documentales, texto jurídico libre, secretos, destinatarios
reales ni una decisión legal. Su manifest y SHA-256 se congelan al preparar la
tarea y los guards PostgreSQL impiden sustituirlos.

El checklist fijo verifica autorización CORE congelada, hashes documentales,
identidad humana asignada, gate humano final y captura de atestación sintética.
La revisión produce una atestación hash-bound; no modifica el paquete.

## Flujo normal

```text
acción y grant R4 congelados emitidos por CORE
→ prepared
→ assigned
→ reviewing
→ ready_for_release
→ released
→ in_progress
→ awaiting_receipt
→ receipt_submitted + E3
→ verified + E4
→ completed + confirmación CORE con la E4 exacta
```

1. `prepare_assisted_legal` valida la frontera staging y la autoridad exacta,
   reclama idempotencia, crea un único intento y congela paquete, plazo y gate.
2. `begin_assisted_review` solo permite trabajar al operador asignado.
3. `attest_assisted_review` fija el resultado del checklist y deja el paquete
   listo para liberación.
4. `release_assisted_legal` exige un operador distinto del asignado y una
   atestación de liberación ligada al mismo paquete y gate.
5. `begin_assisted_execution` registra el comienzo del paso humano. No abre una
   sede ni envía el paquete.
6. `mark_assisted_awaiting_receipt` registra que se espera exclusivamente una
   atestación sintética.
7. `submit_assisted_receipt` captura hash, referencia y almacenamiento
   `synthetic://assisted-legal/`, crea E3 y no acepta
   `legal_submission_executed=true`.
8. `verify_assisted_receipt` exige un tercer operador distinto del asignado y
   del liberador. Compara hash, referencia, paquete y gate, y produce E4.
9. `complete_assisted_legal` usa el identificador exacto de esa E4 para que CORE
   confirme; nunca confirma por “la última evidencia”.

Asignación, revisión, liberación y evidencias son write-once. Los replays
exactos reutilizan el mismo estado; cambiar paquete, operador, atestación o
justificante produce conflicto.

## Rama UNKNOWN

Si tras empezar el paso humano no puede saberse si ocurrió el acto simulado,
`mark_assisted_outcome_unknown` lleva acción e intento a `unknown` y conserva
una referencia sintética auditable. C7 nunca repite el acto a ciegas.

```text
in_progress
→ outcome_unknown
→ reconciling
→ receipt_submitted + E3 → verified + E4 → completed
                     ↘ outcome_unknown | manual_review | permanent_failed
```

`begin_assisted_reconciliation` reutiliza el intento original y no crea otro.
La reconciliación C7 es humana y sin red: una atestación sintética exacta puede
resolver el expediente; la ausencia de prueba permanece unknown o deriva
expresamente a revisión manual/fallo permanente. No existe retry automático.
`resolve_assisted_reconciliation` registra esa clasificación sin aportar un
recibo, sin crear otro intento y sin reenviar el acto.

## Separación de funciones R4

C7 acumula barreras distintas:

- CORE aporta dos aprobadores diferentes;
- el solicitante es distinto de esos aprobadores;
- el operador asignado revisa y realiza el paso humano simulado;
- otro operador libera el paquete;
- un tercer operador verifica la atestación y completa la tarea.

Los constraints impiden que asignado, liberador y verificador coincidan. La
separación operativa no sustituye la doble aprobación CORE; ambas son exigidas.

## Persistencia

C7 añade únicamente dos tablas:

- `rtm_connect_assisted_tasks`: estado versionado, asignación, autorización,
  plazo, paquete y gate congelados, atestaciones write-once y referencias a E3
  y E4.
- `rtm_connect_assisted_events`: historial secuencial append-only de toda
  transición.

Acciones, autorizaciones, intentos, idempotencia, evidencia y transiciones se
reutilizan de C1. Las tablas C3 no se reutilizan ni se alteran: manual y assisted
comparten contrato normalizado, no una máquina de estados falsa.

El DDL es aditivo, idempotente y no destructivo. La aplicación requiere
`--apply --confirmation STAGING_CONNECT_C7_SCHEMA_ONLY`. No siembra conectores
ni datos.

## Superficie y efectos

C7 v1 queda cerrado por defecto. `RTM_ENABLE_CONNECT_C7_ASSISTED` debe permanecer
desactivado. No modifica `app.py`, no aparece en OpenAPI y no publica ruta,
worker, panel mutador, webhook o endpoint de presentación.

El conector existe solo dentro del smoke transaccional y desaparece mediante
rollback. No usa red, B2, proveedor documental, correo, Stripe, pagos o
presentación externa. No conserva endpoint, secreto o `credential_ref`.

La allowlist C5 no se amplía. Por ello ningún conector o registro C7 puede
quedar persistente: contaminaría el scope supervisor y debe fallar cerrado.

## Criterio de cierre

1. esquema aditivo C7 aplicado con confirmación exacta, migración registrada y
   auditoría de columnas, índices, triggers y constraints completa;
2. preflight read-only con manifiestos congelados, R4/E4/doble control exactos,
   feature default-off, grafo sin C7 y cero endpoint/secret dormido;
3. cero conector, acción, intento, tarea o evento C7 persistente antes y después
   del smoke;
4. smoke normal transaccional con paquete congelado, separación triple, E3,
   E4 exacta y confirmación CORE;
5. smoke UNKNOWN sin segundo intento ni retry ciego y reconciliación humana
   sobre el intento original;
6. tampering, replay conflict, misma persona en funciones incompatibles,
   evidencia incorrecta y mutación de ledgers bloqueados;
7. rollback completo, regresión C0–C6, `/health` correcto y restore remoto
   verificado.
