import os
import json
import secrets
import hashlib
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from fastapi import Response
import io

router = APIRouter(prefix="/partner", tags=["partner"])

MAX_FILES = 5


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _require_admin(x_admin_token: Optional[str]) -> None:
    expected = _env("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return dk.hex()


def _make_token() -> str:
    return secrets.token_urlsafe(32)


def _require_partner_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Falta Authorization")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization inválido (usar Bearer)")
    return parts[1].strip()


def _get_partner_by_token(conn, token: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT id, name, email, active FROM partners WHERE api_token=:t"),
        {"t": token},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token partner inválido")
    if not bool(row[3]):
        raise HTTPException(status_code=403, detail="Partner desactivado")
    return {"id": str(row[0]), "name": row[1], "email": row[2]}


def _event(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
        ),
        {"case_id": case_id, "type": typ, "payload": json.dumps(payload)},
    )


def _build_partner_authorization_template_pdf() -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="Modelo autorización gestorías",
        author="RecurreTuMulta / LA TALAMANQUINA, S.L.",
    )

    styles = getSampleStyleSheet()
    title = styles["Title"]
    normal = styles["BodyText"]
    normal.leading = 15
    section = ParagraphStyle("section", parent=normal, fontSize=11, leading=14, spaceAfter=4)
    small = ParagraphStyle("small", parent=normal, fontSize=9, leading=11)

    content = []
    content.append(Paragraph("REGISTRO DE APODERAMIENTOS · OTORGAMIENTO DE REPRESENTACIÓN", title))
    content.append(Spacer(1, 0.4 * cm))

    content.append(Paragraph("<b>DATOS DEL REPRESENTANTE / AUTORIZADO</b>", section))
    content.append(Paragraph("<b>Nombre o razón social:</b> LA TALAMANQUINA, S.L.", normal))
    content.append(Paragraph("<b>NIF/CIF:</b> B75440115", normal))
    content.append(Paragraph("<b>Domicilio social:</b> Calle Velázquez, 15 – 28001 Madrid (España)", normal))
    content.append(Spacer(1, 0.35 * cm))

    content.append(Paragraph("<b>DATOS DEL REPRESENTADO / CLIENTE</b>", section))
    content.append(Paragraph("<b>Nombre y apellidos o razón social:</b> ________________________________________________", normal))
    content.append(Paragraph("<b>DNI/NIE/CIF:</b> ________________________________________________", normal))
    content.append(Paragraph("<b>Domicilio:</b> ________________________________________________", normal))
    content.append(Spacer(1, 0.35 * cm))

    content.append(Paragraph("<b>TRÁMITE AUTORIZADO</b>", section))
    content.append(Paragraph("Presentación de escritos de alegaciones o recursos ante la DGT y actuaciones administrativas vinculadas al expediente sancionador.", normal))
    content.append(Spacer(1, 0.35 * cm))

    content.append(Paragraph("<b>ALCANCE DE LA REPRESENTACIÓN</b>", section))
    content.append(Paragraph(
        "La persona firmante autoriza expresamente a <b>LA TALAMANQUINA, S.L.</b> para actuar en su nombre "
        "ante la Dirección General de Tráfico y organismos competentes en relación con expedientes sancionadores "
        "de tráfico, incluyendo la preparación y presentación de alegaciones, recursos y la obtención del "
        "justificante oficial de presentación.",
        normal,
    ))
    content.append(Spacer(1, 0.55 * cm))

    content.append(Paragraph("En _______________________, a ______ de __________________ de 20____", normal))
    content.append(Spacer(1, 1.0 * cm))

    content.append(Paragraph("Firma del representante / autorizado:", normal))
    content.append(Spacer(1, 0.3 * cm))

    firma_path = os.path.join(os.path.dirname(__file__), "templates", "firma.png")
    if os.path.exists(firma_path):
        max_width = 4.0 * cm
        max_height = 2.0 * cm

        img = Image(firma_path)
        ratio = min(
            max_width / float(img.imageWidth),
            max_height / float(img.imageHeight),
        )

        img.drawWidth = float(img.imageWidth) * ratio
        img.drawHeight = float(img.imageHeight) * ratio
        img.hAlign = "CENTER"
        content.append(Spacer(1, 0.2 * cm))
        content.append(img)
    else:
        content.append(Paragraph("__________________________________________", normal))

    content.append(Spacer(1, 0.2 * cm))
    content.append(Paragraph("LA TALAMANQUINA, S.L.", small))
    content.append(Spacer(1, 0.7 * cm))

    content.append(Paragraph("Firma del representado / cliente:", normal))
    content.append(Spacer(1, 0.8 * cm))
    content.append(Paragraph("__________________________________________", normal))
    content.append(Paragraph("Nombre: _________________________________", small))
    content.append(Paragraph("DNI/NIE: ________________________________", small))
    content.append(Spacer(1, 0.5 * cm))

    content.append(Paragraph(
        "Documento base para firma manuscrita del cliente. Tras la firma, debe subirse escaneado o fotografiado al expediente.",
        small,
    ))

    doc.build(content)
    return buffer.getvalue()


