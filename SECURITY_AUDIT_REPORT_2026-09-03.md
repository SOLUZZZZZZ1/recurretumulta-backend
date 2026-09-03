# RTM — Informe público de auditoría y endurecimiento defensivo

**Fecha de corte:** 3 de septiembre de 2026
**Ámbito:** backend RTM, frontend RTM, automatización CI/CD e historial Git alcanzable
**Naturaleza:** revisión defensiva de código y configuración; no se realizaron acciones ofensivas contra terceros
**Estado de publicación:** borrador público, sin datos personales, valores secretos ni instrucciones de explotación

## 1. Dictamen ejecutivo

La revisión ha encontrado y corregido múltiples clases de riesgo de impacto alto o crítico: inyección de instrucciones en flujos de IA, documentos hostiles, autorización horizontal entre expedientes, sesiones compartidas, manipulación y repetición de pagos, carreras de estado, falsos positivos de presentación externa, exposición de datos en el navegador y debilidades de CI/CD.

Las defensas están presentes en los árboles de trabajo locales de las ramas de seguridad y cuentan con suites locales integrales positivas. No obstante, este informe **no autoriza producción**. En el momento de corte, los cambios aún no habían sido confirmados, enviados, fusionados ni desplegados, y quedaban controles externos, CI remoto e integraciones reales por verificar.

La conclusión correcta es, por tanto:

> **Mejora defensiva material, pero NO-GO para producción hasta completar la lista de bloqueos predeploy.**

No existe una forma responsable de certificar “cero vulnerabilidades”. Las conclusiones se limitan al código, historial y pruebas accesibles durante esta revisión.

### 1.1 Estado verificable del código

| Componente | Rama local revisada | Punto de partida | Estado al corte |
|---|---|---|---|
| Backend | `rtm-ai-security-hardening-2026-09-03` | `a203da35c03b5cd055ccc5243f1262284fe4eee5` | Cambios locales sin commit ni push |
| Frontend | `rtm-ai-security-hardening-2026-09-03` | `3def8233658a00ee887adc23cf96c2e821ced5a2` | Cambios locales sin commit ni push |

### 1.2 Resultados de cierre

**Backend final:** 2.029/2.029 pruebas ejecutadas correctamente; 8 integraciones omitidas de forma declarativa por requerir PostgreSQL o proveedores externos. Suite focal final: 231/231. Restaurante/PIN: 18/18. Compilación Python, `git diff --check`, validación YAML, `pip check` y escaneo de secretos del árbol e historial: correctos.

**Auditoría CVE Python:** no verificable en este entorno. `pip-audit --strict` no pudo consultar su servicio de vulnerabilidades porque la salida de red fue bloqueada; la consulta secundaria al registro oficial también fue denegada. No se interpreta como ausencia de vulnerabilidades. Las 20 dependencias directas están fijadas exactamente y `pip check` es correcto, pero el audit remoto completo sigue siendo un bloqueo de CI.

## 2. Cómo interpretar la evidencia

Este informe utiliza cuatro niveles de certeza:

- **Probado:** observado en el código y ejercitado mediante pruebas o escáneres ejecutados.
- **Implementado:** corrección presente en el árbol local, pendiente de la última validación integral o remota.
- **Inferido:** conclusión razonable derivada del diseño, sin comprobación operativa externa.
- **No verificable externamente:** requiere acceso a producción, proveedores, logs o configuración fuera del repositorio.

La severidad indicada en los hallazgos corresponde al impacto que podía tener la condición **antes** de aplicar la corrección.

## 3. Alcance y metodología

Se aplicó una revisión white-box y adversarial, pensando en los puntos de entrada que intentaría aprovechar un atacante:

