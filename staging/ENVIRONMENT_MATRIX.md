# RTM · Matriz vinculante de entornos

Versión lógica: `rtm_environment_contract_v1_2`

Este documento define cómo debe separarse un servicio de **staging real** de la
instancia de producción. No contiene credenciales ni autoriza un despliegue. La
autoridad ejecutable es `rtm_core/environment_contract.py`; este documento
explica sus decisiones para operación, auditoría y continuidad.

## 1. Principio de aislamiento

Un entorno no queda aislado solo porque tenga otro nombre. Staging debe disponer
de recursos propios y verificables:

```text
rama propia
+ servicio propio
+ base PostgreSQL propia
+ frontend y CORS propios
+ token OPS propio
+ secreto propio para enlaces de expediente
+ secreto independiente para firmar la cadena de autoridad
+ bucket propio cuando B2 esté activo
+ claves test cuando Stripe esté activo
+ política synthetic_only cuando el proveedor documental esté activo
+ correo y presentaciones externas desactivados
```

No se copiarán valores de producción para «probar primero». La ausencia de una
capacidad se representa desactivándola expresamente; nunca reutilizando el
recurso equivalente de producción.

## 2. Perfiles reconocidos

| Perfil | Finalidad | Puede acreditarse como desplegable |
|---|---|---:|
| `development` | Desarrollo local | No |
| `test` | Pruebas automáticas y PostgreSQL temporal | No |
| `staging` | Validación aislada previa a producción | Sí, si no hay bloqueos |
| `production` | Servicio real | Sí, si no hay bloqueos |

`development` y `test` pueden ejecutar pruebas, pero el preflight nunca los
considera equivalentes a un servicio de staging o producción.

## 3. Identidad obligatoria de staging

Valores propuestos para la primera instancia separada:

```text
RTM_ENV=staging
RTM_ENVIRONMENT_CONFIRMATION=RTM_STAGING_ISOLATED
RTM_INSTANCE_ID=rtm-staging
RTM_DATA_NAMESPACE=rtm-staging
RTM_SIDE_EFFECT_POLICY=isolated
RTM_ALLOW_REAL_CUSTOMER_DATA=0
RTM_ALLOWED_HOSTS=<hostname exacto del backend verificado>
RTM_PUBLIC_CASE_ACCESS_SECRET=<secreto exclusivo de al menos 32 caracteres>
RTM_AUTHORITY_SIGNING_SECRET=<otro secreto exclusivo de al menos 32 caracteres>
RTM_EXPECTED_BRANCH=rtm-core-consolidation-2026-08-08
```

En Render, cuando estén disponibles, se contrastan además:

```text
RENDER_GIT_BRANCH
RENDER_GIT_COMMIT
RENDER_SERVICE_NAME
```

Reglas:

- la rama esperada de staging no puede ser `main`;
- la rama desplegada debe coincidir con `RTM_EXPECTED_BRANCH`;
- el nombre del servicio debe contener un marcador inequívoco de staging;
- `RTM_EXPECTED_COMMIT` puede fijar temporalmente el SHA exacto de una prueba;
- la identidad y el espacio de datos deben contener literalmente un marcador de staging.

## 4. Matriz de recursos

| Recurso | Staging | Producción | Regla de bloqueo |
|---|---|---|---|
| Backend | Servicio separado | Servicio real | El nombre de staging debe estar marcado |
| Rama | Rama de consolidación o `staging` | `main` | Staging no puede esperar `main` |
| PostgreSQL | Base dedicada con `staging` en el nombre | Base real | La base de staging sin marcador queda bloqueada |
| Frontend | Host propio con marcador de staging | `recurretumulta.eu` | Staging no puede usar el host exacto de producción |
| CORS | Orígenes explícitos de staging | Orígenes explícitos reales | `*` queda bloqueado |
| Host HTTP | `RTM_ALLOWED_HOSTS` exacto | `RTM_ALLOWED_HOSTS` exacto | Sin valor, esquema, puerto o comodín queda bloqueado |
| OPS | Token exclusivo de staging | Token exclusivo real | Mínimo de seguridad y sin valores de ejemplo |
| Acceso público | Secreto HMAC exclusivo | Secreto HMAC exclusivo real | Mínimo 32 caracteres |
| Autoridad firmada | Secreto HMAC distinto | Secreto HMAC distinto real | No puede coincidir con acceso público |
| B2 | Bucket dedicado de staging | Bucket real | No puede coincidir con producción |
| Stripe | Modo test, `sk_test_` | Modo live, `sk_live_` | Una clave live en staging queda bloqueada |
| Proveedor documental | `synthetic_only` | `customer_documents` | Staging no admite documentos de cliente |
| Correo | Desactivado | Activación expresa | Staging no puede enviar correo real |
| Presentación externa | Desactivada | Activación expresa | Staging no puede presentar actuaciones |

