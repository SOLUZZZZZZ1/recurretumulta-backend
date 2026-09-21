# Continuidad RTM: PC local de Ramon

Estado preparado el 18 de septiembre de 2026. Este documento acompana al
lanzador `D:\rtm\RTM_LOCAL_OPS_V2.py` y permite continuar desde Codex local.

Actualización del 21/09/2026: el trabajo local acumulado se incorpora a la rama
`rtm-ai-security-hardening-2026-09-03` de ambos repositorios. Los HEAD indicados
abajo son las bases históricas del 18/09, no las versiones actuales. La entrega
y sus comprobaciones se describen en `REPOSITORY_SYNC_2026-09-21.md`.

## Ubicacion y estado de Git

- Backend: `D:\rtm\RTM_BACKEND_STAGING`.
- Frontend: `D:\rtm\RTM_FRONTEND_STAGING`.
- Ambos: rama `rtm-ai-security-hardening-2026-09-03`.
- HEAD base backend: `7a2d7d0e57fc9a7a20eb0959aad68230d8a54021`.
- HEAD base frontend: `05196b96e61939f685739bfde187d10c60cc966d`.
- Hay cambios locales deliberados, sin commit ni push. Revisar el diff antes
  de sincronizar; no descartar ni sustituir esos archivos con un reset/pull.
- El trabajo autorizado en esta sesion es preparar y probar el entorno local.
  No se ha desplegado ni cambiado Render o la web publica.

## Entorno confirmado

Windows 11, Python 3.12, Node 24, Git y PostgreSQL 17 instalados.
Venv: `D:\rtm\.venvs\RTM_BACKEND_STAGING`.
Base: `rtm_local`; usuario propietario no superusuario: `rtm_local_app`;
servidor: `127.0.0.1:5432`. Se importo solo esquema (63 tablas), sin clientes.
Ramon conoce la contrasena de PostgreSQL; no esta incluida en estos archivos.

Frontend: `http://127.0.0.1:5173`; backend: `http://127.0.0.1:8000`.
El proxy de Vite elimina `/api` una sola vez. No usar `localhost` como sustituto
de `127.0.0.1`: el perfil exige el origen exacto.

## Arranque

Detener el lanzador anterior con Ctrl+C y ejecutar en CMD:

```bat
py -3.12 D:\rtm\RTM_LOCAL_OPS_V2.py
```

El lanzador valida HEAD y huellas de archivos, conserva originales en un
respaldo, solicita la contrasena local, comprueba la identidad real de la base,
conserva o crea el operador sintetico y arranca ambos servidores.
La contrasena de OPS existente se conserva. Usuario:
`rtm-local-supervisor@example.com`.
Las claves tecnicas se guardan con DPAPI del usuario Windows actual en
`%LOCALAPPDATA%\RTM_Local\ops_keys.dpapi`; las contrasenas no se guardan alli.

Los documentos se custodian en `D:\rtm\RTM_LOCAL_DATA\documents`.
No editar sus archivos internos: contienen tamano y hash de integridad.
Los archivos ficticios para subir desde el navegador estan en
`D:\rtm\PRUEBAS_RTM`.

## Prueba que estamos haciendo

Entrada: `/iniciar-expediente/claims/consumer?family=bancos`.

| Campo | Dato ficticio |
| --- | --- |
| Nombre | PRUEBA LOCAL BANCOS |
| Documento | RTMTEST001 |
| Email | prueba.bancos@example.com |
| Telefono | 000000000 |
| Calle y numero | Calle de Prueba, 1 |
| Codigo postal, poblacion, provincia | 08240, Manresa, Barcelona |
| Relato | PRUEBA LOCAL. El BANCO DE PRUEBA RTM ha cobrado una comision ficticia de 30 EUR. Solicito su revision y devolucion. No corresponde a una operacion real. |

Adjuntar los PDF `01_identidad_prueba_frontal.pdf` y
`02_identidad_prueba_reverso.pdf`. Son hojas de prueba, no imagenes de un DNI.
El servidor marca el caso `test_mode=TRUE`; no confia en un flag del navegador.
Al crear, comprobar que se descarga un PDF RTM con marca de prueba y que el
expediente aparece en OPS al refrescar (familia Bancos).

Para probar recepcion del candidato, utilizar
`04_candidato_autorizacion_prueba.pdf`. No tiene firma real y solo puede quedar
pendiente de revision. Para documentacion adicional puede usarse
`03_comision_bancaria_prueba.pdf`.

## Limites del flujo preparado

- Autenticacion individual, dispositivo, sesiones y permisos siguen activos.
- El nuevo flujo RTM generico es local y cubre Consumo en Bancos, Energia,
  Telecomunicaciones y Seguros. No equivale a poder DGT ni a firma verificada.
- Subir un candidato no pone `authorized=TRUE`, no verifica una firma y no
  concede permisos de presentacion o pago.
- B2, IA documental externa, pagos, correo, Presenter y firma externa siguen
  desactivados. No activar staging para simular desarrollo.
- Las descargas publicas antiguas por `/files/presign` siguen siendo de B2;
  no estan adaptadas a local. El PDF RTM de prueba se descarga por su endpoint
  autenticado, sin enlaces externos ni rutas del disco en la respuesta.
- La base real Windows y el login OPS V1 ya fueron confirmados por Ramon.
  La custodia local V2 necesita comprobarse en Windows, ademas de las pruebas
  de codigo y SQL complementarias hechas en el entorno de preparacion.

## Continuacion desde Codex local

### Tarifas publicas revisadas

Ramon detecto que `/precios` solo mostraba avisos de cotizacion. Se ha preparado
`GET /public/review-prices`, una proyeccion informativa del catalogo existente,
y la pagina consulta esa ruta mediante `/api`. No crea expedientes ni pagos.
El catalogo actual fija revisiones de trafico y deuda en 10 EUR, administracion
en 25 EUR y consumo en 10 EUR. No se duplican importes en el frontend; si la
consulta falla se muestra el fallo y una opcion para reintentar.

Ramon ha confirmado expresamente que el recurso administrativo de multa
cuesta 39 EUR en total y que, tras pagar los primeros 10 EUR, el saldo es
29 EUR (corrigio la cifra inicial de 28 EUR). Se incorpora esa tarifa al
catalogo y a la pagina publica. Es distinta del servicio de eliminacion de
vehiculo aunque ambos tengan actualmente el mismo total.

El segundo cobro debe vincularse al mismo expediente y acreditar la revision
ya pagada antes de descontarla. Los 29 EUR mostrados como ejemplo en la pagina
no autorizan un cobro. Queda pendiente integrar el cobro final con presupuesto
persistido, evidencia de pago y Stripe; el checkout final antiguo sigue
retirado. No se ha creado ningun producto, precio, sesion o enlace en Stripe.
En el entorno de preparacion no hay conexion a la cuenta Stripe de Ramon.

### Siguiente prueba

Empezar comprobando ambos `git status -sb` y HEAD, y leyendo los cambios
existentes. Continuar la prueba anterior con los servidores del PC. No pedir
contraseñas por el chat ni imprimir claves, tokens o archivos DPAPI.
Si un paso falla, conservar el case_id y corregir el fallo; no repetir altas
a ciegas ni borrar registros o archivos para ocultarlo.