- autenticación, cookies, capacidades y recuperación de sesión;
- rutas con `case_id`, tenant, asignaciones y permisos;
- cargas PDF, DOCX e imagen, parsers y extracción de texto;
- llamadas de IA, OCR, prompts, herramientas y límites de consumo;
- checkout, webhooks, reembolsos, disputas, repeticiones y carreras;
- conectores externos, correo, almacenamiento y presentación DGT/Registro;
- URLs, redirecciones, CORS, Host, proxy headers y framing HTTP;
- almacenamiento del navegador, BFCache, navegación y exposición de PII;
- dependencias, workflows, acciones, secretos e historial Git.

La validación combinó revisión manual multiagente, pruebas unitarias y de contrato, pruebas de concurrencia simulada, compilación, auditoría de dependencias, escaneo de secretos actual e histórico y verificación de build.

## 4. Hallazgos corregidos

### 4.1 Frontera de IA y resistencia a prompt injection — Alta

**Riesgo previo.** Un documento, OCR, nombre de archivo o formulario podía contener instrucciones dirigidas al modelo, intentar cambiar su tarea, solicitar secretos o inducir acciones no autorizadas.

**Corrección.** Se centralizó una frontera pasiva en `rtm_core/ai_security.py`:

- todo contenido documental y del usuario se trata como dato no confiable;
- los datos se encapsulan de forma inequívoca y con longitud limitada;
- las instrucciones de seguridad se insertan con prioridad superior;
- las llamadas documentales no admiten herramientas;
- la persistencia del proveedor se desactiva cuando el contrato lo permite;
- se limitan salida y número de llamadas por operación;
- los subámbitos y copias de contexto comparten un presupuesto duro;
- las respuestas de IA siguen siendo datos sin autoridad de negocio;
- cualquier efecto legal, de pago o presentación exige comprobaciones deterministas y revisión humana.

El detector de frases sospechosas es una señal auxiliar; la seguridad no depende de que reconozca todas las formulaciones posibles.

**Evidencia.** Suites `test_rtm_ai_security`, `test_analyze_security_boundary`, `test_rtm_generation_gateway`, `test_openai_endpoint_security` y pruebas específicas de OCR. **Estado: probado.**

### 4.2 Archivos hostiles y aislamiento de parsers — Alta

**Riesgo previo.** Archivos falsamente etiquetados, documentos activos, traversal, relaciones externas, zip bombs, PDFs complejos o imágenes descomprimidas gigantes podían provocar SSRF, agotamiento o explotación de librerías.

**Corrección.** Se añadió validación por contenido y trabajo acotado:

- lectura con límite antes de materializar el cuerpo completo;
- coincidencia estricta entre magic bytes, extensión y MIME;
- rechazo de acciones y objetos activos en PDF;
- rechazo de macros, relaciones externas, objetos incrustados, XML activo y traversal en DOCX;
- límites de entradas, ratio de compresión, bytes expandidos, páginas, objetos, dimensiones, píxeles y frames;
- extracción textual acotada;
- proceso de parser separado con timeout y límites verificables de CPU, memoria, ficheros y procesos;
- errores opacos y cierre preventivo ante timeout, crash o fallo de aislamiento.

**Evidencia.** `rtm_core/upload_security.py`, `rtm_core/parser_isolation.py` y suites de upload, intake, parser y text loader. **Estado: probado.**

### 4.3 Autenticación individual, dispositivo y sesiones — Crítica

**Riesgo previo.** Credenciales compartidas, enumeración de cuentas, bearer sin segunda prueba de posesión y sesiones persistentes aumentaban el impacto de robo de token y dificultaban la atribución.

**Corrección.** Se implantó autenticación individual en la superficie habilitada:

- bearer ligado a un dispositivo registrado;
- logout exige simultáneamente sesión y posesión del dispositivo;
- cookie de dispositivo host-only con prefijo `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` y sin `Domain`;
- purga de la cookie legacy al iniciar o cerrar sesión;
- cuentas inexistentes y bloqueadas recorren coste Argon2 comparable y devuelven el mismo 401 genérico;
- sesiones, dispositivos y credenciales pueden revocarse sin borrado destructivo;
- eventos de autenticación no almacenan valores secretos.

