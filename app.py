import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import HealthResponse
from database import get_engine, ping_db

from admin_migrate import router as admin_migrate_router
from analyze import router as analyze_router
from analyze_expediente import router as analyze_expediente_router
from generate import router as generate_router
from files import router as files_router
from billing import router as billing_router
from admin_migrate_payments import router as admin_payments_router
from ai_router import router as ai_router
from partner_cases import router as partner_cases_router
from ops_automation_router import router as ops_automation_router



# ✅ AÑADIDO: OPS (operador)
from ops import router as ops_router
from ops_restaurant_reservations import router as ops_restaurant_router
from cases import router as cases_router
from partner import router as partner_router
from ops_override import router as ops_override_router


app = FastAPI(title="RecurreTuMulta Backend", version="0.1.0")

allowed = os.getenv("ALLOWED_ORIGINS", "").strip()
origins = [o.strip() for o in allowed.split(",") if o.strip()] if allowed else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers existentes
app.include_router(admin_migrate_router)
app.include_router(analyze_router)
app.include_router(analyze_expediente_router)
app.include_router(generate_router)
app.include_router(files_router)
app.include_router(billing_router)
app.include_router(admin_payments_router)
app.include_router(ai_router)
app.include_router(partner_cases_router)
app.include_router(ops_automation_router)

# ✅ NUEVO: router de operador (/ops/*)
app.include_router(ops_router)
app.include_router(ops_restaurant_router)
app.include_router(cases_router)
app.include_router(partner_router)
app.include_router(ops_override_router)


@app.get("/health", response_model=HealthResponse)
def health():
    try:
        engine = get_engine()
        ping_db(engine)
        return HealthResponse(ok=True)
    except Exception:
        return HealthResponse(ok=True)
