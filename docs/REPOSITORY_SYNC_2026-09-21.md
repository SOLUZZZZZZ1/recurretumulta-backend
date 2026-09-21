# Sincronización del entorno local — 21/09/2026

Rama de entrega: `rtm-ai-security-hardening-2026-09-03`, en backend y frontend.
Esta entrega incorpora el trabajo del PC y no modifica ni mezcla `main`.

Incluye autenticación individual local, custodia documental local, entrada y
autorización genérica de expedientes de prueba, precios públicos, clasificación
y preparación de estacionamiento, revisión e incorporación de hechos con
procedencia documental, borradores locales con PDF e historial, revisión de
plazos tras presentación simulada, fixtures sintéticos, esquema local y manuales.

La preparación local mantiene sus condiciones de acceso y no convierte datos
simulados en pagos, autorizaciones, firmas o presentaciones reales. Las funciones
de cada perfil conservan sus controles y las revisiones humanas pendientes.

## Comprobaciones de esta entrega

- Backend: 2.177 pruebas ejecutadas, sin fallos ni errores; 19 omisiones por
  plataforma o requisitos de integración en la ejecución unitaria de Windows.
- Seis integraciones con PostgreSQL 17 efímero ejecutadas por separado, todas
  correctas, incluida lectura de los PDF e historial de hechos y borradores.
- Importación completa de la aplicación y análisis de secretos del árbol e
  historial de Git correctos.
- Frontend compañero: 228 contratos Python, 296 pruebas JavaScript, compilación
  e inspección del paquete de producción y análisis de secretos correctos.
- La comprobación funcional de los datos adicionales ya verificó el navegador
  en escritorio y móvil y el arranque del entorno local.

El entorno activo del PC se conserva durante la sincronización. El lanzador
externo `D:\rtm\RTM_LOCAL_OPS_V2.py` se actualiza con los commits y las huellas
de esta entrega, para que el siguiente arranque reconozca el código sincronizado.
Los instaladores históricos y las copias de respaldo locales quedan ignorados
por Git. El esquema incluido no contiene filas de expedientes ni credenciales.

Los informes operativos de esta sincronización se conservan en
`C:\Users\soluz\rtm-work\2026-09-21-repo-sync\reports`.
