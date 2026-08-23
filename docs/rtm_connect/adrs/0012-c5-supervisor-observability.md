# ADR-0012 · C5 Supervisor Panel de observabilidad

**Estado:** Aceptado para staging sintético.

## Contexto

C1–C4 ya conservan acciones, autorizaciones, intentos, evidencia,
transiciones, tareas manuales, webhooks y reconciliaciones, pero deliberadamente
no publican rutas. La siguiente fase necesita hacer visible ese estado a un
supervisor sin transformar funciones internas de ejecución en comandos HTTP.

Exponer desde el panel operaciones como completar un `manual_handoff` o
reconciliar un webhook no sería una simple mejora de interfaz: esos flujos
pueden alcanzar `confirmed`. El prefijo, el rol del usuario o la existencia de
guards de base de datos no conceden por sí mismos autoridad CORE.

## Decisión

C5 será una proyección HTTP GET-only bajo el prefijo
`/ops/connect/supervisor`. Estará cerrada por feature flag, limitada a staging y
a conectores sintéticos sin credenciales, y exigirá autenticación individual y
permiso vigente `ops.supervise`.

Se publican exactamente estas rutas:

```text
GET /ops/connect/supervisor/status
GET /ops/connect/supervisor/overview
GET /ops/connect/supervisor/attention
GET /ops/connect/supervisor/actions
GET /ops/connect/supervisor/actions/{action_id}
GET /ops/connect/supervisor/manual-tasks
GET /ops/connect/supervisor/webhook-dlq
```

## Reglas

1. Todas las rutas C5 requieren una sesión bearer individual válida.
2. El operador, el rol activo y el permiso `ops.supervise` se comprueban contra
   el estado vigente de PostgreSQL en cada petición.
3. C5 solo está disponible tras el preflight central completo: instancia,
   confirmación, namespace, base conectada, frontend/CORS y rama de staging;
   política `isolated`, entrada `synthetic_only`, datos reales prohibidos y
   todas las capacidades externas desactivadas.
4. La presencia de un conector que no sea de staging, sintético o libre de
   credenciales bloquea el panel completo; no se oculta parcialmente el scope
   inseguro.
   Una acción con `case_id` solo pertenece al scope si el expediente tiene
   `cases.test_mode=true`. Una acción sin conector actual debe conservar al
   menos un intento asociado a un conector sintético elegible para mostrarse.
   Solo se admiten los tuples exactos `synthetic.echo/v1.0`,
   `manual.handoff/v1.0` y `synthetic.webhook/v1.0`, con coherencia entre acción,
   intento, tarea, webhook y reconciliación.
5. C5 no publica `POST`, `PUT`, `PATCH` o `DELETE`.
6. El router no invoca `create_action`, `authorize_action`, `queue_action`,
   `start_attempt`, `record_attempt_outcome`, `record_evidence`,
   `confirm_action`, `complete_manual_handoff`, `reconcile_webhook` ni otros
   mutadores de C1–C4.
7. Los avisos y prioridades son proyecciones técnicas; no autorizan una
   transición, no alteran el riesgo y no deciden estrategia o plazo jurídico.
8. `unknown` nunca se muestra como reintentable a ciegas y
   `retryable_failed` no habilita ejecución desde C5.
9. Las consultas seleccionan únicamente columnas allowlisted. No devuelven
   payloads, destinos, documentos, secretos, configuración, metadatos libres,
   instrucciones, justificantes, storage refs ni detalles libres de motivo.
10. Las colecciones y subhistoriales están paginados o limitados y ordenados de
    forma determinista. La prioridad precede a la paginación y los límites de
    historial conservan los eventos más recientes.
11. Las respuestas protegidas usan `Cache-Control: no-store` y no exponen
    evidencia técnica de acceso.
12. Cada lectura satisfactoria crea un evento de auditoría
    `connect.supervisor.*_viewed` append-only. Su identificador y evidencia no
    se devuelven al cliente; tampoco se confían cabeceras proxy del cliente.
13. La auditoría se escribe exclusivamente en los ledgers existentes de acceso
    de operadores. Ninguna tabla `rtm_connect_*` se modifica por una lectura.
14. C5 no introduce DDL, migración, seed ni conector persistente. Su función DDL
    devuelve una lista vacía y la auditoría de esquema no ofrece `--apply`.
15. C5 no realiza llamadas salientes, no usa proveedores externos y no ejecuta
    presentación, correo, cobro o pago.
16. El manifiesto C0 permanece congelado. Su indicador de runtime no se
    reinterpreta como permiso para publicar ejecución CONNECT: C5 solo publica
    observabilidad supervisora.
17. El prefijo se oculta antes de routing/validación, queda fuera de OpenAPI y
    aplica `no-store` también a errores y métodos no admitidos.
18. La allowlist de columnas se refuerza con una validación recursiva de salida
    que falla cerrada ante material operativo o campos libres no aprobados.

## Consecuencias

El supervisor puede localizar incertidumbre, vencimientos manuales,
reconciliaciones abiertas y webhooks en DLQ sin adquirir autoridad de ejecución.
La lectura queda trazada individualmente y el contenido operativo sensible no
sale de los ledgers autoritativos.

El coste deliberado es que C5 no resuelve desde la interfaz aquello que hace
visible. Cualquier control de reintento, reconciliación, verificación,
confirmación, asignación o gestión de conectores requerirá una fase y un ADR
posteriores con autoridad, idempotencia, evidencia, separación de funciones y
criterios de producción propios.

La fase acepta dos límites de staging: `cases.test_mode` sigue siendo mutable y
el rol PostgreSQL del monolito puede conservar permisos superiores a los de la
proyección. El aislamiento del entorno, la base separada, los scopes exactos y
el smoke transaccional son barreras acumulativas, no sustitutos de procedencia
inmutable/RLS ni de un futuro rol DB con `SELECT` allowlisted e `INSERT` solo en
auditoría. El ingress aplicará rate limiting y observabilidad al prefijo.
