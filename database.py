import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL no está configurada en variables de entorno.")
    return url

def get_engine() -> Engine:
    # pool_pre_ping evita conexiones muertas en Render
    return create_engine(get_database_url(), pool_pre_ping=True)

def ping_db(engine: Engine) -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