## 5. Capacidades opt-in

Todas las capacidades sensibles se declaran con valores booleanos explícitos:

```text
RTM_ENABLE_B2
RTM_ENABLE_STRIPE
RTM_ENABLE_FINAL_PAYMENTS
RTM_ENABLE_DOCUMENT_PROVIDER
RTM_ENABLE_OUTBOUND_EMAIL
RTM_ENABLE_EXTERNAL_SUBMISSION
```

Valores admitidos: `1/0`, `true/false`, `yes/no`, `on/off` y
equivalentes reconocidos. Un valor ambiguo bloquea el preflight.

La configuración inicial recomendada de staging es:

```text
RTM_ENABLE_B2=0
RTM_ENABLE_STRIPE=0
RTM_ENABLE_FINAL_PAYMENTS=0
RTM_ENABLE_DOCUMENT_PROVIDER=0
RTM_ENABLE_OUTBOUND_EMAIL=0
RTM_ENABLE_EXTERNAL_SUBMISSION=0
```

Después se activa una capacidad cada vez, se vuelve a ejecutar el preflight y se
realiza su prueba específica.

### 5.1 Correo RecurreTuMulta en Nominalia

El transporte de correo se configura una sola vez para todos los puntos de
entrada del backend. La contraseña canónica es `SMTP_PASSWORD`; no se admite el
nombre legacy `SMTP_PASS`.

```text
SMTP_HOST=authsmtp.securemail.pro
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USER=info@recurretumulta.eu
SMTP_PASSWORD=<secreto exclusivo en Render>
SMTP_FROM=RecurreTuMulta <info@recurretumulta.eu>
CONTACT_TO=info@recurretumulta.eu
```

Nominalia publica actualmente SMTP autenticado mediante SSL implícito en el
puerto 465. `SMTP_SECURITY=starttls` queda reservado para proveedores que
expongan STARTTLS, normalmente en el puerto 587.

En staging, `RTM_ENABLE_OUTBOUND_EMAIL=0` sigue siendo obligatorio. Las
variables SMTP pueden prepararse, pero ninguna ruta, job o notificación puede
abrir una conexión ni enviar un mensaje. La activación real se hará únicamente
en producción, mediante cambio explícito y prueba controlada.

## 6. PostgreSQL

Staging debe usar una base dedicada. Ejemplo de nombre válido:

```text
rtm_staging
```

El informe nunca imprime `DATABASE_URL`. Solo comprueba que:

- es una URL PostgreSQL completa;
- contiene un nombre de base;
- el nombre incluye el marcador de aislamiento de staging;
- no se reutiliza una identidad propia de producción.

La creación, migración y borrado de esa base se harán únicamente cuando el
servicio de staging esté aprobado. Las pruebas automáticas continúan utilizando
PostgreSQL 17 efímero en GitHub Actions.

## 7. Frontend y CORS

Ejemplo de host diferenciado:

```text
FRONTEND_URL=https://staging.recurretumulta.eu
ALLOWED_ORIGINS=https://staging.recurretumulta.eu
RTM_ALLOWED_HOSTS=<hostname exacto del backend verificado>
```

Reglas:

- no se admite `*` en staging ni en producción;
- cada origen debe ser una URL `http(s)` completa;
- CORS debe incluir exactamente el origen de `FRONTEND_URL`;
- staging no puede autorizar `recurretumulta.eu` ni
  `www.recurretumulta.eu` como hosts exactos;
- los hosts reales adicionales pueden declararse, para su exclusión, en
  `RTM_PRODUCTION_FRONTEND_HOSTS`.

`RTM_ALLOWED_HOSTS` no es una URL: contiene exclusivamente uno o varios
hostnames separados por comas, sin `https://`, puerto, ruta ni comodines. Debe
tomarse del dominio real verificado del servicio; este documento no lo adivina.
En `development` y `test` solo se aceptan `localhost`, `127.0.0.1`, `::1` y
`testserver`.

Las mutaciones administrativas con autenticación individual exigen además un
step-up persistido reciente. La ventana predeterminada es de 300 segundos y
solo puede configurarse entre 60 y 900 segundos:

```text
RTM_OPERATOR_REAUTH_MAX_AGE_SECONDS=300
```

Un timestamp enviado por el cliente no satisface este control.

## 8. B2

B2 permanece desactivado hasta crear credenciales y bucket exclusivos. Para
activarlo en staging se requiere:

