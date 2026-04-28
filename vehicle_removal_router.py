from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from database import get_engine
from openai_vision import extract_from_image_bytes
from text_extractors import extract_text_from_pdf_bytes, has_enough_text
import os
import json
import re
import hashlib
import stripe

router = APIRouter(prefix="/vehicle-removal", tags=["vehicle-removal"])


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip") or ""
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host or ""
    return ""


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _normalize_text(value: str) -> str:
    value = (value or "").lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
    }
    for a, b in replacements.items():
        value = value.replace(a, b)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _extract_plate_candidates(raw_text: str) -> list[str]:
    text = (raw_text or "").upper()
    patterns = [
        r"\b\d{4}\s*[-/]?\s*[A-Z]{3}\b",
        r"\b\d{4}\s*[-/]?\s*[A-Z]\s*[-/]?\s*[A-Z]\s*[-/]?\s*[A-Z]\b",
    ]

    candidates = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            plate = _normalize_plate(match)
            if re.fullmatch(r"\d{4}[A-Z]{3}", plate):
                candidates.append(plate)

    compact = _normalize_plate(text)
    for match in re.findall(r"\d{4}[A-Z]{3}", compact):
        candidates.append(match)

    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _normalize_dni(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _name_tokens(full_name: str) -> list[str]:
    clean = _normalize_text(full_name)
    return [t for t in clean.split() if len(t) >= 3]


def _count_name_matches(full_name: str, text: str) -> int:
    normalized_text = _normalize_text(text)
    return sum(1 for token in _name_tokens(full_name) if token in normalized_text)


def _extract_text_from_payload(payload) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


class VehicleRemovalRequest(BaseModel):
    name: str
    full_name: str | None = None
    dni_nie: str
    phone: str
    email: EmailStr
    plate: str
    city: str
    notes: str | None = None
    authorization_accepted: bool = False
    authorization_version: str | None = "vehicle-removal-v1"


@router.get("/health")
def vehicle_removal_health():
    return {"ok": True, "service": "vehicle_removal"}


@router.post("/verify-registration")
async def verify_registration(
    file: UploadFile = File(...),
    full_name: str = Form(...),
    dni_nie: str = Form(...),
    plate: str = Form(...),
):
    """
    Verifica el permiso de circulación antes del pago.
    - Matrícula: debe coincidir.
    - Nombre/apellidos: debe coincidir razonablemente.
    - DNI/NIE: se recoge para expediente, pero no se compara con el permiso.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 12MB).")

        mime = file.content_type or "application/octet-stream"
        filename = file.filename or "permiso-circulacion"
        sha256 = _sha256_bytes(content)

        form_full_name = (full_name or "").strip()
        form_dni = _normalize_dni(dni_nie)
        form_plate = _normalize_plate(plate)

        if not form_full_name:
            raise HTTPException(status_code=400, detail="Nombre completo requerido.")
        if not form_dni:
            raise HTTPException(status_code=400, detail="DNI/NIE requerido.")
        if not form_plate:
            raise HTTPException(status_code=400, detail="Matrícula requerida.")

        extracted_payload = {}
        raw_text = ""

        if mime == "application/pdf":
            try:
                pdf_text = extract_text_from_pdf_bytes(content) or ""
            except Exception:
                pdf_text = ""

            if has_enough_text(pdf_text):
                raw_text = pdf_text
                extracted_payload = {"raw_text_pdf": pdf_text}
            else:
                extracted_payload = extract_from_image_bytes(content, mime, filename) or {}
                raw_text = _extract_text_from_payload(extracted_payload)

        elif mime.startswith("image/"):
            extracted_payload = extract_from_image_bytes(content, mime, filename) or {}
            raw_text = _extract_text_from_payload(extracted_payload)
        else:
            raise HTTPException(status_code=400, detail="Formato no soportado. Sube imagen o PDF.")

        plate_candidates = _extract_plate_candidates(raw_text)
        plate_match = form_plate in plate_candidates

        name_matches = _count_name_matches(form_full_name, raw_text)
        name_token_total = max(1, len(_name_tokens(form_full_name)))
        name_match = name_matches >= min(2, name_token_total)

        can_continue = bool(plate_match and name_match)
        review_required = False
        reasons = []

        if not plate_match:
            reasons.append("matricula_no_coincide")
        if not name_match:
            reasons.append("titular_no_coincide")
        if not raw_text.strip():
            reasons.append("texto_no_extraido")
            review_required = True
        if plate_match and not name_match:
            review_required = True

        return {
            "ok": True,
            "can_continue": can_continue,
            "match": can_continue,
            "review_required": review_required,
            "reasons": reasons,
            "checks": {
                "plate_match": plate_match,
                "plate_candidates": plate_candidates,
                "dni_collected": bool(form_dni),
                "dni_checked_against_document": False,
                "name_match": name_match,
                "name_matches": name_matches,
                "name_token_total": name_token_total,
            },
            "form": {
                "full_name": form_full_name,
                "dni_nie": form_dni,
                "plate": form_plate,
            },
            "extracted": extracted_payload,
            "raw_text_preview": raw_text[:1200],
            "sha256": sha256,
            "filename": filename,
            "mime": mime,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verificando permiso de circulación: {e}")


@router.post("/create-checkout-session")
def create_checkout_session(data: VehicleRemovalRequest, request: Request):
    try:
        if not data.authorization_accepted:
            raise HTTPException(status_code=400, detail="Debes aceptar la autorización para continuar.")

        stripe.api_key = _env("STRIPE_SECRET_KEY")
        price_id = _env("STRIPE_PRICE_ID_ELIMINAR_COCHE")

        engine = get_engine()

        full_name = (data.full_name or data.name or "").strip()
        dni_nie = _normalize_dni(data.dni_nie or "")
        phone_clean = data.phone.strip()
        plate_clean = _normalize_plate(data.plate)
        email_clean = str(data.email).strip()
        ip = _client_ip(request)
        user_agent = request.headers.get("user-agent", "")

        if not full_name:
            raise HTTPException(status_code=400, detail="Nombre completo del titular requerido")
        if not dni_nie:
            raise HTTPException(status_code=400, detail="DNI/NIE del titular requerido")
        if not phone_clean:
            raise HTTPException(status_code=400, detail="Teléfono requerido")
        if not email_clean:
            raise HTTPException(status_code=400, detail="Email requerido")
        if not plate_clean:
            raise HTTPException(status_code=400, detail="Matrícula requerida")

        authorization_snapshot = {
            "accepted": True,
            "version": data.authorization_version or "vehicle-removal-v1",
            "text": (
                "Autorizo a RecurreTuMulta a revisar la documentación aportada y a iniciar "
                "las gestiones necesarias para la baja/retirada del vehículo indicado, incluyendo "
                "el contacto con centros autorizados o colaboradores necesarios para la gestión. "
                "Declaro que los datos facilitados son ciertos y que soy titular o actúo con autorización del titular."
            ),
            "ip": ip,
            "user_agent": user_agent,
        }

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO cases (
                        status,
                        payment_status,
                        product_code,
                        contact_email,
                        category,
                        interested_data,
                        authorized,
                        authorized_at,
                        authorization_version,
                        authorization_ip,
                        authorization_user_agent,
                        authorization_full_name,
                        authorization_dni_nie,
                        authorization_email,
                        authorization_phone,
                        authorization_checks,
                        authorization_snapshot,
                        updated_at
                    )
                    VALUES (
                        'vehicle_removal_pending_payment',
                        'pending',
                        'ELIMINAR_COCHE',
                        :email,
                        'vehicle_removal',
                        CAST(:interested_data AS JSONB),
                        TRUE,
                        NOW(),
                        :authorization_version,
                        :authorization_ip,
                        :authorization_user_agent,
                        :authorization_full_name,
                        :authorization_dni_nie,
                        :authorization_email,
                        :authorization_phone,
                        CAST(:authorization_checks AS JSONB),
                        CAST(:authorization_snapshot AS JSONB),
                        NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "email": email_clean,
                    "interested_data": json.dumps(
                        {
                            "full_name": full_name,
                            "dni_nie": dni_nie,
                            "telefono": phone_clean,
                            "email": email_clean,
                        },
                        ensure_ascii=False,
                    ),
                    "authorization_version": authorization_snapshot["version"],
                    "authorization_ip": ip,
                    "authorization_user_agent": user_agent,
                    "authorization_full_name": full_name,
                    "authorization_dni_nie": dni_nie,
                    "authorization_email": email_clean,
                    "authorization_phone": phone_clean,
                    "authorization_checks": json.dumps(
                        {
                            "vehicle_removal_authorization": True,
                            "data_truthfulness": True,
                            "titular_or_authorized": True,
                        },
                        ensure_ascii=False,
                    ),
                    "authorization_snapshot": json.dumps(authorization_snapshot, ensure_ascii=False),
                },
            ).fetchone()

            if not row:
                raise RuntimeError("No se pudo crear el expediente de eliminación de vehículo")

            case_id = str(row[0])

            payload = {
                "name": full_name,
                "full_name": full_name,
                "dni_nie": dni_nie,
                "phone": phone_clean,
                "email": email_clean,
                "plate": plate_clean,
                "city": data.city.strip(),
                "notes": (data.notes or "").strip() or None,
                "service": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "status": "pending_payment",
                "stripe_price_id": price_id,
                "authorization": authorization_snapshot,
            }

            conn.execute(
                text(
                    """
                    INSERT INTO events (case_id, type, payload, created_at)
                    VALUES (:case_id, 'vehicle_removal_request_created', CAST(:payload AS JSONB), NOW())
                    """
                ),
                {
                    "case_id": case_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO events (case_id, type, payload, created_at)
                    VALUES (:case_id, 'vehicle_removal_authorization_accepted', CAST(:payload AS JSONB), NOW())
                    """
                ),
                {
                    "case_id": case_id,
                    "payload": json.dumps(authorization_snapshot, ensure_ascii=False),
                },
            )

        frontend_url = (
            os.getenv("FRONTEND_URL")
            or os.getenv("FRONTEND_BASE_URL")
            or "https://recurretumulta.vercel.app"
        ).rstrip("/")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            customer_email=email_clean,
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={
                "case_id": case_id,
                "service": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "plate": plate_clean,
                "city": data.city.strip(),
                "phone": phone_clean,
                "dni_nie": dni_nie,
                "email": email_clean,
                "full_name": full_name,
            },
            success_url=f"{frontend_url}/#/eliminar-coche?success=1&case_id={case_id}",
            cancel_url=f"{frontend_url}/#/eliminar-coche?cancelled=1&case_id={case_id}",
        )

        return {"ok": True, "case_id": case_id, "checkout_url": session.url}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando sesión de eliminación de vehículo: {e}")
