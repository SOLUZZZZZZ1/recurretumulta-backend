from __future__ import annotations

import atexit
import os
from threading import RLock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


_ENGINE_LOCK = RLock()
_ENGINES: dict[str, Engine] = {}


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL no está configurada en variables de entorno.")
    return url


def get_engine() -> Engine:
    """Devuelve un único pool SQLAlchemy por URL durante la vida del proceso.

    El backend llamaba antes a ``create_engine`` en cada petición. Además de
    multiplicar pools, los motores temporales podían ser recolectados con
    conexiones todavía abiertas durante pruebas y reinicios. El registro queda
    aislado por URL para que staging, pruebas y producción no compartan pool.
    """

    url = get_database_url()
    with _ENGINE_LOCK:
        engine = _ENGINES.get(url)
        if engine is None:
            # pool_pre_ping evita reutilizar conexiones muertas en Render.
            engine = create_engine(
                url,
                pool_pre_ping=True,
                hide_parameters=True,
            )
            _ENGINES[url] = engine
        return engine


def dispose_all_engines() -> int:
    """Cierra todos los pools registrados y devuelve cuántos se liberaron.

    Se usa al terminar el proceso y también permite que las pruebas cambien de
    DATABASE_URL sin dejar pools o conexiones huérfanos.
    """

    with _ENGINE_LOCK:
        engines = tuple(_ENGINES.values())
        _ENGINES.clear()

    for engine in engines:
        try:
            engine.dispose()
        except Exception:
            # El cierre del proceso no debe quedar bloqueado por un pool roto.
            pass
    return len(engines)


def ping_db(engine: Engine) -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


atexit.register(dispose_all_engines)