El frontend conserva el bearer OPS solo en memoria e invalida la sesión en expiración, `pagehide` y restauración desde BFCache.

**Evidencia.** Pruebas de auth, enumeración, posesión de dispositivo, cookies, partner y lifecycle. **Estado: probado.**

### 4.4 Step-up para operaciones privilegiadas — Crítica

**Riesgo previo.** Una sesión válida pero antigua podía ejecutar mutaciones de administración o ciclo de vida sin una comprobación reciente de contraseña.

**Corrección.** Se añadió reautenticación persistida en servidor y ligada a la misma sesión. La ventana predeterminada es de 300 segundos y solo puede configurarse entre 60 y 900 segundos. La ausencia de evento, un evento caducado, una fecha futura o una afirmación aportada únicamente por el cliente no habilitan la operación.

El requisito cubre las mutaciones de administración de operadores, lifecycle, alta individual de restaurantes y alta partner. **Evidencia:** cierre auth de 388 pruebas y 432 subtests, compilación de 34 archivos y `git diff --check` correctos. **Estado: cerrado y probado focalmente.**

### 4.5 Alcance OPS e IDOR entre expedientes — Crítica

**Riesgo previo.** Una ruta que recibiera `case_id` sin repetir el scope dentro de la misma transacción podía permitir acceso o mutación cruzada después de un cambio concurrente de asignación, tenant o membresía.

**Corrección.** Se creó una matriz AST de todas las rutas CORE montadas con expediente. Los 40 handlers CORE y las 17 rutas de `ops_operator_router.py` quedaron revisados; 16 rutas del segundo grupo comprueban scope dentro de la transacción y `submit` termina en 410 sin acceder a DB. Los handlers con expediente deben:

- recibir el contexto de petición;
- abrir su transacción explícita;
- cargar o exigir el scope dentro de esa misma transacción;
- comprobar tenant, membresía y asignación vigentes;
- responder con 404 opaco cuando el expediente queda fuera de alcance.

La matriz incluye lecturas, mutaciones, recurso final y el claim de reanálisis. Los regeneradores revalidan el scope en sus dos transacciones. El reanálisis rechaza un claim ya activo y solo puede publicar desde el estado intermedio esperado.

**Evidencia.** Matriz negativa de scope incluida en la suite focal final 231/231 y en la suite backend completa 2.029/2.029. **Estado: cerrado y probado localmente.**

### 4.6 Frontera HTTP, Host y request smuggling — Alta

**Riesgo previo.** Host no confiable, cabeceras de identidad duplicadas, X-Forwarded-For ambiguo o framing inconsistente podían alterar redirecciones, auditoría, autenticación o cuotas.

**Corrección.** La aplicación incorpora:

- `redirect_slashes=False`, por lo que una variante con barra devuelve 404 sin `Location` construido desde Host;
- allowlist exacta `RTM_ALLOWED_HOSTS`, middleware 400 y preflight obligatorio en staging/producción;
- rechazo de duplicados de Authorization, Origin, Stripe-Signature y cabeceras RTM sensibles;
- normalización del cookie crumbling legítimo de HTTP/2, rechazando cookies sensibles repetidas;
- rechazo preventivo de cualquier `Transfer-Encoding` y de la combinación Content-Length/Transfer-Encoding;
- límites de cuerpo antes de parsers JSON/multipart y límite específico de webhook;
- XFF únicamente cuando el peer pertenece a CIDRs de proxy configurados; cualquier ambigüedad conserva el peer directo;
- CORS con orígenes exactos y HTTPS remoto obligatorio;
- rate limits por superficie, con `Retry-After` y separación de lectura/escritura.

**Evidencia.** Suites HTTP, environment y auth; pruebas focales de Host, framing, cabeceras duplicadas, XFF, CORS y cuerpos fragmentados. **Estado: probado.**