```text
RTM_ENABLE_B2=1
RTM_B2_ISOLATION_MODE=dedicated_bucket
B2_ENDPOINT=https://...
B2_BUCKET=...staging...
B2_KEY_ID=<credencial exclusiva>
B2_APPLICATION_KEY=<secreto exclusivo>
```

Puede declararse el nombre del bucket real en
`RTM_PRODUCTION_B2_BUCKET`; si coincide con `B2_BUCKET`, el preflight bloquea.
No se admite un prefijo compartido dentro del bucket real como sustituto de un
bucket dedicado en esta primera fase.

## 9. Stripe

Stripe permanece desactivado al crear el servicio. Para activar únicamente los
pagos de prueba:

```text
RTM_ENABLE_STRIPE=1
RTM_STRIPE_MODE=test
RTM_ALLOW_REAL_PAYMENTS=0
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_REVIEW_BASIC=price_...
STRIPE_PRICE_ID_ADMIN=price_...
```

Una clave `sk_live_`, `RTM_STRIPE_MODE=live` o
`RTM_ALLOW_REAL_PAYMENTS=1` bloquea staging.

Para habilitar también el checkout final de retirada de vehículo en modo test:

```text
RTM_ENABLE_FINAL_PAYMENTS=1
STRIPE_PRICE_ID_ELIMINAR_COCHE=price_...
```

`RTM_ENABLE_FINAL_PAYMENTS=1` exige exactamente el Price ID consumido por el
router de retirada y no puede activarse si Stripe está desactivado.

## 10. Proveedor documental

La primera activación live del extractor seguirá limitada a fixtures ficticios:

```text
RTM_ENABLE_DOCUMENT_PROVIDER=1
RTM_DOCUMENT_INPUT_POLICY=synthetic_only
OPENAI_API_KEY=<clave exclusiva del entorno>
OPENAI_DOCUMENT_MODEL=<modelo aprobado>
```

`customer_documents` queda bloqueado en staging. La prueba sintética ya conserva
su propia confirmación adicional:

```text
RTM_STAGING_CONFIRM=SYNTHETIC_ONLY
RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION=1
```

Estas variables no sustituyen el contrato general de aislamiento; ambas capas
deben aprobarse.

## 11. Canales salientes

En staging son vinculantes:

```text
RTM_ENABLE_OUTBOUND_EMAIL=0
RTM_ENABLE_EXTERNAL_SUBMISSION=0
```

No habrá excepciones temporales. Una simulación de correo o presentación deberá
usar un adaptador local o un proveedor sandbox que no pueda alcanzar un
destinatario u organismo real.

En producción, la mera activación técnica tampoco basta. Se exige confirmación
adicional:

```text
RTM_ALLOW_REAL_NOTIFICATIONS=1
RTM_ALLOW_EXTERNAL_SUBMISSIONS=1
```

## 12. Ejecución del preflight

Desde la raíz del repositorio:

```bash
python scripts/rtm_environment_preflight.py
```

Resultado:

```text
0  entorno seguro
1  uno o más bloqueos
2  entorno seguro con advertencias, cuando se usa --strict-warnings
```

Modo compacto:

```bash
python scripts/rtm_environment_preflight.py --compact
```

El JSON resultante muestra códigos, estados y nombres de variables, pero nunca
incluye:

- contraseñas de base de datos;
- tokens OPS;
- claves B2;
- claves o webhooks Stripe;
- claves del proveedor documental;
- secretos de acceso público y autoridad firmada;
- URLs completas de recursos sensibles.

## 13. Orden operativo de creación

```text
1. Crear servicio backend separado sin desplegar main.
2. Crear PostgreSQL separado.
3. Reservar host frontend de staging.
4. Configurar identidad, CORS, token OPS y los dos secretos HMAC exclusivos.
5. Mantener todas las capacidades externas a 0.
6. Ejecutar preflight.
7. Importar aplicación y ejecutar pruebas de salud.
8. Activar B2 de staging, si se necesita, y repetir preflight.
9. Activar proveedor documental synthetic_only y ejecutar smoke sintético.
10. Activar Stripe test, si se necesita, y repetir preflight.
11. No introducir expedientes reales hasta una autorización posterior expresa.
```

## 14. Criterio de aprobación

Un servicio de staging solo se considerará creado cuando concurran todos estos
requisitos:

```text
preflight safe=true
+ rama y commit identificados
+ CI verde
+ 166 rutas importadas
+ PostgreSQL aislado
+ ningún secreto de producción
+ ningún dato real
+ ningún canal saliente real
+ smoke sintético aprobado
```

Hasta entonces, el código continúa únicamente en la rama segura y no se conecta
a Render ni a producción.
