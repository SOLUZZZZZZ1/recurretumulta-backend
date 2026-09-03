import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from schemas import HealthResponse
from database import get_engine, ping_db


from analyze import router as analyze_router
from analyze_expediente import router as analyze_expediente_router
from files import router as files_router
from billing import router as billing_router
from ops_automation_router import router as ops_automation_router
from ops_operator_router import router as ops_operator_router
from ops_queue_smart import router as ops_queue_smart_router
from ops_vehicle_removal_router import router as ops_vehicle_removal_router
from contact_backend_fastapi import router as contact_router
from vehicle_removal_router import router as vehicle_removal_router
from rtm_core.legacy_guard_router import router as rtm_core_legacy_guard_router
from rtm_core.intake_router import router as rtm_core_intake_router
from rtm_core.router import router as rtm_core_router
from rtm_core.workspace_router import router as rtm_core_workspace_router
from rtm_core.document_facts_router import router as rtm_core_document_facts_router
from rtm_core.document_extraction_router import (
    router as rtm_core_document_extraction_router,
)
from rtm_core.document_input_policy import document_input_policy_block
from rtm_core.document_extraction import extraction_limits
from rtm_core.environment_contract import (
    assert_environment_ready,
    runtime_requires_environment_preflight,
)
from rtm_core.parser_isolation import assert_parser_isolation_ready
from rtm_core.http_security import (
    ExactHostMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeaderAmbiguityMiddleware,
    SecurityHeadersMiddleware,
    SensitiveRateLimitMiddleware,
    configured_allowed_hosts,
    parse_allowed_origins,
    scope_path,
)
from rtm_core.reanalysis_execution import install_safe_extraction_policy
from rtm_core.reanalysis_execution_router import (
    router as rtm_core_reanalysis_execution_router,
)
from rtm_core.reanalysis_router import router as rtm_core_reanalysis_router
from rtm_core.authority_router import router as rtm_core_authority_router
from rtm_core.family_router import router as rtm_core_family_router
from rtm_core.specialist_router import router as rtm_core_specialist_router
from rtm_core.preview_router import router as rtm_core_preview_router
from rtm_core.generation_router import router as rtm_core_generation_router
from rtm_core.operator_auth_router import router as rtm_operator_auth_router
from rtm_core.legacy_ops_session_bridge import (
    legacy_ops_individual_session_bridge,
)
from rtm_core.operator_admin_router import (
    router as rtm_operator_admin_router,
)
from rtm_core.operator_lifecycle_router import (
    router as rtm_operator_lifecycle_router,
)
from rtm_presenter_router import router as rtm_presenter_router
from rtm_connect.supervisor_router import (
    connect_supervisor_gate_middleware,
    router as connect_supervisor_router,
)
from rtm_connect.human_filing_router import (
    human_filing_gate_middleware,
    router as connect_human_filing_router,
)


# ✅ AÑADIDO: OPS (operador)
from ops import router as ops_router
from ops_restaurant_reservations import router as ops_restaurant_router
from cases import router as cases_router
from partner import router as partner_router


# El selector de lectura profunda se instala al arrancar la aplicación. De este
# modo cualquier llamada interna a Reanalysis utiliza la política conservadora,
# aunque el módulo legacy ya hubiera sido importado por otro router.
install_safe_extraction_policy()

_DEPLOYED_PROFILE = runtime_requires_environment_preflight()
_APP_ALLOWED_HOSTS = configured_allowed_hosts()
app = FastAPI(
    title="RecurreTuMulta Backend",
    version="0.1.0",
    # Starlette reconstruye los redirects automáticos de barra final con el
    # header Host. Como la API no necesita redirects canónicos, una variante
    # de ruta debe fallar 404 y nunca producir un Location controlable.
    redirect_slashes=False,
    docs_url=None if _DEPLOYED_PROFILE else "/docs",
    redoc_url=None if _DEPLOYED_PROFILE else "/redoc",
    openapi_url=None if _DEPLOYED_PROFILE else "/openapi.json",
)


@app.on_event("startup")
def validate_deployed_environment() -> None:
    """Abort deployed profiles before serving if the safety contract is invalid."""

    if runtime_requires_environment_preflight():
        assert_environment_ready()
        extraction_limits()
        assert_parser_isolation_ready()

# Cota dura previa al parseo de JSON/multipart. Los routers mantienen además
# sus límites específicos, normalmente mucho menores.
app.add_middleware(RequestBodyLimitMiddleware)

# En staging, las superficies OPS legacy dejan de aceptar la credencial
# compartida del navegador. Se registra antes de CORS para que también las
# denegaciones del puente conserven el contrato CORS del frontend.
app.middleware("http")(legacy_ops_individual_session_bridge)

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


