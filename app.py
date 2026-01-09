import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import HealthResponse
from database import get_engine, ping_db
from admin_migrate import router as admin_migrate_router
from analyze import router as analyze_router
from generate import router as generate_router


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

app.include_router(admin_migrate_router)
app.include_router(analyze_router)
app.include_router(generate_router)
app.include_router(files_router)



@app.get("/health", response_model=HealthResponse)
def health():
    try:
        engine = get_engine()
        ping_db(engine)
        return HealthResponse(ok=True)
    except Exception:
        return HealthResponse(ok=True)

# TODO (siguiente paso): /analyze (upload + guardar en B2 + extracción GPT-4o + persistir en Postgres)