@router.get("/authorization-template-pdf")
def partner_authorization_template_pdf() -> Response:
    pdf_bytes = _build_partner_authorization_template_pdf()
    headers = {"Content-Disposition": 'attachment; filename="autorizacion_gestoria_recurretumulta.pdf"'}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/cases")
def list_partner_cases(
    authorization: Optional[str] = Header(default=None),
    q: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    token = _require_partner_token(authorization)
    engine = get_engine()

    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, token)

        sql = """
            SELECT
                c.id,
                c.contact_name,
                c.contact_email,
                c.status,
                COALESCE(c.payment_status, 'monthly') AS payment_status,
                c.updated_at,
                (
                    SELECT COUNT(*)
                    FROM documents d
                    WHERE d.case_id = c.id
                ) AS docs_total,
                EXISTS(
                    SELECT 1
                    FROM documents d2
                    WHERE d2.case_id = c.id
                      AND d2.kind = 'authorization_signed'
                ) AS authorization_document_uploaded
            FROM cases c
            WHERE c.partner_id = :pid
        """
        params = {"pid": partner["id"]}

        if (status or "").strip():
            sql += " AND c.status = :status"
            params["status"] = status.strip()

        if (q or "").strip():
            sql += " AND (COALESCE(c.contact_name,'') ILIKE :q OR COALESCE(c.contact_email,'') ILIKE :q OR CAST(c.id AS TEXT) ILIKE :q)"
            params["q"] = f"%{q.strip()}%"

        sql += " ORDER BY c.updated_at DESC"

        rows = conn.execute(text(sql), params).fetchall()

    items = []
    for row in rows:
        items.append({
            "case_id": str(row[0]),
            "client_name": row[1] or "",
            "client_email": row[2] or "",
            "status": row[3] or "uploaded",
            "payment_status": row[4] or "monthly",
            "updated_at": str(row[5]) if row[5] else None,
            "authorization_mode": "partner_custody",
            "authorization_received": bool(row[7]),
            "authorization_document_uploaded": bool(row[7]),
            "docs_total": int(row[6] or 0),
        })

    return {
        "ok": True,
        "partner_name": partner["name"],
        "items": items,
    }


class PartnerCreateIn(BaseModel):
    name: str
    email: EmailStr
    password: str


class PartnerLoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/admin-create")
def admin_create_partner(
    payload: PartnerCreateIn,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    name = payload.name.strip()
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password mínimo 8 caracteres")

    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    token = _make_token()

    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM partners WHERE email=:e"), {"e": email}).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Ya existe un partner con ese email")
        row = conn.execute(
            text("INSERT INTO partners(name, email, password_salt, password_hash, api_token, active, created_at, updated_at) VALUES (:n,:e,:s,:h,:t,TRUE,NOW(),NOW()) RETURNING id"),
            {"n": name, "e": email, "s": salt, "h": pwd_hash, "t": token},
        ).fetchone()
    return {"ok": True, "partner_id": str(row[0]), "token": token}


@router.post("/login")
def partner_login(payload: PartnerLoginIn) -> Dict[str, Any]:
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, name, email, password_salt, password_hash, active FROM partners WHERE email=:e"),
            {"e": email},
        ).fetchone()
        if not row or not bool(row[5]):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
        salt = row[3] or ""
        expected = row[4] or ""
        got = _hash_password(password, salt)
        if got != expected:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
        token = _make_token()
        conn.execute(text("UPDATE partners SET api_token=:t, updated_at=NOW() WHERE id=:id"), {"t": token, "id": row[0]})
    return {"ok": True, "token": token, "partner_name": row[1]}