### 4.7 Integridad de checkout y carreras de creación — Crítica

**Riesgo previo.** Dos peticiones concurrentes podían intentar crear sesiones distintas, y datos del navegador podían influir en producto, importe o navegación.

**Corrección.** El checkout de revisión usa un claim durable y CAS:

- la intención se reclama bajo lock y se liga a la autoridad y cotización actuales;
- Stripe se llama fuera de la transacción DB con una clave idempotente estable;
- solo se publica la sesión que conserva el claim exacto;
- cualquier sesión remota perdedora se expira sin sustituir a la ganadora;
- producto, servicio, importe, moneda y URLs provienen de autoridad del servidor;
- la sesión remota se recupera y valida antes de devolver su URL;
- se restringe el método a tarjeta para el flujo nuevo;
- estados terminales o en conciliación no abren otro checkout.

El checkout de retirada de vehículo usa también caso autenticado, cotización autoritativa, metadata sin PII, reuse exacto y CAS.

**Evidencia.** Pruebas de solapamiento, timeout, reintento, sesión perdedora, undercharge, estado terminal y fallo DB. **Estado: probado.**

### 4.8 Webhooks, replay, pagos asíncronos y reversión — Crítica

**Riesgo previo.** Un replay, evento fuera de orden, `completed` todavía no pagado o reversión posterior podía duplicar eventos, conceder servicio sin liquidación o reabrir derechos suspendidos.

**Corrección.** El webhook:

- lee bytes crudos con máximo de 1 MiB antes de verificar la firma Stripe;
- exige coincidencia exacta de evento, sesión, payment intent, caso, contrato, importe, moneda, servicio y autoridad;
- deduplica por `stripe_event_id` bajo lock;
- trata `completed` no liquidado como pendiente, nunca como pago;
- procesa `async_payment_succeeded` por el mismo camino de liquidación;
- congela `async_payment_failed` sin conceder servicio;
- libera una expiración únicamente para la sesión exacta y emite un solo evento;
- procesa reembolsos y disputas de forma monótona y fail-closed;
- impide que un éxito tardío resucite un derecho reembolsado o un expediente cambiado;
- separa los contratos genérico y de retirada de vehículo para evitar fall-through.

**Evidencia.** `test_billing_checkout_state_machine` y `test_billing_vehicle_removal_settlement`: 24/24 en el cierre focal específico. **Estado: cerrado y probado focalmente.**

### 4.9 Estados, carreras y atomicidad de expediente — Crítica

**Riesgo previo.** Un titular podía cambiar el material después de generar autoridad o mientras un pago seguía cobrable; un fallo entre B2 y DB podía dejar objetos huérfanos o estados parciales.

**Corrección.** `rtm_core/case_state_policy.py` establece una política única con lock de fila para documentos, revisión, detalles, contacto, autorización, candidato firmado, verificación de vehículo y checkout. Bloquea mutaciones públicas durante `creating`, `pending`, `paid`, revisión, conciliación y estados terminales/protegidos.

Los flujos multietapa validan primero, escriben en transacciones acotadas y compensan en orden inverso los objetos externos si una fase posterior falla.

**Evidencia.** Suites de state policy, intake atomicity, reanalysis atomicity y B2 transactional cleanup. **Estado: probado.**

### 4.10 Presentación DGT sin falso éxito — Crítica

**Riesgo previo.** Una respuesta incompleta, proveedor no configurado o justificante ausente podía dejar el expediente aparentando estar listo o presentado sin prueba verificable.

**Corrección.** El flujo actual:

- bloquea proveedor no configurado/no implementado con estado y evento opacos y HTTP 503;
- no devuelve el expediente a `ready_to_submit` después de un resultado incierto;
- distingue presentación aceptada, justificante pendiente y resultado desconocido;
- exige justificante PDF válido, acotado, con SHA-256 y persistencia confirmada antes de avanzar;
- usa conciliación cuando el proveedor pudo aceptar pero no existe prueba completa;
- el tick no cuenta operaciones omitidas como procesadas y devuelve `ok:false` si algún elemento falla;
- la ausencia de esquema requerido falla cerrada con 503.

