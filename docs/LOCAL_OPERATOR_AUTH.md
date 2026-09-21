# Acceso individual de OPS en desarrollo local

Este perfil permite usar el espacio OPS con la autenticación individual existente
en un equipo local. No representa staging ni habilita administración de
operadores, lifecycle, Presenter, Connect, almacenamiento B2, pagos, proveedores
documentales, correo ni presentación externa.

La activación es explícita y está desactivada por defecto:

```text
RTM_ENV=development
RTM_ENABLE_LOCAL_OPERATOR_AUTH=1
RTM_ENABLE_OPERATOR_AUTH_V1=1
RTM_LOCAL_BIND_HOST=127.0.0.1
RTM_ALLOWED_HOSTS=127.0.0.1
ALLOWED_ORIGINS=http://127.0.0.1:5173
RTM_ALLOW_REAL_CUSTOMER_DATA=0
RTM_TRUST_PROXY_HEADERS=0
RTM_ENABLE_B2=0
RTM_ENABLE_STRIPE=0
RTM_ENABLE_FINAL_PAYMENTS=0
RTM_ENABLE_DOCUMENT_PROVIDER=0
RTM_ENABLE_OUTBOUND_EMAIL=0
RTM_ENABLE_EXTERNAL_SUBMISSION=0
```

`DATABASE_URL` debe utilizar exactamente el dialecto `postgresql+psycopg`, host
`127.0.0.1`, puerto `5432`, base `rtm_local`, usuario `rtm_local_app` y contraseña
local. No admite parámetros de conexión adicionales que puedan sustituir el
host o el esquema. También se rechazan `PGHOSTADDR`, `PGSERVICE` y `PGSERVICEFILE`
heredados del entorno. La contraseña debe introducirse de forma privada y nunca
imprimirse ni incluirse en instrucciones, commits o capturas.

`RTM_OPERATOR_ACCESS_HMAC_KEY` debe tener al menos 32 caracteres y ser exclusivo
del entorno local. El puente OPS necesita además un `OPERATOR_TOKEN` local: este
secreto permanece dentro del backend y nunca es una credencial del navegador.
La provisión de la cuenta y la comprobación de identidad de la conexión real
pertenecen a la herramienta offline local; el servidor no crea operadores.

`RTM_TRUSTED_PROXY_CIDRS` debe estar vacío. Se rechazan indicadores de plataforma
desplegada, identidades de staging/producción y orígenes frontend distintos del
origen local. El proceso debe arrancarse enlazado a `127.0.0.1`, sin confiar en
cabeceras de proxy (`proxy_headers=False`). Declarar `RTM_LOCAL_BIND_HOST` no
cambia por sí mismo la interfaz en la que Uvicorn escucha.

La validación se aplica al arrancar y antes de cada petición. El middleware
comprueba las direcciones ASGI del cliente y del servidor, rechaza orígenes
distintos o duplicados y exige el origen exacto para escrituras. Las lecturas
pueden omitir `Origin`, como hace un `fetch` del mismo origen, pero una petición
marcada `cross-site` o `same-site` se rechaza. La aplicación vuelve a fallar
cerrada si se modifica la configuración durante su ejecución.

La respuesta de `/ops/auth/status` identifica el perfil con
`auth_environment=development`, `auth_profile=local_development`,
`local_only=true`, `staging_only=false` y `shared_ops_login_accepted=false`.
Las respuestas del perfil staging mantienen su contrato anterior.

La contraseña Argon2id, el bloqueo de intentos, los hashes de sesión/dispositivo,
las expiraciones, revocación, roles, permisos, alcance de expediente y
reautenticación siguen utilizando los servicios existentes. La cookie
`__Host-rtm_presenter_device` conserva `Secure`, `HttpOnly`, `SameSite=Strict`
y `Path=/`. Debe verificarse en el navegador local que se almacena y devuelve:
una cookie ausente implica denegación y nunca autoriza un fallback compartido.

Las pruebas automatizadas verifican configuración, frontera HTTP y reutilización
del puente individual; no sustituyen la comprobación real de PostgreSQL ni la
posesión de la cookie en el navegador del equipo Windows.
