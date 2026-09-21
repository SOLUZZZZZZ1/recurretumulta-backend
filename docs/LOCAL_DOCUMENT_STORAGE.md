# Custodia documental local de pruebas

El adaptador local permite guardar y recuperar documentos sintéticos en el PC.
Requiere el perfil individual descrito en `LOCAL_OPERATOR_AUTH.md` y además:

```text
RTM_ENABLE_LOCAL_DOCUMENT_STORAGE=1
RTM_LOCAL_DOCUMENT_ROOT=D:\rtm\RTM_LOCAL_DATA\documents
```

La carpeta debe existir, ser absoluta, estar fuera del repositorio y pertenecer a
una unidad local fija en Windows. No se admiten rutas de red, symlinks, junctions
ni otros puntos de reanálisis. Conviene reservarla para RTM y mantener sus permisos
limitados al usuario local. El adaptador no cifra el contenido: solo se deben
usar documentos y datos de prueba. La validación de expediente `test_mode` y la
autorización del usuario pertenecen a las rutas, antes de llamar al almacenamiento.

`RTM_ENABLE_B2` y todas las capacidades externas siguen desactivadas. El nuevo
interruptor no modifica `require_capability("b2")`: `get_s3_client()` continúa
requiriendo B2 y no construye ningún cliente de red en el perfil local. Un valor
inválido del nuevo interruptor o una configuración local inconsistente provoca
rechazo; no selecciona B2 como alternativa silenciosa.

La fachada de `b2_storage.py` conserva las firmas de `upload_bytes`,
`upload_original`, `download_bytes`, `download_bytes_limited` y `delete_object`.
`require_http_document_storage()` exige el proveedor explícitamente seleccionado;
`get_document_bucket()` es el nombre común y `get_b2_bucket()` sigue disponible
por compatibilidad con los campos históricos `b2_bucket` y `b2_key` de SQL.

El bucket local es siempre `rtm-local-documents-v1`; las claves tienen exactamente
la forma `cases/<UUID canónico>/<carpeta segura>/<UUID hex>.<extensión>`. No se
aceptan coordenadas de otro proveedor, escapes de directorio ni aliases del
sistema de archivos. Cuando el proveedor local está desactivado, sus coordenadas
no pueden reutilizarse como coordenadas B2. La lectura puede además vincularse a
un `case_id` concreto.

Cada objeto es un archivo interno con cabecera de tamaño y SHA-256 seguida del
contenido original. No debe abrirse directamente como PDF: las descargas del
backend retiran la cabecera y verifican tamaño y hash antes de devolver los
bytes. El máximo absoluto es 64 MiB por objeto; las rutas mantienen sus límites
más pequeños y las lecturas limitadas los respetan. Las publicaciones son
atómicas, no reemplazan objetos existentes y los fallos de escritura limpian el
temporal. La compensación elimina únicamente la coordenada exacta confirmada y
puede repetirse si ese objeto ya no existe. No borra carpetas ni otros objetos.

Las rutas se resuelven con descriptores de directorio y `O_NOFOLLOW` en POSIX.
En Windows se mantienen abiertos los directorios ancestrales sin compartir
escritura ni borrado, salvo el ancla fija de la unidad, que se valida sin bloquear.
Se rechazan puntos de reanálisis; un conflicto con un handle de escritura o
borrado existente rechaza la operación, sin recurrir a una ruta menos protegida.
También se rechazan
archivos enlazados, no regulares o cuya identidad haya cambiado al abrirlos.
Las pruebas automatizadas del entorno de desarrollo verifican las operaciones
reales en su sistema de archivos; la ejecución nativa de Windows requiere
comprobarse en el PC de destino.

La custodia local no genera enlaces firmados, no publica una carpeta estática y
no devuelve rutas `file://`. `presign_get_url()` la rechaza explícitamente.
`/files/presign` conserva su contrato de B2 y permanece no disponible en este
perfil. Los endpoints que necesiten entregar bytes locales deben pasar por el
backend y conservar sus comprobaciones de identidad, expediente, tipo de
documento y pago. Esta configuración no habilita pagos, extracción o generación
con proveedores externos, correo, Presenter, Connect ni administración.

Las pruebas relevantes son `tests.test_rtm_local_document_storage`,
`tests.test_b2_storage_security` y `tests.test_rtm_external_capability_guards`.

La selección de modos de apertura sigue el contrato de
[CreateFileW de Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).
