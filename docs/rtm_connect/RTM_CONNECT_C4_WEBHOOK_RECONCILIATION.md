# RTM CONNECT C4 · webhook, UNKNOWN y reconciliación

## Objetivo

C4 demuestra, solo en staging sintético, que RTM puede recibir una observación
asíncrona, deduplicarla, verificar su integridad, correlacionarla de forma
inequívoca con un intento que quedó en `unknown` y reconciliar el resultado sin
repetir la actuación externa.

Un webhook no autoriza ni ejecuta una actuación. Es una observación que CONNECT
debe someter a las mismas barreras de identidad, evidencia y auditoría que el
resto del kernel.

## Cadena

```text
intento ya ejecutado → UNKNOWN
→ webhook sintético recibido
→ integridad verificada
→ correlación exacta con acción e intento originales
→ RECONCILING, sin crear otro intento
→ resultado clasificado
→ evidencia E4 exacta si el resultado es confirmed
→ CORE puede confirmar
```

## Bandeja, deduplicación y DLQ

La identidad de entrada es el par exacto formado por
`ingress_connector_id + source_event_id`. El primer ingreso congela la
identidad y el SHA-256 del contenido normalizado:

- una repetición idéntica reutiliza la misma fila y solo incrementa el contador
  de replay;
- el mismo identificador con contenido distinto es un conflicto, nunca una
  actualización silenciosa;
- un mensaje que no puede verificarse o correlacionarse puede terminar en
  `dead_lettered`, que es la DLQ auditable de C4;
- `processed` y `dead_lettered` son estados terminales.

La prueba `synthetic_integrity_hash_v1` solo valida el contrato determinista de
staging. No es una firma criptográfica de proveedor ni autoriza a afirmar que
exista seguridad de webhook real.

## Correlación exacta

No se busca por una referencia ambigua ni se acepta el registro más reciente.
Para hacer `match` deben coincidir conjuntamente:

- `action_id` y `attempt_id` declarados;
- conector de origen y su versión;
- `request_sha256`;
- `external_reference`;
- estado `unknown` de la acción y del intento;
- `reconciliation_required=true` y soporte de reconciliación.
- conector de origen activo, de staging, sintético y sin credenciales.

El conector de ingreso del webhook es distinto del conector de origen del
intento. Ingress describe quién entrega la observación; origin describe quién
ejecutó la actuación. C4 bloquea cualquier intento de confundir ambos papeles.

## UNKNOWN nunca se reintenta a ciegas

La reconciliación no llama a `queue_action`, no llama a `start_attempt` y no
ejecuta el conector de origen. Reutiliza el intento desconocido exclusivamente
como identidad auditable. Los resultados admitidos son:

- `confirmed`;
- `retryable_failed`;
- `unknown`;
- `manual_review`;
- `permanent_failed`.

Solo una clasificación expresa como `retryable_failed` puede habilitar un flujo
posterior de reintento. Si la observación continúa siendo `unknown`, permanece
obligatoria la reconciliación.

Un expediente ya `resolved` se puede consultar de forma idempotente. Un
expediente durable que todavía esté `started` se comunica expresamente como
reconciliación en curso; nunca se devuelve como si fuera un resultado final.

## Evidencia E4 exacta

`confirmed` exige material de justificante sintético y genera E4. Deben
coincidir el hash de solicitud, la referencia externa, el hash de recibo, la
referencia de almacenamiento `synthetic://webhook/` y el intento original.
El motor pasa al kernel el `evidence_id` concreto creado por esa reconciliación;
no confirma usando simplemente la última evidencia disponible. El guard de
base de datos exige además que la resolución coincida con el resultado del
webhook y que el hash y el almacenamiento E4 sean exactamente los observados.

## Persistencia

- `rtm_connect_webhook_inbox`: sobre mutable con identidad congelada, estado,
  replay y DLQ.
- `rtm_connect_webhook_events`: historial append-only del sobre.
- `rtm_connect_reconciliations`: expediente durable de clasificación.
- `rtm_connect_reconciliation_events`: historial append-only del expediente.

Las migraciones son aditivas. No siembran conectores persistentes ni publican
rutas.

## Alcance de staging

C4 es `synthetic-only`, sin datos reales y sin efectos externos. No incluye
rutas HTTP, modificaciones de `app.py`, red, webhooks externos ni secretos de
proveedor.
No envía correo, no usa Stripe, no presenta escritos, no cobra y no cambia
ningún estado legal por sí solo. Los conectores y registros del smoke viven en
una transacción y desaparecen mediante rollback.

## Criterio de cierre

Esquema aditivo; preflight de solo lectura con `safe=true`; smoke transaccional
con deduplicación, conflicto de replay, correlación exacta, DLQ, bloqueo de
reintento ciego y confirmación E4; cero residuo sintético tras rollback; suite de
regresión completa; `/health` correcto y restore remoto verificado.
