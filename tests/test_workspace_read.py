"""Full app tests. Synthetic SQLite only; no operational credentials or endpoints."""
import json
import os
import socket
from pathlib import Path
from uuid import uuid4

import unittest
from unittest.mock import patch
from contextlib import contextmanager, ExitStack
import inspect as pyinspect
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# These are public fixture markers, not credentials. Startup constructs SDK clients
# but a deny-by-default network guard prevents any remote service call.
os.environ["OPERATOR_TOKEN"] = "workspace-fixture-token"
os.environ["OPENAI_API_KEY"] = "isolated-not-a-real-key"
os.environ["DATABASE_URL"] = "sqlite://"
_real_connect = socket.socket.connect
def isolated_connect(sock, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1") and address[1] not in (5432, 8000, 5173):
        return _real_connect(sock, address)  # Windows asyncio socketpair only.
    raise AssertionError("Network forbidden in workspace tests")
socket.socket.connect = isolated_connect
socket.socket.connect_ex = lambda *a: (_ for _ in ()).throw(AssertionError("Network forbidden"))

import app as application
import database
import ops
import workspace_router
from workspace_read import VERSION, MAX_ITEMS

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
HEADERS = {"X-Operator-Token": "workspace-fixture-token", "X-RTM-Workspace-Contract": VERSION}


class Patches:
    def __init__(self, stack):
        self.stack = stack
    def setattr(self, target, name, value):
        self.stack.enter_context(patch.object(target, name, value))
    def delenv(self, key):
        env = dict(os.environ)
        env.pop(key, None)
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))


def parameterized(names, values):
    def decorate(fn):
        fn.parameters = (names.split(","), values)
        return fn
    return decorate


