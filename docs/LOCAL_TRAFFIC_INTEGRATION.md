# Comprobación local de la cadena de multas con PostgreSQL

## Ejecución

Desde D:\rtm\RTM_BACKEND_STAGING, con PostgreSQL 17 instalado:

```powershell
& 'D:\rtm\.venvs\RTM_BACKEND_STAGING\Scripts\python.exe' -B scripts/rtm_local_integration_checks.py
```

`--reports` permite elegir la carpeta para los informes JSON y de texto.
El valor predeterminado es `rtm-integration-reports` dentro de la carpeta
temporal de Windows. El lanzador requiere ejecución normal de Windows; el
entorno restringido de Codex impide a PostgreSQL crear su proceso restringido.

## Aislamiento

El lanzador crea un clúster nuevo en una carpeta temporal, usa un puerto alto
libre, escucha solo en 127.0.0.1 y genera usuario, base y contraseña propios.
No hereda DATABASE_URL, PGSERVICE, PGPASSWORD, credenciales de proveedores ni
el perfil de autenticación de la aplicación local. Comprueba la identidad
real del servidor y la ruta de datos antes de habilitar las pruebas.

La base habitual `rtm_local` y su puerto 5432 no son destinos permitidos.
Las pruebas seleccionadas reinician su esquema: no deben ejecutarse a mano
apuntando a una base con datos. Utilizar este lanzador para crear el entorno
desechable y comprobar sus límites.

La salida de pg_ctl se captura en archivos para evitar la espera indefinida
que producen en Windows los canales de salida heredados por PostgreSQL.
La parada y la eliminación verifican que la carpeta pertenece a la prueba.
Si no puede confirmarse la parada, se conserva el clúster para diagnóstico.

## Cobertura

- Cadena de hechos, revisión documental explícita, versiones y sustitución.
- Conservación de campos pendientes aunque no tengan valor ni evidencia.
- Rechazo de la congelación de una lectura de IA sin atestación documental.
- Familia bloqueada, previa revisada/aprobada/congelada y recurso generado.
- PDF y DOCX reales, lectura de su contenido y verificación de huellas y tamaños.
- Generar no aprueba ni presenta: el expediente pasa a `final_ready` y necesita
  aprobación expresa para llegar a `ready_to_submit`.
- Reintentar Generate reutiliza el recurso, sin volver a almacenar documentos.
- Los hechos y pendientes sobreviven al commit y a una conexión nueva.
- Un expediente marcado como presentado conserva la fase de seguimiento.

Son pruebas de integración internas con identidades, documentos y revisión
simulados. La custodia externa se sustituye por memoria. No se ejecuta una
lectura de IA externa, un pago, una presentación administrativa ni su recibo.
La prueba de seguimiento fija un estado sintético; no demuestra el circuito
de presentación manual ni el registro de un justificante real.

## Siguiente trabajo de producto

`src/pages/OpsCaseDetailPro.jsx` todavía muestra desactivadas las acciones de
reanálisis, edición CORE y aprobación final. Esta verificación de la cadena
interna no habilita esos controles ni certifica un recorrido completo en el
navegador. Habrá que conectarlos a las rutas autenticadas, manteniendo
revisión, autoría, versiones y protección frente a cambios de expediente.

También permanece pendiente el presupuesto final de multa descrito en
`TRAFFIC_FINAL_PAYMENT_HANDOFF.md`.
