# Preparación de seguridad para RTM staging — 7 de septiembre de 2026

Este cambio parte de `5bc4b75935c66694bfe7a3229240afc9f7e9162b`. Prepara el código; no modifica Render ni activa capacidades externas.

## Destinos verificados

| Elemento | Identidad |
|---|---|
| Servicio Render staging | `srv-d9v1t2qjnfac73a14kug`, `recurretumulta-backend-1` |
| Backend HTTPS | `https://recurretumulta-backend-1.onrender.com` |
| Backend Live al revisar | `a203da35c03b5cd055ccc5243f1262284fe4eee5` |
| Despliegue Live / referencia de retorno | `dep-dack28eq1p3s738eq1pg` |
| Rama de entrega prevista | `rtm-ai-security-hardening-2026-09-03` |
| Proyecto Vercel | `prj_mOgYzD9oofcJMmla5hFmVVpmuKxV` |
| Origen de la Preview de esa rama | `https://recurretumulta-frontendweb3-8-26-git-r-cbbb3a-soluzzzs-projects.vercel.app` |

La API de Vercel devuelve ese alias para los deployments de `b5a7690` y `06836c8`. Ambos fallaron en el guard de build. Esto acredita la asociación proyecto/rama/alias, no que la aplicación nueva esté funcionando. La última Preview READY observada sigue siendo `3def823` en la rama individual anterior.

## Código preparado

El origen exacto de esta Preview se incorpora a la validación de entorno y de enlaces backend, clasificado como staging. No se confía en otras ramas, otros proyectos ni en `*.vercel.app`. Los controles de producción deben rechazarlo.

Los controles de autenticación, dispositivo, autoridad, parser y efectos externos permanecen activos. El cambio no añade DDL ni justifica ejecutar una migración general.

## Propuesta de configuración para una entrega posterior

Estos son valores no secretos propuestos, **no aplicados**. La lectura autorizada del panel no autoriza guardarlos ni desplegar.

| Ajuste | Estado observado | Propuesta |
|---|---|---|
| Rama del servicio | `rtm-ops-individual-auth-2026-09-02` | Rama de seguridad indicada arriba |
| `RTM_EXPECTED_BRANCH` | Rama individual | Cambiar junto con la rama del servicio |
| `RTM_ALLOWED_HOSTS` | Ausente | `recurretumulta-backend-1.onrender.com` |
| `FRONTEND_URL` | Origen del backend | Origen HTTPS exacto de la Preview indicado arriba |
| `ALLOWED_ORIGINS` | Origen del backend | Mismo origen exacto de la Preview |
| `RTM_TRUST_PROXY_HEADERS` | `1`, sin `RTM_TRUSTED_PROXY_CIDRS` | Propuesta conservadora `0` hasta verificar el ingreso; no inventar CIDRs |
| Auto-Deploy / PR Previews | Off / Off | Conservar |

Desactivar la interpretación de cabeceras en RTM puede agrupar clientes bajo la IP del proxy y afectar la granularidad de cuotas y auditoría. Antes de desplegar debe revisarse también cómo Uvicorn establece `scope.client`; no basta con desactivar un parser de cabeceras si otra capa ya modificó el peer. Los rangos de salida de Render no identifican sus proxies de entrada.

No se propone cambiar los secretos ni habilitar correo, Stripe, pagos finales, presentaciones externas o datos reales. B2 y proveedor documental ya están activados bajo política sintética; esta entrega no los invoca. La configuración observada declara aislamiento, pero la conexión secreta de base de datos y los permisos efectivos del bucket no se han inspeccionado.

## Condiciones antes de aprobar un despliegue

1. Publicar únicamente los commits revisados en la rama de seguridad. Render permanece en despliegue manual y la nueva confirmación de entrega Vercel permanece sin configurar.
2. Verificar el destino aislado de base de datos/bucket sin exponer credenciales, la protección de la Preview y el comportamiento del proxy/peer.
3. Aprobar de forma concreta el commit a desplegar y los cambios de configuración. Mantener la referencia de retorno del backend y la Preview anterior.
4. Ejecutar los preflights sobre **el código candidato** en el entorno correcto. Un preflight del backend antiguo no acredita el candidato.
5. Comprobar el arranque real, incluido el probe del parser, y `/health/live` frente a `/health/ready`. Las pruebas que crean datos sintéticos o consumen proveedores requieren alcance autorizado.

Lecturas previstas, todavía no ejecutadas en Render para este parche:

```text
python scripts/rtm_environment_preflight.py --compact
python scripts/rtm_staging_document_schema.py --compact
python scripts/rtm_staging_core_schema.py --compact
python scripts/rtm_staging_operator_auth_schema.py --compact
python scripts/rtm_operator_auth_routes_preflight.py --require-enabled --compact
python scripts/rtm_operator_admin_preflight.py --require-enabled --compact
python scripts/rtm_operator_lifecycle_preflight.py --require-enabled --compact
```

El comando start observado ya ejecuta el preflight antes de Uvicorn; `/health` sigue siendo válido como ruta de readiness. No ejecutar smokes de escritura como si fueran consultas de configuración.

## Límite A1S

F1/F2 conserva su hostname histórico y sus evidencias previas. Esta entrega habilita la preparación del despliegue general, no renueva esas evidencias ni abre A1S. Un cambio de hostname F1/F2 requiere su propia actualización de integridad y comprobaciones; no deben recalcularse hashes históricos para hacerlos pasar por pruebas nuevas.
