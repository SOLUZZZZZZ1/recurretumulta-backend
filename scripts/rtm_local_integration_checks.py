"""Prueba CORE en un PostgreSQL efímero propio; nunca usa DATABASE_URL heredada.

Requiere los binarios de PostgreSQL ya instalados. No instala servicios, no
usa rtm_local ni su puerto/contraseña, y bloquea red exterior en el trabajador.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid

SUITES = (
    "tests.test_rtm_postgres_integration",
    "tests.test_rtm_workspace_postgres_integration",
)
NAME_PREFIX = "rtm_check_"
MARKER = "rtm-integration-owner.json"


def clean_environment(source):
    keep = {"systemroot", "windir", "comspec", "path", "pathext", "temp", "tmp",
            "userprofile", "appdata", "localappdata", "programfiles", "programfiles(x86)"}
    env = {key: value for key, value in source.items() if key.lower() in keep}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", RTM_ENV="test",
               RTM_CORE_INTEGRATION_DB="1", RTM_ALLOW_REAL_CUSTOMER_DATA="0")
    for name in ("B2", "STRIPE", "FINAL_PAYMENTS", "DOCUMENT_PROVIDER",
                 "OUTBOUND_EMAIL", "EXTERNAL_SUBMISSION"):
        env[f"RTM_ENABLE_{name}"] = "0"
    return env


def verify_identity(row, *, folder, database, role, port):
    if not database.startswith(NAME_PREFIX) or not role.startswith(NAME_PREFIX):
        raise RuntimeError("La base y el usuario deben pertenecer a esta prueba")
    if not 49152 <= port <= 65535:
        raise RuntimeError("Puerto de prueba fuera del intervalo reservado")
    expected = (database, role, "127.0.0.1", port)
    mismatches = [label for label, actual, wanted in zip(
        ("database", "role", "host", "port"), row[:4], expected,
    ) if actual != wanted]
    if Path(row[4]).resolve() != folder.resolve():
        mismatches.append("data_directory")
    if mismatches:
        raise RuntimeError("Identidad del clúster efímero inconsistente: " + ", ".join(mismatches))


def network_guard(port):
    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or tuple(address[:2]) != ("127.0.0.1", port):
                raise RuntimeError("La prueba solo puede conectar con su PostgreSQL efímero")
        elif event == "socket.getaddrinfo":
            if args[0] not in {"127.0.0.1", None}:
                raise RuntimeError("Resolución de red exterior bloqueada en la prueba")
    sys.addaudithook(audit)


def worker(repo, report_path):
    import unittest
    import psycopg
    from sqlalchemy.engine import URL

    config = json.loads(os.environ["RTM_EPHEMERAL_CONFIG"])
    folder = Path(config["folder"]).resolve()
    marker = json.loads((folder.parent / MARKER).read_text(encoding="utf-8"))
    if marker != {key: config[key] for key in ("nonce", "database", "role", "port", "folder")}:
        raise RuntimeError("La marca de propiedad del clúster no coincide")
    network_guard(config["port"])
    with psycopg.connect(host="127.0.0.1", port=config["port"], dbname=config["database"],
                         user=config["role"], password=config["password"], connect_timeout=3) as conn:
        row = conn.execute("SELECT current_database(), current_user, host(inet_server_addr()), "
                           "inet_server_port(), current_setting('data_directory')").fetchone()
        verify_identity(row, folder=folder, database=config["database"],
                        role=config["role"], port=config["port"])
    # Construct the URL only after verifying the actual server identity.
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg", username=config["role"], password=config["password"],
        host="127.0.0.1", port=config["port"], database=config["database"],
    ).render_as_string(hide_password=False)
    sys.path.insert(0, str(repo))
    suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue().replace(config["password"], "[redacted]")
    report_path.with_suffix(".log").write_text(output, encoding="utf-8")
    report = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), passed=result.wasSuccessful() and not result.skipped,
                  suites=list(SUITES), postgres_temporary=True, external_services=False)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output, flush=True)
    return 0 if report["passed"] else 1


def run(repo, pg_bin, reports):
    import psycopg
    from psycopg import sql

    repo, pg_bin, reports = repo.resolve(), pg_bin.resolve(), reports.resolve()
    for name in ("initdb.exe", "pg_ctl.exe"):
        if not (pg_bin / name).is_file():
            raise RuntimeError(f"Falta el binario instalado {name}")
    for name in SUITES:
        if not (repo / (name.replace(".", "/") + ".py")).is_file():
            raise RuntimeError("No se encuentra una de las pruebas previstas")
    reports.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.gettempdir()).resolve()
    root = Path(tempfile.mkdtemp(prefix="rtm-integration-", dir=base)).resolve()
    if root.parent != base:
        raise RuntimeError("El clúster temporal queda fuera del directorio previsto")
    folder = root / "data"
    token = uuid.uuid4().hex
    name = NAME_PREFIX + token[:16]
    password = secrets.token_urlsafe(40)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    if not 49152 <= port <= 65535:
        raise RuntimeError("Windows no ha asignado un puerto efímero del intervalo previsto")
    owner = dict(nonce=token, database=name, role=name, port=port, folder=str(folder))
    (root / MARKER).write_text(json.dumps(owner), encoding="utf-8")
    password_file = root / "password.txt"
    password_file.write_text(password + "\n", encoding="ascii")
    env = clean_environment(os.environ)
    report_path = reports / f"integration-{token[:12]}.json"
    may_be_running = False
    stopped = True

    def command(arguments, timeout=45):
        # A PostgreSQL child on Windows can inherit PIPE handles and prevent
        # communicate() from returning even after pg_ctl exits. Use a file.
        capture = root / f"command-{uuid.uuid4().hex}.log"
        with capture.open("wb") as output:
            result = subprocess.run(arguments, env=env, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            detail = capture.read_text(encoding="utf-8", errors="replace").replace(password, "[redacted]")
            raise RuntimeError(detail[-3500:])
        return result

    try:
        print("Preparando PostgreSQL temporal independiente...", flush=True)
        command([str(pg_bin / "initdb.exe"), "-D", str(folder), "-U", name,
                 "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                 "--pwfile", str(password_file), "--encoding=UTF8", "--locale=C"])
        password_file.unlink()
        may_be_running = True
        command([str(pg_bin / "pg_ctl.exe"), "-D", str(folder), "-l", str(root / "postgres.log"),
                 "-w", "-t", "20", "-o", f"-h 127.0.0.1 -p {port}", "start"])
        with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user=name,
                             password=password, connect_timeout=3, autocommit=True) as conn:
            actual = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
            if actual != folder.resolve():
                raise RuntimeError("La instancia iniciada no pertenece a esta prueba")
            conn.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(name)))
        env["RTM_EPHEMERAL_CONFIG"] = json.dumps({**owner, "password": password})
        print("Identidad del clúster comprobada. Ejecutando el recorrido CORE...", flush=True)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
             "--repo", str(repo), "--report", str(report_path)],
            env=env, cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (result.stdout + result.stderr).replace(password, "[redacted]")
        if not report_path.exists():
            report_path.with_suffix(".log").write_text(output, encoding="utf-8")
        print(output, flush=True)
        print("Informe:", report_path, flush=True)
        return result.returncode
    finally:
        password_file.unlink(missing_ok=True)
        if may_be_running:
            try:
                command([str(pg_bin / "pg_ctl.exe"), "-D", str(folder), "-w", "-t", "20",
                         "-m", "fast", "stop"])
            except Exception:
                status = subprocess.run(
                    [str(pg_bin / "pg_ctl.exe"), "-D", str(folder), "status"],
                    env=env, capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                stopped = status.returncode == 3 and not (folder / "postmaster.pid").exists()
                if not stopped:
                    print("No se confirmó la parada; conservar el clúster para revisión:", root, flush=True)
        if stopped:
            if root.resolve().parent != base or json.loads((root / MARKER).read_text()) != owner:
                raise RuntimeError("No se elimina una carpeta sin su marca de propiedad exacta")
            shutil.rmtree(root)
            print("Clúster temporal detenido y eliminado; rtm_local permanece intacta.", flush=True)
        if not stopped:
            raise RuntimeError("Revisar el proceso temporal antes de repetir la prueba")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--postgres-bin", type=Path, default=Path(r"C:\Program Files\PostgreSQL\17\bin"))
    parser.add_argument("--reports", type=Path, default=Path(tempfile.gettempdir()) / "rtm-integration-reports")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.repo.resolve(), args.report.resolve())
    return run(args.repo, args.postgres_bin, args.reports)


if __name__ == "__main__":
    raise SystemExit(main())