@router.post("/cases")
async def create_partner_case(
    authorization: Optional[str] = Header(default=None),
    client_email: Optional[EmailStr] = Form(default=None),
    client_name: Optional[str] = Form(default=None),
    interesado_json: Optional[str] = Form(default=None),
    partner_note: Optional[str] = Form(default=None),
    confirm_client_informed: str = Form(...),
    authorization_file: UploadFile = File(...),
    files: List[UploadFile] = File(...),
) -> Dict[str, Any]:
    """
    Entrada asesorías (B2B):
    - Auth: Bearer <partner_api_token>
    - Crea case con channel='partner' y facturación mensual (sin Stripe)
    - Sube hasta 5 docs a B2 y crea documents(kind='original')
    - Requiere autorización firmada del cliente y la guarda como documents(kind='authorization_signed')
    """
    token = _require_partner_token(authorization)

    if (confirm_client_informed or "").strip().lower() not in ("true", "1", "yes", "si", "sí"):
        raise HTTPException(status_code=400, detail="Debe confirmarse cliente informado (confirm_client_informed=true).")
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FILES} documentos por expediente.")
    if not authorization_file:
        raise HTTPException(status_code=400, detail="Falta la autorización firmada del cliente.")

    interesado: Dict[str, Any] = {}
    if interesado_json:
        try:
            interesado = json.loads(interesado_json)
        except Exception:
            raise HTTPException(status_code=400, detail="interesado_json no es JSON válido")

    engine = get_engine()

    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, token)

        row = conn.execute(
            text(
                """
                INSERT INTO cases (
                    contact_email, contact_name,
                    channel, partner_id, partner_name,
                    payment_status, status,
                    interested_data,
                    created_at, updated_at
                )
                VALUES (
                    :ce, :cn,
                    'partner', :pid, :pname,
                    'monthly', 'uploaded',
                    :idata,
                    NOW(), NOW()
                )
                RETURNING id
                """
            ),
            {
                "ce": str(client_email).strip().lower() if client_email else None,
                "cn": (client_name or "").strip() or None,
                "pid": partner["id"],
                "pname": partner["name"],
                "idata": json.dumps(interesado or {}),
            },
        ).fetchone()

        case_id = str(row[0])

        _event(conn, case_id, "partner_case_created", {
            "partner_id": partner["id"],
            "partner_name": partner["name"],
            "client_email": str(client_email).strip().lower() if client_email else None,
            "client_name": (client_name or "").strip() if client_name else None,
            "partner_note": (partner_note or "").strip()[:1000] if partner_note else None,
        })

    auth_data = await authorization_file.read()
    if not auth_data:
        raise HTTPException(status_code=400, detail="La autorización firmada está vacía.")

    auth_filename = (authorization_file.filename or "authorization_signed").replace("/", "_").replace("\\", "_")[:120]
    auth_ext = ".bin"
    if "." in auth_filename:
        auth_ext = "." + auth_filename.split(".")[-1].lower()
        if len(auth_ext) > 8:
            auth_ext = ".bin"

    auth_bucket, auth_key = upload_bytes(
        case_id,
        "authorization_signed",
        auth_data,
        ext=auth_ext,
        content_type=(authorization_file.content_type or "application/octet-stream"),
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:case_id, 'authorization_signed', :b, :k, :m, :s, NOW())
                """
            ),
            {
                "case_id": case_id,
                "b": auth_bucket,
                "k": auth_key,
                "m": authorization_file.content_type or "application/octet-stream",
                "s": len(auth_data),
            },
        )
        _event(conn, case_id, "authorization_uploaded", {
            "source": "partner",
            "filename": auth_filename,
        })

    uploaded = []
    for idx, uf in enumerate(files, start=1):
        data = await uf.read()
        if not data:
            continue

        filename = (uf.filename or f"doc_{idx}").replace("/", "_").replace("\\", "_")[:120]
        ext = ".bin"
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
            if len(ext) > 8:
                ext = ".bin"

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "original",
            data,
            ext=ext,
            content_type=(uf.content_type or "application/octet-stream"),
        )
        uploaded.append({
            "filename": filename,
            "bucket": b2_bucket,
            "key": b2_key,
            "mime": uf.content_type or "application/octet-stream",
            "size_bytes": len(data),
        })

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:case_id, 'original', :b, :k, :m, :s, NOW())
                    """
                ),
                {"case_id": case_id, "b": b2_bucket, "k": b2_key, "m": uf.content_type or "application/octet-stream", "s": len(data)},
            )

    with engine.begin() as conn:
        _event(conn, case_id, "partner_documents_uploaded", {"count": len(uploaded)})

    return {
        "ok": True,
        "case_id": case_id,
        "uploaded": uploaded,
        "authorization_signed": {
            "filename": auth_filename,
            "bucket": auth_bucket,
            "key": auth_key,
            "mime": authorization_file.content_type or "application/octet-stream",
            "size_bytes": len(auth_data),
        },
    }


class PartnerSignupRequest(BaseModel):
    empresa: str
    contacto: str
    email: EmailStr
    telefono: Optional[str] = None
    provincia: Optional[str] = None
    volumen: Optional[str] = None
    mensaje: Optional[str] = None


@router.post("/signup")
def partner_signup(payload: PartnerSignupRequest):
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = "Nueva solicitud de alta asesoría"
        msg["From"] = os.getenv("SMTP_FROM")
        msg["To"] = "soporte@recurretumulta.eu"

        body = f"""
Nueva solicitud de asesoría:

Empresa: {payload.empresa}
Contacto: {payload.contacto}
Email: {payload.email}
Teléfono: {payload.telefono}
Provincia: {payload.provincia}
Volumen: {payload.volumen}

Mensaje:
{payload.mensaje}
        """

        msg.set_content(body)

        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
            server.send_message(msg)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error enviando email: {e}")

    return {"ok": True}
