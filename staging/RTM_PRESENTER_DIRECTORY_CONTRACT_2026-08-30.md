# RTM Presenter · contrato del directorio DIR3/SIR

**Corte:** 30/08/2026

**Contrato runtime:** `rtm_presenter_directory_v1_0`

**Contrato snapshot:** `rtm_presenter_directory_snapshot_v1_0`

**Ámbito:** referencia administrativa de solo lectura; nunca autoriza una
presentación.

## 1. Decisión de producto

RTM separa cuatro conceptos que no son equivalentes:

| Dato | Responde a | No demuestra |
|---|---|---|
| DIR3 | Qué organismo o unidad administrativa es | Que exista un trámite electrónico adecuado |
| SIR | Que una unidad constaba integrada y asociada a una oficina registral en el snapshot; puede ser candidata a remisión por REG | Que la integración siga vigente o la unidad sea competente para este expediente |
| Perfil RTM verificado | Qué procedimiento, origen y campos puede utilizar Presenter | Que la actuación humana haya sido finalmente registrada |
| DEHú | Qué notificaciones o comunicaciones puede consultar el interesado | Que permita remitir un escrito a cualquier Administración |

Un resultado DIR3/SIR nunca entra en el selector de sedes. Para convertirse en
un destino utilizable debe existir un perfil RTM independiente, activo, con
procedimiento y origen exactos, doble verificación y fecha de comprobación.

El REG introduce una excepción arquitectónica útil, no una excepción de
control: RTM puede mantener un único perfil verificado de **REG — escrito
general** y seleccionar dentro de él la unidad destinataria. Para AGE alcanza a
sus órganos, organismos y entidades vinculadas o dependientes; para CCAA y EELL,
solo a las integradas en SIR. Antes de entregar bytes, RTM debe confirmar que la
unidad sigue apareciendo en el REG y que no existe un procedimiento electrónico
o formulario específico cuyo régimen desplace la vía general.

## 2. Snapshot incorporado

El compilador offline utiliza exclusivamente los cinco XLSX suministrados para
este corte:

- `Listado de oficinas SIR.xlsx`;
- `Listado Unidades EELL.xlsx`;
- `Catalogo de Localidades.xlsx`;
- `Catalogo de Provincias.xlsx`;
- `Catalogo-de-Comunidades-Autonomas.xlsx`.

La página oficial de descargas DIR3 mostraba el listado SIR con fecha de
modificación `30/06/2026`. Esa fecha identifica la publicación consultada; no
es una garantía de vigencia futura ni sustituye una comprobación antes de dar
de alta un procedimiento.

El snapshot generado tiene:

- identificador SHA-256
  `03eebff877fb55a00f1eab889a88af94af1469445492a04da7f9de3afa0a8aa7`;
- 35.841 organismos o unidades;
- 32.705 con al menos una oficina presente en el listado SIR;
- 11.811 localidades contrastadas con una unidad EELL vigente;
- 434 filas duplicadas por variante insular colapsadas sin perder la identidad
  DIR3;
- 58 códigos de localidad sin unidad EELL vigente, que no se incorporan como
  resultados autónomos.

Cada fichero fuente queda ligado por su SHA-256. El loader rechaza el snapshot
si cambia su contenido, aparecen claves o ficheros no previstos, se repite un
código, las estadísticas no cuadran o la fecha/fuente incumple el contrato.

## 3. Datos expuestos al operador

La búsqueda puede mostrar únicamente:

- denominación de organismo o unidad;
- código DIR3;
- nivel administrativo, comunidad, provincia y localidad cuando constan;
- código y nombre de la oficina SIR de referencia;
- fecha e identificador del snapshot.

No contiene correo, NIF/CIF, credenciales, URL de trámite, coordenadas de
almacenamiento ni datos de expedientes. Tampoco consulta Internet al buscar.

Toda respuesta lleva de forma obligatoria:

- `reference_only=true`;
- `usable_as_destination=false`;
- `procedure_profile_available=false`;
- `routing_decision_available=false`.

El frontend vuelve a validar esos límites y rechaza la respuesta completa si
el backend intenta ampliar el contrato.

## 4. Experiencia de búsqueda

El operador puede buscar por nombre, municipio, código DIR3 o alias limitado.
Los resultados administrativos aparecen en un bloque separado y no se mezclan
con los perfiles seleccionables.

Ejemplos verificados en el snapshot:

| Búsqueda | DIR3 | Oficina SIR de referencia |
|---|---|---|
| Manresa | `L01081136` | `O00011794` |
| DGT / Jefatura Central de Tráfico | `E00130201` | `O00009247` |
| Jefatura de Tráfico de Barcelona | `E03099901` | `O00010233` |
| Jefatura de Tráfico de Lleida | `E03101601` | `O00010248` |
| Jefatura de Tráfico de Badajoz | `E03099701` | `O00010231` |

Estos resultados identifican unidades y, cuando constaban en SIR, candidatos a
destino mediante el REG. No deciden si un recurso debe ir a la DGT central, a
una Jefatura, al CTDA, a un ayuntamiento o a otro órgano. Esa decisión debe
basarse en el organismo sancionador, la notificación, el número de expediente y
un procedimiento RTM revisado.

El portal oficial del REG precisa que admite solicitudes, escritos y
comunicaciones sin procedimiento electrónico o formulario normalizado. También
advierte que, si un régimen especial exige otra forma de presentación, el envío
por REG puede ser rechazado. Por ello RTM mostrará «candidato REG», nunca
«destino válido», hasta completar esas comprobaciones.

La observación manual del portal real en este corte confirma un flujo de cuatro
pasos:

1. datos del solicitante y elección interesado/representante;
2. datos de la solicitud y unidad destinataria;
3. documentación;
4. firma de la solicitud.

Cuando se actúa como representante, el propio portal advierte que debe hacerse
una solicitud separada por cada interesado representado. El perfil RTM deberá
bloquear la mezcla de varios interesados en una misma presentación. En el paso
de documentación, los archivos seguirán saliendo uno a uno desde el contenedor;
la firma y el envío final continúan siendo humanos. La captura usada para esta
observación contiene datos identificativos y no forma parte del snapshot, de
las fixtures ni del repositorio.

Si existe identidad DIR3/SIR pero no perfil verificado, la UI lo explica y
permite proponer un enlace para revisión independiente. La propuesta no abre
la URL, no crea un perfil y no permite adjuntar ni presentar.

Fuente funcional del REG:
<https://sede.administracion.gob.es/servicios-electronicos/registro-electronico-general-age>.

## 5. Actualización segura

El comando `scripts/rtm_presenter_directory_build.py`:

1. lee los XLSX localmente mediante un parser acotado;
2. valida cabeceras, códigos, estados y colisiones;
3. genera JSON canónico comprimido de forma determinista;
4. calcula hashes de fuentes y snapshot;
5. vuelve a cargar el resultado con el contrato runtime.

No usa red, base de datos, B2 ni perfiles de destino. Una actualización futura
debe comparar altas, bajas y cambios de oficina; pasar revisión humana; ejecutar
las pruebas; y publicarse como un nuevo snapshot. Nunca se actualiza en silencio
en tiempo de ejecución.

## 6. Evidencia de este corte

- 43 pruebas focales de directorio y servicio: OK.
- 115 pruebas `test_rtm_presenter*.py`: OK.
- 26 pruebas de scripts staging Presenter: OK.
- 15 contratos frontend y 22 pruebas del modelo/API: OK.
- build Vite de producción: OK, con advertencias no bloqueantes ya conocidas.
- verificación visual remota: no obtenida; el navegador cloud bloqueó el acceso
  a `localhost`. No se declara como E2E ni se interpreta como fallo funcional.

No se publicó, desplegó, migró ni activó ningún destino externo durante este
corte.