@contextmanager
def setup_db(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE cases (id TEXT PRIMARY KEY, status TEXT, payment_status TEXT,
        contact_name TEXT, contact_email TEXT, interested_data TEXT, department TEXT,
        case_type TEXT, customer_comment TEXT, authorized BOOLEAN, created_at TEXT, updated_at TEXT)"""))
        c.execute(text("""CREATE TABLE documents (id TEXT PRIMARY KEY, case_id TEXT, kind TEXT,
        mime TEXT, size_bytes INTEGER, sha256 TEXT, created_at TEXT, b2_bucket TEXT, b2_key TEXT)"""))
        c.execute(text("CREATE TABLE events (id TEXT PRIMARY KEY, case_id TEXT, type TEXT, created_at TEXT, payload TEXT)"))
        c.execute(text("""CREATE TABLE ops_followups (id TEXT PRIMARY KEY, case_id TEXT, kind TEXT,
        status TEXT, title TEXT, description TEXT, due_at TEXT, resolved_at TEXT,
        resolution_note TEXT, created_at TEXT, updated_at TEXT)"""))
        for cid, name in [(A, "Interesado A"), (B, "Interesado B")]:
            c.execute(text("""INSERT INTO cases VALUES (:id, 'uploaded', NULL, :contact,
            'contacto@example.com', :data, 'claims', 'consumer', 'Relato sintético', 0,
            '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00')"""),
            {"id":cid,"contact":"Contacto "+cid[0], "data":json.dumps({"full_name":name, "email":name[-1]+"@example.com",
                     "dni_nie":"SYNTHETIC1234", "private_token":"must-not-leak", "address":{"secret":"not-projected"}})})
            c.execute(text("""INSERT INTO documents VALUES (:id,:case,'identity_front','application/pdf',
            123,:hash,'2026-10-19T00:00:00+00:00','private-bucket','private-key')"""),
                      {"id":str(uuid4()),"case":cid,"hash":"a"*64})
            c.execute(text("INSERT INTO events VALUES (:id,:case,'uploaded','2026-10-19',:payload)"),
                      {"id":str(uuid4()),"case":cid,"payload":json.dumps({"secret":"must-not-leak","snapshot":{"private":"no"}})})
    monkeypatch.setattr(workspace_router, "get_engine", lambda: engine)
    monkeypatch.setattr(ops, "get_engine", lambda: engine)
    original_dispose = engine.dispose
    monkeypatch.setattr(engine, "dispose", lambda: None)
    queries = []
    @event.listens_for(engine, "before_cursor_execute")
    def record(conn,cursor,statement,parameters,context,executemany):
        queries.append(statement)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        yield client, engine, queries
    original_dispose()


def url(cid=A):
    return "/ops/core/cases/"+cid+"/workspace"


def test_route_registered_real_app():
    assert any(r.path == "/ops/core/cases/{case_id}/workspace" and "GET" in r.methods for r in application.app.routes)


def test_real_projection_and_no_writes(setup_db):
    client, engine, queries = setup_db
    with engine.connect() as c:
        before = list(c.execute(text("SELECT * FROM cases"))) + list(c.execute(text("SELECT * FROM documents"))) + list(c.execute(text("SELECT * FROM events")))
    queries.clear()
    response = client.get(url(), headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["workspace_version"] == VERSION
    assert data["case"]["identities"]["interested"]["full_name"]["value"] == "Interesado A"
    assert data["case"]["identities"]["contact"]["full_name"]["value"] == "Contacto 1"
    assert data["case"]["identities"]["interested"]["email"]["value"] == "A@example.com"
    assert data["case"]["fields"]["payment_status"]["status"] == "not_recorded"
    assert data["case"]["fields"]["organismo"]["status"] == "schema_unavailable"
    assert data["case"]["identities"]["driver"]["status"] == "not_supported"
    assert data["sections"]["followups"]["status"] == "empty"
    assert data["legal"]["status"] == "not_supported"
    assert all(v is False for v in data["capabilities"].values())
    assert "private-bucket" not in response.text and "must-not-leak" not in response.text
    assert "payload" not in response.text and "b2_key" not in response.text
    assert all(q.lstrip().upper().startswith(("SELECT", "PRAGMA")) for q in queries)
    assert "no-store" in response.headers["cache-control"]
    with engine.connect() as c:
        after = list(c.execute(text("SELECT * FROM cases"))) + list(c.execute(text("SELECT * FROM documents"))) + list(c.execute(text("SELECT * FROM events")))
    assert before == after


@parameterized("token", [None, "", "wrong"])
def test_auth_before_any_read(setup_db, token):
    client, engine, queries = setup_db
    response = client.get(url("bad-uuid"), headers={} if token is None else {"X-Operator-Token":token})
    assert response.status_code == 401
    assert queries == []
    assert "no-store" in response.headers["cache-control"]


@parameterized("cid,status,code", [
    ("not-a-uuid",422,"WORKSPACE_INVALID_CASE_ID"),
    ("33333333-3333-4333-8333-333333333333",404,"WORKSPACE_CASE_NOT_FOUND"),
    ("x' OR 1=1--",422,"WORKSPACE_INVALID_CASE_ID"),
])
def test_bad_or_missing_case(setup_db,cid,status,code):
    client, engine, queries = setup_db
    response = client.get(url(cid),headers=HEADERS)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "no-store" in response.headers["cache-control"]


def test_case_separation(setup_db):
    client,_,_=setup_db
    a=client.get(url(A),headers=HEADERS).json()
    b=client.get(url(B),headers=HEADERS).json()
    assert a["case"]["identities"]["interested"] != b["case"]["identities"]["interested"]
    for cid,data in [(A,a),(B,b)]:
        assert all(x["case_id"]==cid for x in data["sections"]["documents"]["items"])
    assert {x["id"] for x in a["sections"]["documents"]["items"]}.isdisjoint(
        x["id"] for x in b["sections"]["documents"]["items"])
    if os.getenv("WORKSPACE_FIXTURE_OUTPUT"):
        Path(os.environ["WORKSPACE_FIXTURE_OUTPUT"]).write_text(json.dumps({"a":a,"b":b}), encoding="utf-8")


@parameterized("data,status", [(None,200),("{}",200),("null",500),("[]",500),("not json",500),
                                        ('{"full_name": []}',500),('{"full_name": null}',200)])
def test_identity_integrity_and_no_contact_fallback(setup_db,data,status):
    client,engine,_=setup_db
    with engine.begin() as c:
        c.execute(text("UPDATE cases SET interested_data=:data WHERE id=:id"),{"data":data,"id":A})
    r=client.get(url(),headers=HEADERS)
    assert r.status_code==status
    if status==200:
        assert r.json()["case"]["identities"]["interested"]["full_name"]["status"]=="not_recorded"
        assert r.json()["case"]["identities"]["contact"]["full_name"]["value"]=="Contacto 1"
    else:
        assert r.json()["detail"]["code"]=="WORKSPACE_INTEGRITY_INVALID"


@parameterized("table,section",[("documents","documents"),("events","timeline"),("ops_followups","followups")])
def test_optional_table_absent(setup_db,table,section):
    client,engine,_=setup_db
    with engine.begin() as c: c.execute(text("DROP TABLE "+table))
    r=client.get(url(),headers=HEADERS)
    assert r.status_code==200
    assert r.json()["sections"][section]["status"]=="schema_unavailable"
    assert r.json()["sections"][section]["items"] is None


def test_absent_required_case_schema(setup_db):
    client,engine,_=setup_db
    with engine.begin() as c:c.execute(text("DROP TABLE cases"))
    r=client.get(url(),headers=HEADERS)
    assert r.status_code==503 and r.json()["detail"]["code"]=="WORKSPACE_SCHEMA_UNAVAILABLE"


@parameterized("sqlstate,code",[("42501","WORKSPACE_STORAGE_PERMISSION_DENIED"),("XX000","WORKSPACE_READ_FAILED")])
def test_sql_failure_sanitized(setup_db,monkeypatch,sqlstate,code):
    from sqlalchemy.exc import DBAPIError
    class PrivateError(Exception): pass
    original=PrivateError("secret parameter do-not-log")
    original.sqlstate=sqlstate
    def fail(*a):raise DBAPIError("SELECT secret-statement",{"private":"secret-param"},original)
    monkeypatch.setattr(workspace_router,"build_workspace",fail)
    r=setup_db[0].get(url(),headers=HEADERS)
    assert r.status_code==503
    assert r.json()["detail"]["code"]==code
    assert "secret" not in r.text


@parameterized("column,value", [("size_bytes",-1),("sha256","invalid")])
def test_document_integrity(setup_db,column,value):
    client,engine,_=setup_db
    with engine.begin() as c:c.execute(text("UPDATE documents SET "+column+"=:value WHERE case_id=:id"),{"value":value,"id":A})
    assert client.get(url(),headers=HEADERS).status_code==500


def test_empty_documents_are_distinct(setup_db):
    client,engine,_=setup_db
    with engine.begin() as c:c.execute(text("DELETE FROM documents WHERE case_id=:id"),{"id":A})
    s=client.get(url(),headers=HEADERS).json()["sections"]["documents"]
    assert s["status"]=="empty" and s["items"]==[]


def test_optional_column_missing_is_explicit(setup_db):
    client,engine,_=setup_db
    with engine.begin() as c:c.execute(text("ALTER TABLE documents DROP COLUMN sha256"))
    s=client.get(url(),headers=HEADERS).json()["sections"]["documents"]
    assert s["status"]=="available" and "sha256" in s["missing_columns"]


def test_missing_identity_column_is_not_no_record(setup_db):
    client,engine,_=setup_db
    with engine.begin() as c:c.execute(text("ALTER TABLE cases DROP COLUMN interested_data"))
    r=client.get(url(),headers=HEADERS)
    assert r.status_code==200
    assert r.json()["case"]["identities"]["interested"]["full_name"]["status"]=="schema_unavailable"


def test_collection_limit_is_explicit(setup_db,monkeypatch):
    import workspace_read
    monkeypatch.setattr(workspace_read,"MAX_ITEMS",0)
    s=setup_db[0].get(url(),headers=HEADERS).json()["sections"]["documents"]
    assert s["status"]=="partial" and s["truncated"] is True


def test_original_read_routes_remain_registered_and_guarded(setup_db):
    client,_,_=setup_db
    for suffix in ("documents","events","followups"):
        path="/ops/cases/"+A+"/"+suffix
        assert client.get(path).status_code==401
        assert client.get(path,headers=HEADERS).status_code==200


def test_non_read_methods_not_added(setup_db):
    for method in ("post","put","patch","delete"):
        assert getattr(setup_db[0],method)(url(),headers=HEADERS).status_code==405


def test_auth_config_absent_fail_closed(setup_db,monkeypatch):
    monkeypatch.delenv("OPERATOR_TOKEN")
    r=setup_db[0].get(url(),headers=HEADERS)
    assert r.status_code==503 and not setup_db[2]


@parameterized("contract", [None, "rtm_ops_workspace_v1_0", "unknown"])
def test_legacy_client_cannot_misinterpret_read_subset(setup_db, contract):
    headers={"X-Operator-Token":"workspace-fixture-token"}
    if contract is not None:headers["X-RTM-Workspace-Contract"]=contract
    r=setup_db[0].get(url(),headers=headers)
    assert r.status_code==406 and not setup_db[2]
    assert r.json()["detail"]["code"]=="WORKSPACE_CONTRACT_REQUIRED"
    assert "no-store" in r.headers["cache-control"]

def load_tests(loader, tests, pattern):
    """Build standard unittest cases; every variant gets a fresh SQLite fixture."""
    suite = unittest.TestSuite()
    for name, fn in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(fn):
            continue
        names, values = getattr(fn, "parameters", ([], [()]))
        for index, valueset in enumerate(values):
            vals = valueset if isinstance(valueset, tuple) else (valueset,)
            params = dict(zip(names, vals))
            def run(fn=fn, params=params):
                with ExitStack() as stack:
                    patches = Patches(stack)
                    args = dict(params)
                    signature = pyinspect.signature(fn).parameters
                    if "monkeypatch" in signature:
                        args["monkeypatch"] = patches
                    if "setup_db" in signature:
                        args["setup_db"] = stack.enter_context(setup_db(patches))
                    fn(**args)
            suite.addTest(unittest.FunctionTestCase(run, description=f"{name}[{index}]"))
    return suite
