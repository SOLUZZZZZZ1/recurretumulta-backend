# RTM · Paquete sintético de staging

Este directorio contiene documentos **completamente ficticios** para validar la cadena documental de los satélites no pertenecientes a Tráfico:

```text
documento sintético
→ extractor documental
→ normalización
→ hechos propuestos
→ resolución de familia
→ primer rumbo
```

No contiene datos personales, expedientes, reservas, contratos, facturas ni actos administrativos reales. Los documentos no producen efectos jurídicos y no deben utilizarse como modelos de reclamación.

## Aislamiento del entorno

La creación de un servicio de staging separado se rige por:

```text
staging/ENVIRONMENT_MATRIX.md
rtm_core/environment_contract.py
scripts/rtm_environment_preflight.py
```

Antes de desplegar una instancia de staging debe ejecutarse:

```bash
python scripts/rtm_environment_preflight.py
```

El resultado debe contener `safe: true`. El informe no imprime secretos, URLs de conexión ni credenciales. Staging no puede utilizar la base, el bucket, Stripe, el frontend, el token OPS, el correo ni los canales de presentación de producción.

## Escenarios incluidos

| Servicio | Documento | Familia esperada |
|---|---|---|
| Morosidad | `fixtures/debt_invoice.txt` | `factura_impagada` |
| Administración | `fixtures/administration_enforcement.txt` | `apremio_recaudacion` |
| Viajes | `fixtures/travel_flight_cancelled.txt` | `vuelo_cancelado` |
| Reclamaciones | `fixtures/claims_telecommunications.txt` | `telecomunicaciones` |

Cada fichero debe conservar literalmente la cabecera:

```text
DOCUMENTO SINTÉTICO RTM — SOLO PRUEBAS DE STAGING
```

El validador rechaza cualquier documento que no incluya esa marca.

## Prueba determinista de CI

La integración continua utiliza un proveedor controlado y no llama a servicios externos. Comprueba contratos, fuentes, normalización, familia y primer rumbo.

```bash
python -m unittest tests.test_rtm_staging_validation -v
```

## Prueba live, manual y aislada

La prueba live no usa base de datos, B2 ni expedientes reales. Solo envía al proveedor los cuatro documentos sintéticos de este directorio. Requiere simultáneamente:

```text
RTM_ENV=staging
RTM_STAGING_CONFIRM=SYNTHETIC_ONLY
RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION=1
OPENAI_API_KEY=<clave exclusiva del entorno de staging>
```

Ejecución:

```bash
python scripts/rtm_staging_smoke.py
```

Para limitarla a uno o varios satélites:

```bash
python scripts/rtm_staging_smoke.py --services debt,travel
```

El informe final no contiene el texto de los documentos ni fragmentos de evidencia. Solo muestra huellas SHA-256, versiones, campos aceptados, conflictos, familia, especialista, madurez del primer rumbo y resultado de la validación.

## Límites vinculantes

La prueba sintética:

- no persiste extracciones ni hechos;
- no congela `ValidatedFacts`;
- no bloquea la familia;
- no crea `LegalPreview`;
- no llama a Generate;
- no presenta documentos;
- no modifica `cases` ni ninguna base de datos;
- no sustituye las pruebas posteriores en un servicio de staging separado.
