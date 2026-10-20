"""Allowlisted, non-mutating projection of the deployed intake schema.

No legal evaluation, snapshots, B2 coordinates or document bytes. Missing schema
and unreadable data are never translated into an empty successful collection.
"""
import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import inspect, text

VERSION = "rtm_ops_workspace_read_v1"
CASE_FIELDS = (
    "id", "status", "payment_status", "product_code", "contact_name",
    "contact_email", "department", "case_type", "category", "organismo",
    "expediente_ref", "notified_at", "deadline_main", "created_at", "updated_at",
    "customer_comment", "source_module", "authorized", "interested_data",
)
SECTIONS = {
    "documents": ("documents", ("id", "kind", "mime", "size_bytes", "sha256", "created_at")),
    "timeline": ("events", ("id", "type", "created_at")),
    "followups": ("ops_followups", ("id", "kind", "status", "title", "description",
                                   "due_at", "resolved_at", "created_at", "updated_at")),
}
MAX_ITEMS = 1000


class InvalidWorkspace(ValueError):
    pass


class MissingCase(Exception):
    pass


class MissingSchema(Exception):
    pass


def scalar(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, (date, datetime, UUID)):
        return str(value)
    if isinstance(value, str) and len(value) <= 65536:
        return value
    raise InvalidWorkspace("Unsupported persisted field type")


def field(value=None, source=None, status=None):
    value = scalar(value)
    return {"value": value, "source": source,
            "status": status or ("not_recorded" if value is None or value == "" else "available")}


def mapping(value):
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise InvalidWorkspace("Invalid interested_data") from None
    if not isinstance(value, dict):
        raise InvalidWorkspace("Invalid interested_data")
    return value


def identity(data, schema_available):
    aliases = {"full_name": ("full_name", "name"),
               "dni_nie": ("dni_nie", "dni", "identity_number"),
               "address": ("domicilio_notif", "domicilio"),
               "email": ("email",), "phone": ("telefono", "phone")}
    result = {}
    for name, keys in aliases.items():
        if not schema_available:
            result[name] = field(status="schema_unavailable")
            continue
        key = next((k for k in keys if data.get(k) not in (None, "")), keys[0])
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise InvalidWorkspace("Invalid identity field")
        result[name] = field(value, "cases.interested_data." + key)
    return result


def qualified(conn, table):
    # All names originate in constants, never request input.
    return "public." + table if conn.dialect.name == "postgresql" else table


def columns(conn, inspector, table):
    schema = "public" if conn.dialect.name == "postgresql" else None
    if not inspector.has_table(table, schema=schema):
        return None
    return {c["name"] for c in inspector.get_columns(table, schema=schema)}


def collection(conn, inspector, table, allowed, case_id):
    available = columns(conn, inspector, table)
    required = {"id", "case_id", "kind" if table != "events" else "type"}
    if available is None or not required.issubset(available):
        return {"status": "schema_unavailable", "items": None,
                "missing_columns": sorted(required - (available or set()))}
    selected = [c for c in allowed if c in available]
    rows = conn.execute(text(
        "SELECT " + ", ".join(selected) + " FROM " + qualified(conn, table)
        + " WHERE case_id = :case_id ORDER BY id LIMIT :limit"
    ), {"case_id": case_id, "limit": MAX_ITEMS + 1}).mappings().all()
    result = []
    for row in rows[:MAX_ITEMS]:
        item = {k: scalar(v) for k, v in row.items()}
        try:
            item["id"] = str(UUID(str(item["id"])))
        except (ValueError, TypeError):
            raise InvalidWorkspace("Invalid document/event identifier") from None
        if table == "documents":
            size = item.get("size_bytes")
            if size is not None and (type(size) is not int or size < 0):
                raise InvalidWorkspace("Invalid document size")
            digest = item.get("sha256")
            if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                      or any(c not in "0123456789abcdefABCDEF" for c in digest)):
                raise InvalidWorkspace("Invalid document digest")
        item["case_id"] = case_id
        result.append(item)
    return {"status": "partial" if len(rows) > MAX_ITEMS else ("available" if rows else "empty"),
            "items": result, "missing_columns": sorted(set(allowed) - available),
            "truncated": len(rows) > MAX_ITEMS}


def build_workspace(conn, case_id):
    inspector = inspect(conn)
    available = columns(conn, inspector, "cases")
    if available is None or "id" not in available:
        raise MissingSchema()
    selected = [c for c in CASE_FIELDS if c in available]
    row = conn.execute(text(
        "SELECT " + ", ".join(selected) + " FROM " + qualified(conn, "cases")
        + " WHERE id = :case_id"
    ), {"case_id": case_id}).mappings().one_or_none()
    if row is None:
        raise MissingCase()
    if str(row["id"]) != case_id:
        raise InvalidWorkspace("Case identity mismatch")
    interested = mapping(row.get("interested_data"))
    fields = {k: field(row.get(k), "cases." + k,
                       "schema_unavailable" if k not in available else None)
              for k in CASE_FIELDS if k not in ("interested_data", "contact_name", "contact_email")}
    authorized = row.get("authorized")
    if authorized is not None and authorized not in (True, False, 0, 1):
        raise InvalidWorkspace("Invalid authorization value")
    # SQLite fixture booleans may be 0/1; production PostgreSQL returns bool.
    if authorized is not None:
        fields["authorized"]["value"] = bool(authorized)
    sections = {name: collection(conn, inspector, table, cols, case_id)
                for name, (table, cols) in SECTIONS.items()}
    return {
        "ok": True, "workspace_version": VERSION, "case_id": case_id,
        "access_model": "production_ops_token", "read_only": True,
        "case": {"fields": fields,
                 "identities": {
                     "interested": identity(interested, "interested_data" in available),
                     "contact": {
                         "full_name": field(row.get("contact_name"), "cases.contact_name",
                                            "schema_unavailable" if "contact_name" not in available else None),
                         "email": field(row.get("contact_email"), "cases.contact_email",
                                        "schema_unavailable" if "contact_email" not in available else None)},
                     "driver": {"status": "not_supported"},
                     "owner": {"status": "not_supported"},
                     "representative": {"status": "not_supported"}}},
        "sections": sections,
        "legal": {"status": "not_supported", "detail": "Lectura de datos guardados; evaluación jurídica no disponible."},
        "capabilities": {"mutations": False, "document_export": False, "legal_actions": False},
    }