**Evidencia.** Suites de exception opacity y hardening: 43/43 focales. **Estado: cerrado y probado focalmente.**

### 4.11 Autoridad y sustitución de PDF — Crítica

**Riesgo previo.** Un PDF distinto, una autorización genérica o una firma subida sin revisión podían confundirse con representación aprobada.

**Corrección.** La cadena liga mediante HMAC y SHA-256:

- expediente e identidad;
- autoridad y versión;
- documento PDF emitido, tamaño y nonce;
- candidato firmado exacto;
- evento de visualización;
- supervisor individual y reautenticación reciente;
- checklist y decisión final v3.

El candidato permanece `pending_review` hasta completar la revisión. La autoridad DGT solo es válida para el tipo de expediente previsto y el consentimiento de preparación de vehículo nunca se convierte en representación genérica.

**Evidencia.** Suites de authorization evidence, PDF security, state policy y vehicle contracts. **Estado: probado.**

### 4.12 Partner y restaurantes — Alta

**Riesgo previo.** Sesiones bearer legacy, CSRF, enumeración, PIN separado de la escritura, rotación no atómica o claves idempotentes reutilizadas podían producir acceso indebido o duplicados.

**Corrección.** Se implantó:

- sesión partner cookie-only, expiración, revocación y hash en reposo;
- double-submit CSRF ligado a la sesión para mutaciones;
- coste Argon2 uniforme para identidades inexistentes/inactivas;
- contraseña temporal que no emite sesión hasta cambio atómico;
- cursores de paginación ligados a filtros y límites duros;
- restaurante activo, PIN y reserva comprobados en una sola transacción/lock;
- rotación de PIN mediante CAS sobre el hash actual;
- 401 uniforme y actores derivados por servidor;
- idempotencia ligada a restaurante y payload exacto.

**Evidencia.** Suite focal PIN/restaurante: 18/18, además de contratos partner y restaurant. **Estado: probado.**

### 4.13 Privacidad y capacidades en frontend — Alta

**Riesgo previo.** Capacidades en URLs, persistencia global de PII, respuestas tardías entre expedientes o fetch a un origen arbitrario podían filtrar acceso o mezclar identidades.

**Corrección.** El frontend:

- guarda capacidades por expediente en `sessionStorage`, no en almacenamiento persistente global;
- purga claves legacy y bearer-shaped text, incluso codificado repetidamente;
- exige coincidencia entre caso de la capacidad y path, query o body;
- envía capacidades solo a rutas same-origin bajo `/api`;
- rechaza rutas no canónicas, traversal, credenciales embebidas y redirects automáticos;
- usa generaciones de petición para descartar respuestas tardías de otro expediente;
- valida tamaño, shape y claves exactas de respuestas sensibles;
- limita navegación de checkout al host HTTPS autorizado;
- no envía cookies o credenciales RTM a SpainRoom.

**Evidencia.** Tests de case access, case session, safe navigation, authorization case, partner API/session y vehicle removal access. **Estado: probado.**

### 4.14 Cabeceras y build frontend — Alta

Se aplica CSP restrictiva, `frame-ancestors 'none'`, `object-src 'none'`, `nosniff`, HSTS, no-referrer, Permissions-Policy y no-store/noindex en superficies sensibles. El build productivo valida identidad de origen, busca secretos y artefactos prohibidos y excluye el PDF estático retirado.

**Resultado verificable del frontend:**

- Python: **228/228**;
- Node: **241/241**;
- `npm ci --ignore-scripts`: correcto;
- `npm audit` completo y producción: **0 vulnerabilidades conocidas**;
- build productivo: **111 módulos**;
- PDF prohibido: ausente de `dist`;
- preflight de preview/despliegue Vercel no autorizado: bloqueado antes de iniciar Vite.

