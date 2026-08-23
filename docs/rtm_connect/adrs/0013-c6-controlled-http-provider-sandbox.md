# ADR-0013 · C6 proveedor sandbox HTTP controlado

**Estado:** Aceptado para C6.

## Contexto

El manifiesto C0 ordena `C6_first_provider_sandbox`, pero el repositorio no
identifica proveedor, API, sandbox, credenciales ni semántica de recibos.
Elegir un vendor sin esa autoridad introduciría una afirmación falsa y un borde
de seguridad no revisado.

## Decisión

C6 congela `controlled.sandbox/v1.0`: un probe HTTP R1 exclusivamente sintético
y controlado por RTM. Valida el contrato de red, secreto por referencia,
idempotencia, E2 y reconciliación sin atribuirse compatibilidad con ningún
proveedor real.

El request remoto se deriva solo de la acción congelada y no contiene
`attempt_id`; así la misma clave C0 siempre conserva el mismo body. El sandbox
deduplica por `Idempotency-Key` y cuerpo, rechaza un conflicto sin repetir el
efecto y se consulta por `action_id` mediante un GET observacional.

Una respuesta aceptada y exactamente correlacionada aporta E2. HTTP 200 no es
evidencia suficiente por sí mismo. Ambigüedad de transporte produce UNKNOWN y
nunca un retry ciego. Solo CORE confirma con el identificador exacto de E2.

El valor de la credencial se resuelve inmediatamente antes del transporte,
nunca se persiste y nunca aparece en metadata, serialización o cadenas de
errores. El endpoint externo de esta versión usa `.invalid` como marcador, pero
todo transporte no loopback se bloquea en código antes de DNS; el smoke exige
IP loopback literal y no puede activarse por entorno.

Antes de DML y antes del socket se valida la frontera staging, la rama runtime,
el nombre y rol reales de PostgreSQL, `search_path`/schema temporal y la cadena
exacta endpoint/transporte/conector.
CORE debe persistir previamente la acción y el grant del emisor congelado;
CONNECT no crea ninguna autorización. En ejecución, replay y reconciliación,
el grant suministrado debe coincidir campo por campo con el último grant
inmutable persistido. Un fallo anterior al envío aborta; solo una ambigüedad
posterior al intento HTTP produce UNKNOWN. La vigencia se decide en el instante
de despacho: un resultado ya enviado siempre se registra para no perder la
ambigüedad si el grant expira durante la espera de red.

C6 añade cero DDL, migraciones, seeds o rutas. Todo estado del smoke se revierte
en una transacción. El GET no cambia el estado del sandbox: la finalización
simulada ocurre fuera de banda antes de observarla.

## Consecuencias

- Se prueba una frontera HTTP real a loopback, no una integración vendor.
- Un proveedor real requiere nuevo código/versión, ADR, allowlist HTTPS,
  protocolo, credenciales y pruebas contra su sandbox oficial.
- La recuperación durable tras caída necesita dispatcher/outbox antes de
  producción; el rollback C6 solo es válido para smoke.
- La allowlist C5 no cambia: un tuple C6 persistente sigue siendo un fallo.
- No hay presentación legal, cobro, documento, correo, webhook público, worker,
  retry automático ni efecto sobre un expediente real.
