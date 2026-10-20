# OPS workspace de lectura — contrato y dependencia acotada

Preparado desde backend público 73af66c67f5736bfc554006a34cbff415f1ccc35.
Frontend público correlacionado: 1700e89986917f3e001a2bbad14ad57d6deb2af1.

## Ruta y acceso
GET /ops/core/cases/{case_id}/workspace. Header X-Operator-Token con el mecanismo
OPS compartido YA existente en esa versión pública. No es el acceso individual
de las ramas de octubre. No se incorpora ese modelo histórico a dichas ramas.
El guard compartido con ops.py se separa en ops_auth.py, sin importar escritores
para leer. Comparación constante; ausencia de configuración: 503 cerrado.
Autorización antes de conectar; UUID validado y canonizado; parámetros enlazados.
El modelo de producción da al operador acceso global a los casos; no existe
en esta base un alcance individual por caso que este parche pueda certificar.
No se añade acceso anónimo, token de cliente, modo local o rol ficticio.

Respuestas de la nueva ruta (errores incluidos): private, no-store, no-cache.
El cliente solicita X-RTM-Workspace-Contract: rtm_ops_workspace_read_v1.
Si falta o no coincide, 406 WORKSPACE_CONTRACT_REQUIRED, sin SQL después de
autenticar. Permite desplegar primero backend sin ofrecer al frontend antiguo
un JSON parcial que pueda confundir con un workspace jurídico completo.
401 credencial ausente/incorrecta; 422 identificador inválido; 404 con código
WORKSPACE_CASE_NOT_FOUND solo para ausencia del caso comprobada. 503 para esquema
base ausente, falta de permisos SQL (WORKSPACE_STORAGE_PERMISSION_DENIED) o lectura
fallida; 500 WORKSPACE_INTEGRITY_INVALID para datos tipados/integridad inválidos.
No se serializan sentencias, parámetros, URLs de conexión ni mensajes SQL.

## Dependencias y lectura
app.py incluye workspace_router; workspace_router usa ops_auth, database.get_engine,
SQLAlchemy y workspace_read. No importa rtm_core histórico ni evaluadores,
migradores, escritores, IA, B2 o generadores para formar la proyección. app.py
mantiene sus imports y rutas preexistentes; no se ejecutan sus mutaciones.

PostgreSQL: transacción READ ONLY, schema public explícito. SQLite solo fixtures.
Introspección de tablas/columnas con SQLAlchemy; nombres SQL internos constantes.
Caso consultado primero por id enlazado. Campos permitidos: ver CASE_FIELDS.
Nunca SELECT *, to_jsonb(c), authorization_snapshot ni eventos payload.
Se lee interested_data para proyectar SOLO los campos conocidos del interesado;
las claves desconocidas y el address estructurado completo nunca se devuelven.
No se usa contact_name/contact_email como sustituto del interesado.
Contacto procede de cases.contact_name/contact_email. Conductor, titular y
representante: not_supported, sin inferir identidad ni afirmar inexistencia.

Documentos: id, kind, mime, size_bytes, sha256, created_at. Ni bucket/key,
URLs firmadas, bytes, descarga o verificación real del objeto.
Eventos: id/type/created_at, SIN payload. Seguimientos: id/kind/status/title/
description/due_at/resolved_at/created_at/updated_at; no cálculos de vencimiento.
Filtrados siempre por case_id. Máximo 1000 por colección, orden estable por id;
si hay más, status=partial y truncated=true; no se afirma recuento total.

## Ausencias y errores
workspace_version=rtm_ops_workspace_read_v1; read_only=true.
case.fields y cada campo de identidad contienen value, source y status:
available (valor leído), not_recorded (nulo/vacío en campo leído) y
schema_unavailable (columna inexistente; distinto de «No consta»).
sections.documents/timeline/followups contienen status, items, missing_columns
y, cuando se completa la consulta, truncated.
- tabla/columnas necesarias ausentes: schema_unavailable, items=null.
- lectura correcta sin filas: empty, items=[].
- lectura correcta con filas: available; campos opcionales ausentes enumerados.
- error de permiso/SQL: la petición FALLA; no se inventa una colección vacía.
- JSON/identificadores/tamaños/hashes inválidos: error de integridad.
legal.status=not_supported. No se han leído tablas de hechos/previas/recursos:
ese estado no dice que estén ausentes. No hay readiness, aprobación ni presupuesto
ficticios. Todas las capacidades nuevas de mutación/exportación/jurídicas son false.

## Interfaz
Se añade una rama explícita de presentación para este contrato. Se conservan las
rutas/pantallas restantes, sin cambiar vercel.json, proxy o dependencias.
GET workspace antes de consultas accesorias del contrato antiguo. El contrato
de lectura ya incluye las colecciones y NO consulta documentos/events crudos.
No fallback ante 404; 401/403 retiran contexto y token actual; respuestas tardías
se descartan mediante abort/secuencia y contraste de caso/token. Remontaje por
caso/sesión; cierre externo de sesión sincronizado por storage/focus y sondeo de
500 ms mientras la vista está montada. No se restaura cache del navegador.
La vista limitada no presenta controles de escritura, exportación o evaluación.
Las otras rutas operativas históricas no se certifican seguras por este cambio.

## Verificación aislada
Desde un entorno instalado con requirements.txt:
PYTHONPATH del worktree, variables operativas eliminadas; OPERATOR_TOKEN y
OPENAI_API_KEY contienen únicamente marcadores públicos de fixture. Pruebas:
python -B -m unittest discover -s tests -p test_workspace_read.py -v
Las pruebas importan app.py completo y ejercen HTTP ASGI, con SQLite por petición.
La red externa está bloqueada en el arnés. El constructor SDK histórico existe
en la importación de app; no se invoca un proveedor.
La suite comprueba registro de ruta, autorización antes de SQL, no DML,
proyección y separación, columnas/tablas opcionales, integridad, errores y
rutas de lectura anteriores. No equivale a PostgreSQL/producción.
La prueba PostgreSQL desechable y el esquema operativo siguen pendientes.