La última comprobación se refiere al gate de identidad de preview/despliegue Vercel; **no debe describirse como una prueba de `vite preview` local**.

### 4.15 Supply chain y CI/CD — Alta

**Corrección.** Dependencias directas fijadas a versiones exactas; lockfile npm v3; lifecycle scripts deshabilitados durante instalación; GitHub Actions fijadas a SHA; checkout sin persistencia de credenciales; permisos mínimos; escaneo histórico antes de instalar dependencias; CODEOWNERS y Dependabot.

Los workflows usan `ubuntu-24.04`, no una etiqueta mayor flotante. Frontend CI ejecuta también la suite Python de contratos.

**Evidencia.** Contratos CI backend 15/15 y frontend 12/12; YAML válido; scanners histórico y actual limpios; `git diff --check` limpio. **Estado: probado localmente.**

## 5. Hallazgos históricos y trazabilidad

Se realizó un escaneo de los objetos Git alcanzables sin imprimir valores potencialmente sensibles:

| Repositorio | Cobertura del mirror temporal | Resultado de credenciales |
|---|---:|---|
| Backend | 29 ramas, 2 refs PR, 1.269 commits y 1.797 blobs | 0 credenciales plausibles; 0 blobs omitidos |
| Frontend | 10 ramas, 4 refs PR, 85 commits y 421 blobs | 0 credenciales plausibles; 0 blobs omitidos |

También se inspeccionaron mensajes de commit/tag y formatos binarios reconocibles. Una URL sintética de pruebas está allowlisted únicamente por ruta y digest exactos.

Esta trazabilidad técnica no equivale a una cadena de custodia forense certificada. Antes de reescribir el historial debe generarse un inventario firmado y preservado en almacenamiento restringido.

### 5.1 Riesgos históricos que requieren actuación

- Existió un workflow antiguo con una frontera insegura entre código seleccionado y un secreto protegido. Está corregido en el árbol actual, pero deben revisarse ejecuciones históricas y rotarse las credenciales afectadas si hay cualquier duda.
- Una migración legacy contenía identificadores operativos descritos como pertenecientes a un caso real. El archivo actual se retiró, pero los objetos históricos siguen requiriendo evaluación de privacidad y saneamiento autorizado.
- Una firma raster permanece rastreada y debe considerarse invalidada, aunque el flujo nuevo no dependa de ella.
- Un cron legacy exponía una credencial a la lista de argumentos del proceso; está retirado. Debe rotarse si llegó a utilizarse con un valor real.
- Un PDF estático sigue en el source frontend aunque el build verificado lo excluye de `dist`.
- Un gitlink histórico carece de objeto externo accesible y queda fuera del alcance auditable.
- Dos manifiestos históricos contienen seis digests declarados que no coinciden con blobs alcanzables. El addendum conserva los manifiestos originales y documenta la discrepancia; la procedencia de esos seis valores no puede reconstruirse.

No se reproduce en este informe ningún identificador personal, valor secreto ni detalle operativo innecesario.

## 6. Riesgos residuales y bloqueos

### 6.1 Bloqueos antes de producción

1. Confirmar, enviar y revisar los cambios; el `main` remoto todavía no queda endurecido por este trabajo local.
2. Ejecutar en CI remoto `pip-audit --strict` con acceso a la base de vulnerabilidades y conservar su artefacto.
3. Ejecutar CI remoto con PostgreSQL y revisar sus artefactos.
4. Investigar y sanear de forma controlada las exposiciones históricas.
5. Verificar protección de rama, checks requeridos y aprobación del entorno GitHub.
6. Verificar la configuración efectiva de Vercel, Render, B2, Stripe, PostgreSQL, SMTP, DNS, proxy y WAF.

### 6.2 Riesgos altos residuales