@app.exception_handler(RequestValidationError)
async def redact_request_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    """No refleja credenciales, PII ni cargas gigantes en ningún error 422."""

    issues = []
    for error in exc.errors()[:20]:
        location = []
        for segment in error.get("loc", ())[:8]:
            value = str(segment)[:64]
            location.append(
                value
                if value and all(ch.isalnum() or ch in "_.-" for ch in value)
                else "field"
            )
        issues.append(
            {
                "location": ".".join(location)[:256],
                "type": str(error.get("type") or "validation_error")[:80],
            }
        )
    return JSONResponse(
        status_code=422,
        content={"detail": "Solicitud no válida", "issues": issues},
        headers=_NO_STORE_HEADERS,
    )


@app.middleware("http")
async def enforce_document_input_policy(request: Request, call_next):
    """Bloquea en runtime entradas documentales incompatibles con el entorno."""

    block = document_input_policy_block(
        method=request.method,
        path=scope_path(request),
    )
    if block is not None:
        return JSONResponse(
            status_code=block.status_code,
            content={"detail": block.detail},
        )
    return await call_next(request)


origins = parse_allowed_origins(os.getenv("ALLOWED_ORIGINS"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# El gate C5 se registra despues de CORS para quedar como capa exterior y
# ocultar tambien OPTIONS/metodos invalidos cuando el panel esta cerrado.
app.middleware("http")(connect_supervisor_gate_middleware)

# A1-S publica el contrato de operacion humana exclusivamente tras su gate
# staging/sintetico. El middleware oculta tambien OPTIONS y metodos invalidos
# cuando la fase no esta habilitada.
app.middleware("http")(human_filing_gate_middleware)


@app.middleware("http")
async def no_store_private_ops(request: Request, call_next):
    """Evita cachear respuestas privadas OPS/partner, incluidos errores."""

    response = await call_next(request)
    path = scope_path(request)
    ops_path = path == "/ops" or path.startswith("/ops/")
    partner_path = path == "/partner" or path.startswith("/partner/")
    private_path = ops_path or partner_path
    if private_path:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        vary = [
            value.strip()
            for value in response.headers.get("Vary", "").split(",")
            if value.strip()
        ]
        if "*" in vary:
            return response
        known_vary = {value.casefold() for value in vary}
        for value in (
            "Authorization",
            "Cookie",
            "X-Operator-Token",
            "X-RTM-Device",
            "X-CSRF-Token",
        ):
            if value.casefold() not in known_vary:
                vary.append(value)
                known_vary.add(value.casefold())
        response.headers["Vary"] = ", ".join(vary)
    return response

# El cortafuegos y las rutas seguras CORE se registran antes de los routers legacy.
app.include_router(rtm_core_legacy_guard_router)
app.include_router(rtm_core_intake_router)

# Routers existentes. Las migraciones administrativas son tareas offline y no
# se montan en la aplicación HTTP.
app.include_router(analyze_router)
app.include_router(analyze_expediente_router)
app.include_router(files_router)
app.include_router(billing_router)
app.include_router(ops_automation_router)
app.include_router(ops_operator_router)
app.include_router(ops_queue_smart_router)
app.include_router(ops_vehicle_removal_router)
app.include_router(contact_router)
app.include_router(vehicle_removal_router)
app.include_router(rtm_core_router)
app.include_router(rtm_core_workspace_router)
app.include_router(rtm_core_document_facts_router)
app.include_router(rtm_core_document_extraction_router)
app.include_router(rtm_core_reanalysis_execution_router)
app.include_router(rtm_core_reanalysis_router)
app.include_router(rtm_core_authority_router)
app.include_router(rtm_core_family_router)
app.include_router(rtm_core_specialist_router)
app.include_router(rtm_core_preview_router)
app.include_router(rtm_core_generation_router)
app.include_router(rtm_operator_auth_router)
app.include_router(rtm_operator_admin_router)
app.include_router(rtm_operator_lifecycle_router)
app.include_router(rtm_presenter_router)
app.include_router(connect_supervisor_router)
app.include_router(connect_human_filing_router)


# ✅ NUEVO: router de operador (/ops/*)
app.include_router(ops_router)
app.include_router(ops_restaurant_router)
app.include_router(cases_router)
app.include_router(partner_router)

# Última capa registrada: añade cabeceras también a errores y denegaciones de
# los gates interiores. El límite de cuerpo sigue actuando antes de los parsers.
app.add_middleware(SensitiveRateLimitMiddleware)
app.add_middleware(ExactHostMiddleware, allowed_hosts=_APP_ALLOWED_HOSTS)
app.add_middleware(SecurityHeaderAmbiguityMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health/live", response_model=HealthResponse)
def health_live():
    return HealthResponse(ok=True)


@app.get("/health", response_model=HealthResponse)
@app.get("/health/ready", response_model=HealthResponse)
def health():
    try:
        engine = get_engine()
        ping_db(engine)
        return HealthResponse(ok=True)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"ok": False},
            headers={"Cache-Control": "no-store"},
        )
