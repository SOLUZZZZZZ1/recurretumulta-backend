"""Offline bootstrap of one synthetic supervisor in the dedicated local DB.

Requires the explicit local development profile. It never replaces credentials,
repairs roles, creates a remote account, or grants Presenter/admin capabilities.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from database import get_engine
from rtm_core.local_operator_auth import assert_local_operator_auth_ready
from rtm_core.operator_auth_crypto import hash_operator_password, validate_operator_password


LOCAL_EMAIL = "rtm-local-supervisor@example.com"
LOCAL_ROLE = "rtm.supervisor"
LOCAL_PERMISSIONS = ("ops.supervise", "ops.view")


def require_local_database(conn) -> None:
    row = conn.execute(text("""
        SELECT current_database() AS database_name,
               current_user AS user_name, session_user AS session_name,
               host(inet_server_addr()) AS server_address,
               host(inet_client_addr()) AS client_address,
               inet_server_port() AS server_port,
               current_setting('server_version_num')::integer AS version,
               pg_get_userbyid(d.datdba) AS owner_name,
               r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication
        FROM pg_database d JOIN pg_roles r ON r.rolname=current_user
        WHERE d.datname=current_database()
    """)).mappings().one()
    expected = {
        "database_name": "rtm_local", "user_name": "rtm_local_app",
        "session_name": "rtm_local_app", "server_address": "127.0.0.1",
        "client_address": "127.0.0.1", "server_port": 5432,
        "owner_name": "rtm_local_app", "rolsuper": False,
        "rolcreatedb": False, "rolcreaterole": False, "rolreplication": False,
    }
    if any(row[key] != value for key, value in expected.items()) or row["version"] // 10000 != 17:
        raise RuntimeError("La conexion no es la base local PostgreSQL 17 esperada.")


def existing_local_operator(conn):
    require_local_database(conn)
    row = conn.execute(text("""
        SELECT o.id, o.email, o.status, o.profile, o.must_change_password,
               o.mfa_required, r.code, r.permissions, r.active, r.system_role
        FROM rtm_operators o LEFT JOIN rtm_operator_roles r ON r.id=o.primary_role_id
        WHERE lower(btrim(o.email))=:email
    """), {"email": LOCAL_EMAIL}).mappings().one_or_none()
    if row is None:
        return None
    profile = row["profile"]
    if (
        not isinstance(profile, dict)
        or profile.get("synthetic") is not True
        or profile.get("local_only") is not True
        or profile.get("environment") != "development"
        or row["status"] != "active"
        or row["code"] != LOCAL_ROLE
        or row["active"] is not True
        or row["system_role"] is not True
        or row["must_change_password"] is not False
        or row["mfa_required"] is not False
        or set(row["permissions"] or []) != set(LOCAL_PERMISSIONS)
    ):
        raise RuntimeError("El operador existente requiere revision; no se ha modificado.")
    return str(row["id"])


def create_local_operator(conn, password: str) -> str:
    assert_local_operator_auth_ready()
    require_local_database(conn)
    validate_operator_password(password)
    conn.execute(text("LOCK TABLE rtm_operators, rtm_operator_roles IN SHARE ROW EXCLUSIVE MODE"))
    existing = existing_local_operator(conn)
    if existing is not None:
        return existing
    if conn.execute(text("SELECT count(*) FROM rtm_operators")).scalar_one():
        raise RuntimeError("La provision inicial requiere una tabla de operadores vacia.")
    if conn.execute(text("SELECT count(*) FROM rtm_operator_roles")).scalar_one():
        raise RuntimeError("La provision inicial requiere una tabla de roles vacia.")
    if conn.execute(text("SELECT count(*) FROM cases WHERE test_mode IS NOT TRUE")).scalar_one():
        raise RuntimeError("La base contiene expedientes que no estan marcados como prueba.")
    operator_id, role_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(text("""
        INSERT INTO rtm_operator_roles
            (id, code, name, description, permissions, system_role, active)
        VALUES (CAST(:id AS UUID), :code, :name, :description,
                CAST(:permissions AS JSONB), TRUE, TRUE)
    """), {
        "id": role_id, "code": LOCAL_ROLE, "name": "Supervisor local de pruebas",
        "description": "Acceso OPS a datos sinteticos en el PC local.",
        "permissions": json.dumps(list(LOCAL_PERMISSIONS)),
    })
    conn.execute(text("""
        INSERT INTO rtm_operators
            (id, email, display_name, password_hash, status, primary_role_id,
             must_change_password, mfa_required, profile, password_changed_at,
             password_algorithm, password_version, auth_epoch)
        VALUES (CAST(:id AS UUID), :email, :name, :password_hash, 'active',
                CAST(:role_id AS UUID), FALSE, FALSE, CAST(:profile AS JSONB),
                NOW(), 'argon2id', 1, 1)
    """), {
        "id": operator_id, "email": LOCAL_EMAIL,
        "name": "Supervisor local de pruebas", "role_id": role_id,
        "password_hash": hash_operator_password(password),
        "profile": json.dumps({"synthetic": True, "local_only": True,
                               "environment": "development", "purpose": "local_operator_auth"}),
    })
    return operator_id


def main() -> int:
    assert_local_operator_auth_ready()
    engine = get_engine()
    with engine.connect() as conn:
        if existing_local_operator(conn):
            print(f"OPERADOR_LOCAL_EXISTENTE: {LOCAL_EMAIL}")
            return 0
    print(f"Crear operador de pruebas: {LOCAL_EMAIL}")
    password = getpass.getpass("Nueva contrasena OPS local (minimo 12 caracteres): ")
    confirmation = getpass.getpass("Repite la contrasena OPS local: ")
    if password != confirmation:
        raise ValueError("Las contrasenas no coinciden.")
    with engine.begin() as conn:
        create_local_operator(conn, password)
    print(f"OPERADOR_LOCAL_CREADO: {LOCAL_EMAIL}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