- El proceso aislado de parsing reduce impacto, pero no sustituye un sandbox de kernel. Debe ejecutarse como servicio/contenedor no-root, sin red, con filesystem de solo lectura, cgroups y perfil seccomp/AppArmor.
- La cookie de dispositivo aporta posesión, pero no es MFA. WebAuthn o MFA sigue siendo necesario para producción sensible.
- Las capacidades públicas son stateless y aún carecen de revocación individual, scopes por acción y JTI persistente.
- Las cuotas son locales al proceso/IP; requieren Redis/WAF y límites por cuenta para defensa distribuida.
- Deben probarse en el proxy real los límites, timeouts y rechazo de framing ambiguo.
- La atestación HMAC prueba custodia RTM, no identidad legal del firmante. Se necesita un mecanismo jurídico como PAdES, eID u OTP validado.
- La auditoría DB no es un ledger criptográficamente inmutable frente a un escritor privilegiado.
- La limpieza B2 es compensatoria y best-effort; requiere reconciliador, bucket privado verificado y política de lifecycle/retención.
- El visor aún entrega un PDF validado al renderer del navegador; una vulnerabilidad del motor PDF queda fuera del control de RTM.
- El checkout final genérico permanece retirado hasta disponer de un presupuesto OPS persistido, versionado y firmado.
- La autenticación individual está desplegándose por fases y producción falla cerrada hasta activación expresa.

### 6.3 Riesgos medios residuales

- La imagen `postgres:17` permanece ligada a una etiqueta mutable: no se obtuvo un digest verificable y no se ha inventado uno.
- `glob@10.5.0`, dependencia transitiva de la cadena Tailwind/Sucrase, está deprecada pero el audit no reporta CVE. Debe migrarse de forma coordinada.
- No existe todavía SBOM firmado, procedencia de artefactos ni verificación por hash de wheels Python.
- Trusted Types sigue siendo una mejora planificada.
- El bundle frontend ronda 689 kB y merece división/optimización posterior.
- La integración SpainRoom depende de CORS exacto y credentialless configurado en el servicio externo.
- La ruta de reanálisis largo abre transacciones internas posteriores sin scope repetido, aunque su endpoint actual es solo supervisor con scope global; no se observó bypass cross-case activo. Conviene mantenerlo en el registro de deuda técnica.
- La ruta estática legacy `/ops/cases/presented` queda ocultada por una ruta dinámica anterior; la ruta activa `/ops/presented-cases` funciona. Conviene retirar la duplicada para evitar confusión operativa.
- El checkout de retirada de vehículo mantiene un lock DB durante la llamada a Stripe. No se observó un bypass de integridad, pero conviene migrarlo al patrón trifásico del checkout genérico para reducir contención y riesgo de disponibilidad.

## 7. Cambios de comportamiento y operación

- Las mutaciones públicas de identidad, contacto, documentos, revisión, autorización y vehículo devuelven 409 durante checkout y tras pago/revisión. Los cambios posteriores requieren flujo OPS auditable.
- Login y mutadores OPS compartidos quedan retirados o cerrados por 410/503 según la superficie.
- Las migraciones administrativas pasan a tareas offline y dejan de publicarse como routers HTTP.
- La creación activa de casos partner y el checkout final legacy permanecen cerrados hasta disponer de contratos seguros completos.
- Los conectores DGT/Registro/cron legacy no ejecutan efectos externos.
- La extracción documental externa sin autoridad específica devuelve 503.
- Las sesiones OPS no sobreviven a expiración, cierre del ciclo de página o restauración BFCache.
- Los operadores no reciben coordenadas B2 ni exportaciones directas; los documentos sensibles se sirven mediante rutas controladas.
- Node 24 es requisito del frontend y CI instala con `--ignore-scripts`.
- Los probes deben distinguir `/health/live` de `/health/ready`.
- SpainRoom usa un origen HTTPS exacto directamente y sin cookies RTM.
- El PDF estático retirado no forma parte del bundle productivo.

