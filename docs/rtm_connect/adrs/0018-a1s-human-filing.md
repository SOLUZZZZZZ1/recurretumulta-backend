# ADR 0018: A1S, workflow humano sintético de presentación

- Estado: aceptada para staging sintético
- Fecha: 2026-08-25
- Decisión de producción: `NO-GO`
- Base congelada: `b0bc7ddfad9278e601dce8dd69083472662874b5`
- ZIP SHA-256: `4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e`

## Contexto

G1 confirmó que los candidatos legacy no constituyen un proveedor admisible y
que la Administración no ofrece hoy una puerta técnica verificada. El camino
realista exige que una persona use la puerta de la Administración. El cuello de
botella humano no justifica introducir un transport inseguro ni afirmar que el
backend puede presentar por sí mismo.

Necesitamos, sin embargo, probar el contrato backend que consumirá el frontend:
asignación, revisión, doble aprobación, entrega al ejecutor, captura de evidencia,
reconciliación y cierre. C7 conserva su alcance congelado; no se amplía ni se
reactiva. A1S es un overlay nuevo y explícitamente sintético.

## Decisión

Se acepta A1S como workflow humano interno, feature-gated y exclusivo de
staging. Solo admite fixtures marcadas como sintéticas. No tiene transport de
proveedor o Administración, red hacia terceros, B2, B2B, secretos de sede,
datos reales, worker, reintentos ni efectos externos.

Se adoptan estas barreras:

1. identidad individual de operador derivada de sesión bearer validada;
2. membership activa de tenant y ownership explícito de expediente;
3. permisos de menor privilegio por transición;
4. evidencia de representación tipada y ligada a hash;
5. paquete canónico e inmutable antes de aprobar;
6. ejecutor/solicitante, releaser y verifier forman un mínimo de tres
   identidades: releaser y verifier son distintos entre sí y del ejecutor;
7. el verifier preaprueba el hash antes de liberar y es quien valida después la
   E4 sintética;
8. idempotencia acotada por tenant/expediente/operación/hash;
9. optimistic locking y ledger de eventos/aprobaciones append-only;
10. `outcome_unknown` obliga a reconciliar y prohíbe reenvío ciego;
11. feature flag cerrada por defecto y guards locales de efectos en `false`;
12. rutas internas bajo `/ops/connect/human-filings`, fuera de OpenAPI.

El DDL de la fase se limita a tablas A1S nuevas. La migración es aditiva, exige
`STAGING_CONNECT_A1S_SCHEMA_ONLY`, no hace seed y conserva inmutable la entrada
del ledger. Los artefactos son metadata/hash de fixtures; no se suben a B2.
Durante el workflow, el servicio coordina también las tablas CORE sintéticas
existentes de connectors/actions/attempts/evidence en una única transacción.
No existe transporte ni efecto externo.

## Consecuencias

El frontend puede adaptarse a un contrato estable de tareas humanas sin esperar
una API de la Administración. El sistema puede demostrar separación de
funciones, ownership e idempotencia con datos sintéticos. A cambio, A1S no reduce
por sí mismo el trabajo humano ni prueba una presentación legal.

Un resultado A1S, incluso `completed`, significa únicamente que la simulación
sintética terminó. Un informe sintético no es un recibo real y una E4 sintética
no satisface E4 de proveedor. Las decisiones `NO-GO` de G0/G1 siguen siendo
autoridad para cualquier ejecución live.

Como compromiso explícito de este sobre, `prepare` encola la acción y abre el
attempt C1 antes de asignar, revisar y aprobar la tarea humana. Por ello CORE
observa `EXECUTING` durante esas fases preparatorias aunque todavía no haya
simulación humana. Es aceptable solo en staging sintético; una fase real deberá
alinear el inicio del attempt con release/execution. Asimismo, la aplicación
recalcula hashes canónicos, pero PostgreSQL no reproduce por sí solo la
canonicalización Python.

## Alternativas rechazadas

- Reactivar C7: violaría su contrato congelado y mezclaría fases.
- Usar `dgt_client`, XML de desarrollo o Registro General genérico: los tres
  candidatos fueron rechazados en G1 y carecen de identidad/protocolo/evidencia.
- Guardar documentos en B2 ahora: faltan namespace por tenant, validación,
  cifrado/custodia, malware scan, retención y borrado acreditados para este flujo.
- Usar PIN o token compartido: impide atribución individual y separación de
  funciones.
- Permitir datos reales «solo para probar»: rompería la frontera staging y
  convertiría una prueba sin proveedor ni controles completos en tratamiento
  real no autorizado.

## Condiciones para una fase real posterior

Una ADR y gate separados deberán probar identidad fuerte/MFA, tenant isolation,
almacenamiento seguro, legal basis, representación, proveedor/Administración y
origen verificados, secretos y egress, idempotencia/consulta remotas, E4
auténtica, reconciliación/rollback, observabilidad, respuesta a incidentes,
retención/DSAR y aprobaciones vigentes ligadas a hash. Hasta entonces:

```text
synthetic_only=true
real_data_used=false
provider_network_used=false
administration_network_used=false
provider_contacted=false
administration_contacted=false
b2_used=false
b2b_enabled=false
external_effects_executed=false
production_authorized=false
live_verdict=no_go
```