## 8. Limitaciones de esta auditoría

- El conector Codex Security no pudo establecerse; no se presenta como si hubiera producido resultados.
- No se ejecutaron SAST comercial, Bandit, DAST, fuzzing protocolario, pentest independiente ni pruebas de carga.
- No hubo acceso a tráfico o logs de producción, secret manager ni paneles completos de los proveedores.
- Stripe, B2, OpenAI, SMTP, DGT y Registro se probaron principalmente mediante contratos y dobles, no con operaciones productivas.
- El escaneo de secretos no detecta esteganografía, secretos cifrados ni contenido del gitlink inaccesible.
- Los audits de dependencias solo cubren vulnerabilidades conocidas por sus bases de datos en el momento de ejecución.
- No se realizó una evaluación jurídica o de protección de datos formal.
- Una revisión amplia reduce el riesgo, pero no demuestra ausencia de zero-days o fallos de configuración externa.

## 9. Checklist predeploy

### P0 — obligatoria

- [x] Registrar la suite local final, focales, compilación, diff y escaneo histórico.
- [ ] Obtener un `pip-audit --strict` concluyente en CI con acceso a la base CVE.
- [ ] Congelar el diff y registrar los SHAs exactos de release.
- [ ] Obtener revisión independiente de las fronteras de auth, pagos, scope, IA y parsers.
- [ ] Con autorización expresa, hacer commit y push de ambas ramas.
- [ ] Exigir CI verde, incluyendo PostgreSQL, scanners, audits y build.
- [ ] Preservar evidencia histórica antes de reescribir objetos Git.
- [ ] Investigar ejecuciones del workflow antiguo y rotar/revocar credenciales si corresponde.
- [ ] Evaluar la exposición histórica de datos con responsables legales y de privacidad.
- [ ] Retirar firma raster, PDF fuente y objetos legacy; sanear historial de forma coordinada.
- [ ] Proteger `main` con checks requeridos, CODEOWNERS, revisores y aprobación de entorno.
- [ ] Verificar configuración real de Host, CORS, proxy, Stripe, B2, DB y correo.
- [ ] Probar en Stripe test-mode pago, async, expiración, replay, refund y dispute.
- [ ] Preparar rollback por SHA y verificar backup/restore antes de migraciones.
- [ ] Igualar repos remotos y PCs solo contra los SHAs finalmente aprobados.

### P1 — antes de tráfico sensible

- [ ] Aislar parsers con controles de kernel y red.
- [ ] Instalar rate limiting distribuido, WAF y anti-bot.
- [ ] Activar MFA/WebAuthn y extender step-up a toda mutación privilegiada.
- [ ] Migrar capacidades públicas a JTI, TTL corto, scopes y revocación.
- [ ] Verificar TLS con CA, mínimo privilegio DB y restauración de backups.
- [ ] Verificar ACL/lifecycle de B2 y ejecutar reconciliador de huérfanos.
- [ ] Implantar auditoría inmutable o ledger firmado.
- [ ] Adoptar firma electrónica jurídicamente válida.
- [ ] Confirmar CORS SpainRoom exacto y sin credenciales.
- [ ] Añadir SBOM, procedencia y firma de artefactos.
- [ ] Ejecutar pentest independiente y E2E en entorno efímero similar a producción.
- [ ] Alertar sobre auth anómala, cuotas, parser kills, webhook retries y conciliaciones.

## 10. Conclusión

El sistema queda mucho mejor preparado para resistir entradas maliciosas y errores concurrentes. Las defensas principales siguen un criterio fail-closed: una prueba incompleta, ambigua o fuera de scope no produce acceso, pago reconocido, autoridad, presentación externa ni mutación silenciosa.

La protección adecuada es preventiva y trazable: aislar, negar, registrar, revocar, conciliar y recuperar. Este trabajo no contempla represalias ni acciones fuera de los sistemas propios de RTM.
