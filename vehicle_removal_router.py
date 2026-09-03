from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from database import get_engine
from openai_vision import extract_from_image_bytes
from text_extractors import extract_text_from_pdf_bytes, has_enough_text
import os
import json
import re
import hashlib
import stripe
from urllib.parse import urlsplit

from public_case_access import require_case_access_token
from rtm_core.ai_security import ModelCallBudgetExceeded, model_call_budget
from rtm_core.case_state_policy import (
    require_public_material_mutation_payment_status,
    require_public_material_mutation_status,
)
from rtm_core.parser_isolation import ParserIsolationError
from rtm_core.runtime_capabilities import require_http_capability
from rtm_core.trusted_origins import trusted_frontend_origin
from rtm_core.upload_security import (
    SAFE_IMAGE_OR_PDF_MIMES,
    UploadSecurityError,
    read_upload_limited,
    validate_document_bytes,
)
from rtm_core.vehicle_removal_contract import (
    VEHICLE_REMOVAL_AMOUNT_CENTS as _VEHICLE_REMOVAL_AMOUNT_CENTS,
    VEHICLE_REMOVAL_AUTHORIZATION_SHA256 as _VEHICLE_REMOVAL_AUTHORIZATION_SHA256,
    VEHICLE_REMOVAL_AUTHORIZATION_TEXT as _VEHICLE_REMOVAL_AUTHORIZATION_TEXT,
    VEHICLE_REMOVAL_AUTHORIZATION_VERSION as _VEHICLE_REMOVAL_AUTHORIZATION_VERSION,
    VEHICLE_REMOVAL_CHECKOUT_CONTRACT as _VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
    VEHICLE_REMOVAL_CURRENCY as _VEHICLE_REMOVAL_CURRENCY,
    VEHICLE_REMOVAL_METADATA_KEYS as _SAFE_STRIPE_METADATA_KEYS,
    VEHICLE_REMOVAL_PRODUCT_CODE as _VEHICLE_REMOVAL_PRODUCT_CODE,
    VEHICLE_REMOVAL_QUOTE_VERSION as _VEHICLE_REMOVAL_QUOTE_VERSION,
    VEHICLE_REMOVAL_REQUEST_CONTRACT as _VEHICLE_REMOVAL_REQUEST_CONTRACT,
    VEHICLE_REMOVAL_SERVICE_CODE as _VEHICLE_REMOVAL_SERVICE_CODE,
    build_vehicle_removal_quote,
    build_vehicle_removal_preparation_consent,
    build_vehicle_removal_stripe_metadata,
    is_exact_vehicle_removal_stripe_metadata,
    vehicle_removal_authorization_is_exact,
)

router = APIRouter(prefix="/vehicle-removal", tags=["vehicle-removal"])
MAX_REGISTRATION_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 50_000
_CASE_ID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_MAX_REGISTRATION_MODEL_CALLS = 1


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


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

    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _extract_registration_candidate(
    content: bytes,
    mime: str,
    filename: str,
) -> dict:
    """OCR no autoritativo con una sola llamada externa por petición."""

    with model_call_budget(_MAX_REGISTRATION_MODEL_CALLS):
        return extract_from_image_bytes(content, mime, filename) or {}


def _normalize_dni(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _name_tokens(full_name: str) -> list[str]:
    clean = _normalize_text(full_name)
    return [t for t in clean.split() if len(t) >= 3]


def _count_name_matches(full_name: str, text: str) -> int:
    document_tokens = set(_normalize_text(text).split())
    return sum(1 for token in _name_tokens(full_name) if token in document_tokens)


def _extract_text_from_payload(payload) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload[:MAX_EXTRACTED_TEXT_CHARS]
    try:
        return json.dumps(payload, ensure_ascii=False)[:MAX_EXTRACTED_TEXT_CHARS]
    except Exception:
        return str(payload)[:MAX_EXTRACTED_TEXT_CHARS]


def _without_forbidden_controls(value: str, *, multiline: bool = False) -> str:
    candidate = str(value or "").strip()
    allowed = {"\r", "\n", "\t"} if multiline else set()
    if any(
        (ord(character) < 32 or ord(character) == 127) and character not in allowed
        for character in candidate
    ):
        raise HTTPException(status_code=422, detail="Datos de solicitud no válidos")
    return candidate


def _decode_payload(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _row_value(row, index: int, key: str):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _load_vehicle_case(conn, case_id: str, *, for_update: bool = False) -> dict:
    lock_clause = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            """
            SELECT id, status, payment_status, stripe_session_id,
                   contact_email, contact_name,
                   COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                   COALESCE(department, '') AS department,
                   COALESCE(case_type, '') AS case_type,
                   COALESCE(category, '') AS category
            FROM cases
            WHERE id=:case_id
            """
            + lock_clause
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    interested = _decode_payload(_row_value(row, 6, "interested_data"))
    department = str(_row_value(row, 7, "department") or "").strip().lower()
    case_type = str(_row_value(row, 8, "case_type") or "").strip().lower()
    category = str(_row_value(row, 9, "category") or "").strip().lower()
    if case_type != "vehicle_removal" or department != "traffic":
        raise HTTPException(
            status_code=409,
            detail="El expediente no corresponde a retirada de vehículo",
        )
    if category not in {"traffic", "vehicle_removal"}:
        raise HTTPException(
            status_code=409,
            detail="El expediente no corresponde a retirada de vehículo",
        )

    return {
        "id": str(_row_value(row, 0, "id")),
        "status": str(_row_value(row, 1, "status") or ""),
        "payment_status": str(_row_value(row, 2, "payment_status") or ""),
        "stripe_session_id": str(
            _row_value(row, 3, "stripe_session_id") or ""
        ).strip(),
        "contact_email": str(_row_value(row, 4, "contact_email") or "").strip(),
        "contact_name": str(_row_value(row, 5, "contact_name") or "").strip(),
        "interested_data": interested,
    }


def _persisted_identity(case: dict) -> dict:
    interested = case["interested_data"]
    return {
        "full_name": str(
            interested.get("full_name") or case.get("contact_name") or ""
        ).strip(),
        "dni_nie": _normalize_dni(
            str(interested.get("dni_nie") or interested.get("dni") or "")
        ),
        "phone": str(
            interested.get("telefono") or interested.get("phone") or ""
        ).strip(),
        "email": str(
            interested.get("email") or case.get("contact_email") or ""
        ).strip().lower(),
        "plate": _normalize_plate(
            str(interested.get("matricula") or interested.get("plate") or "")
        ),
    }


def _require_persisted_identity(identity: dict) -> None:
    """La identidad autoritativa procede del expediente, nunca del checkout."""

    if (
        not identity["full_name"]
        or not identity["dni_nie"]
        or not identity["email"]
    ):
        raise HTTPException(
            status_code=409,
            detail="El expediente no contiene una identidad completa",
        )


def _stripe_value(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    getter = getattr(value, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(value, key, default)


def _safe_stripe_metadata(case_id: str) -> dict[str, str]:
    return build_vehicle_removal_stripe_metadata(case_id)


def _validated_checkout_session(
    session, expected_metadata: dict
) -> tuple[str, str, str, int, str]:
    session_id = str(_stripe_value(session, "id") or "").strip()
    checkout_url = str(_stripe_value(session, "url") or "").strip()
    status = str(_stripe_value(session, "status") or "").strip().lower()
    currency = str(_stripe_value(session, "currency") or "").strip().upper()
    try:
        amount_total = int(_stripe_value(session, "amount_total"))
    except (TypeError, ValueError):
        amount_total = 0
    metadata = _decode_payload(_stripe_value(session, "metadata") or {})
    parsed_url = urlsplit(checkout_url)
    if (
        not session_id.startswith("cs_")
        or parsed_url.scheme != "https"
        or (parsed_url.hostname or "").lower() != "checkout.stripe.com"
        or not parsed_url.path.startswith("/")
        or metadata != expected_metadata
        or not is_exact_vehicle_removal_stripe_metadata(metadata)
        or status not in {"open", "complete", "expired"}
        or amount_total != _VEHICLE_REMOVAL_AMOUNT_CENTS
        or currency != _VEHICLE_REMOVAL_CURRENCY
    ):
        raise HTTPException(
            status_code=502,
            detail="El proveedor de pago no devolvió una sesión válida",
        )
    return session_id, checkout_url, status, amount_total, currency


def _expire_checkout_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        stripe.checkout.Session.expire(session_id)
    except Exception:
        # Compensación best effort: el idempotency key permite recuperar y
        # vincular la misma sesión si la confirmación local falló.
        pass


class VehicleRemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(pattern=_CASE_ID_PATTERN)
    plate: str = Field(min_length=1, max_length=16)
    city: str = Field(default="", max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    authorization_accepted: bool = False
    authorization_version: str = Field(min_length=1, max_length=80)
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_safe_fields(self):
        for value, multiline in (
            (self.plate, False),
            (self.city, False),
            (self.notes or "", True),
            (self.authorization_version, False),
            (self.authorization_sha256, False),
        ):
            try:
                _without_forbidden_controls(value, multiline=multiline)
            except HTTPException as exc:
                raise ValueError("La solicitud contiene caracteres no válidos") from exc
        if not vehicle_removal_authorization_is_exact(
            self.authorization_version,
            self.authorization_sha256,
        ):
            raise ValueError("El contrato de autorización no es válido")
        return self


@router.get("/health")
def vehicle_removal_health():
    return {"ok": True, "service": "vehicle_removal"}


@router.get("/quote")
def vehicle_removal_quote(
    response: Response,
    case_id: str = Query(
        ...,
        min_length=36,
        max_length=36,
        pattern=_CASE_ID_PATTERN,
    ),
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Tarifa pública autoritativa, ligada al expediente y sin price ID ni PII."""

    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        _load_vehicle_case(conn, case_id)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return build_vehicle_removal_quote(case_id)


@router.post("/verify-registration")
async def verify_registration(
    file: UploadFile = File(...),
    case_id: str = Form(..., min_length=36, max_length=36, pattern=_CASE_ID_PATTERN),
    plate: str = Form(..., min_length=1, max_length=16),
    ai_processing_consent: bool = Form(False),
    privacy_version: str = Form("", max_length=80),
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
):
    """
    Verifica el permiso de circulación antes del pago.
    - Matrícula: debe coincidir.
    - Nombre/apellidos: se contrasta con la identidad ya guardada.
    - Los datos nominales no vuelven a viajar desde el navegador.
    """
    try:
        case_id = require_case_access_token(case_id, x_case_token)
        if not ai_processing_consent or privacy_version != "vehicle-removal-ai-v1":
            raise HTTPException(
                status_code=409,
                detail="Falta el consentimiento documental vigente",
            )

        form_plate = _normalize_plate(_without_forbidden_controls(plate))
        if not re.fullmatch(r"\d{4}[A-Z]{3}", form_plate):
            raise HTTPException(status_code=422, detail="Datos de verificación no válidos")

        engine = get_engine()
        with engine.begin() as conn:
            case = _load_vehicle_case(conn, case_id)
            require_public_material_mutation_status(case["status"])
            require_public_material_mutation_payment_status(
                case["payment_status"]
            )
        identity = _persisted_identity(case)
        _require_persisted_identity(identity)
        if identity["plate"] and form_plate != identity["plate"]:
            raise HTTPException(
                status_code=409,
                detail="Los datos no coinciden con el expediente",
            )

        try:
            content = await read_upload_limited(file, max_bytes=MAX_REGISTRATION_BYTES)
            upload = await run_in_threadpool(
                validate_document_bytes,
                filename=file.filename or "permiso-circulacion.pdf",
                declared_mime=file.content_type,
                data=content,
                max_bytes=MAX_REGISTRATION_BYTES,
                allowed_mimes=SAFE_IMAGE_OR_PDF_MIMES,
            )
        except UploadSecurityError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail="Documento rechazado por la política de seguridad",
            ) from exc

        mime = upload.mime
        filename = upload.filename

        extracted_payload = {}
        raw_text = ""
        ai_assisted = False

        if mime == "application/pdf":
            try:
                pdf_text = (await run_in_threadpool(
                    extract_text_from_pdf_bytes, content
                ) or "")[
                    :MAX_EXTRACTED_TEXT_CHARS
                ]
            except ParserIsolationError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="El lector documental seguro no está disponible",
                ) from exc
            except UploadSecurityError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="El documento no ha superado la lectura de seguridad",
                ) from exc
            except Exception:
                pdf_text = ""

            if has_enough_text(pdf_text):
                raw_text = pdf_text
            else:
                require_http_capability("document_provider")
                extracted_payload = await run_in_threadpool(
                    _extract_registration_candidate, content, mime, filename
                )
                raw_text = _extract_text_from_payload(extracted_payload)
                ai_assisted = True

        elif mime.startswith("image/"):
            require_http_capability("document_provider")
            extracted_payload = await run_in_threadpool(
                _extract_registration_candidate, content, mime, filename
            )
            raw_text = _extract_text_from_payload(extracted_payload)
            ai_assisted = True
        else:
            raise HTTPException(status_code=400, detail="Formato documental no soportado")

        plate_candidates = _extract_plate_candidates(raw_text)
        plate_match = form_plate in plate_candidates

        name_matches = _count_name_matches(identity["full_name"], raw_text)
        name_token_total = max(1, len(_name_tokens(identity["full_name"])))
        name_match = name_matches >= min(2, name_token_total)

        deterministic_match = bool(plate_match and name_match)
        # La salida de un modelo nunca constituye autoridad. Cuando intervino
        # OCR externo, el resultado solo es una pista y exige revisión humana.
        review_required = ai_assisted
        can_continue = bool(deterministic_match and not ai_assisted)
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
        if ai_assisted:
            reasons.append("revision_humana_obligatoria")

        if can_continue:
            # La matrícula solo se vuelve autoritativa tras la comprobación
            # determinista del permiso. Checkout deberá usar exactamente esta.
            with engine.begin() as conn:
                locked_case = _load_vehicle_case(conn, case_id, for_update=True)
                require_public_material_mutation_status(locked_case["status"])
                require_public_material_mutation_payment_status(
                    locked_case["payment_status"]
                )
                locked_identity = _persisted_identity(locked_case)
                _require_persisted_identity(locked_identity)
                if (
                    _normalize_text(locked_identity["full_name"])
                    != _normalize_text(identity["full_name"])
                    or locked_identity["dni_nie"] != identity["dni_nie"]
                    or locked_identity["email"] != identity["email"]
                    or (
                        locked_identity["plate"]
                        and locked_identity["plate"] != form_plate
                    )
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="El expediente cambió durante la verificación",
                    )
                conn.execute(
                    text(
                        """
                        UPDATE cases
                        SET interested_data=jsonb_set(
                                jsonb_set(
                                    COALESCE(interested_data, '{}'::jsonb),
                                    '{matricula}',
                                    to_jsonb(CAST(:plate AS text)),
                                    TRUE
                                ),
                                '{matricula_verification_status}',
                                to_jsonb(CAST('deterministic_verified' AS text)),
                                TRUE
                            ),
                            updated_at=NOW()
                        WHERE id=:case_id
                        """
                    ),
                    {"case_id": case_id, "plate": form_plate},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO events(case_id, type, payload, created_at)
                        VALUES (
                            :case_id,
                            'vehicle_registration_verified',
                            CAST(:payload AS JSONB),
                            NOW()
                        )
                        """
                    ),
                    {
                        "case_id": case_id,
                        "payload": json.dumps(
                            {
                                "verification_version": "rtm_vehicle_registration_v2",
                                "document_sha256": str(
                                    getattr(upload, "sha256", "")
                                    or hashlib.sha256(content).hexdigest()
                                ),
                                "match_method": "deterministic_text",
                            },
                            ensure_ascii=False,
                        ),
                    },
                )

        return {
            "ok": True,
            "can_continue": can_continue,
            "match": can_continue,
            "review_required": review_required,
            "reasons": reasons,
            "checks": {
                "plate_match": plate_match,
                "name_match": name_match,
                "ai_assisted": ai_assisted,
            },
            "verification_version": "rtm_vehicle_registration_v2",
        }

    except HTTPException:
        raise
    except ModelCallBudgetExceeded as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo completar el análisis documental de forma segura",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo verificar el documento",
        ) from exc


@router.post("/create-checkout-session")
def create_checkout_session(
    data: VehicleRemovalRequest,
    request: Request,
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(data.case_id, x_case_token)
    del request
    if (
        not data.authorization_accepted
        or not vehicle_removal_authorization_is_exact(
            data.authorization_version,
            data.authorization_sha256,
        )
    ):
        raise HTTPException(status_code=400, detail="Falta la autorización obligatoria")

    # Los interruptores se comprueban después de autenticar el expediente y
    # antes de tocar claves, base de datos o el proveedor externo.
    require_http_capability("stripe")
    require_http_capability("final_payments")

    created_session_id = ""
    transaction_committed = False
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")
        price_id = _env("STRIPE_PRICE_ID_ELIMINAR_COCHE")
        engine = get_engine()
        plate_clean = _normalize_plate(data.plate)
        city_clean = _without_forbidden_controls(data.city)
        notes_clean = _without_forbidden_controls(data.notes or "", multiline=True)
        if not re.fullmatch(r"\d{4}[A-Z]{3}", plate_clean):
            raise HTTPException(status_code=422, detail="Matrícula no válida")
        frontend_url = trusted_frontend_origin()

        expected_metadata = _safe_stripe_metadata(case_id)
        with engine.begin() as conn:
            case = _load_vehicle_case(conn, case_id, for_update=True)
            expected_case_status = str(case["status"] or "").strip().lower()
            expected_payment_status = str(
                case["payment_status"] or ""
            ).strip().lower()
            expected_session_id = str(case["stripe_session_id"] or "").strip()
            identity = _persisted_identity(case)
            _require_persisted_identity(identity)
            if identity["plate"] and identity["plate"] != plate_clean:
                raise HTTPException(
                    status_code=409,
                    detail="La matrícula no coincide con el expediente",
                )
            raw_plate_status = str(
                case["interested_data"].get("matricula_verification_status") or ""
            ).strip()
            plate_verification_status = (
                "deterministic_verified"
                if identity["plate"]
                and raw_plate_status == "deterministic_verified"
                else "declared"
            )
            if expected_payment_status == "paid" or expected_case_status in {
                "vehicle_removal_paid",
                "vehicle_removal_assigned",
                "vehicle_removal_completed",
            }:
                raise HTTPException(status_code=409, detail="El expediente ya consta pagado")

            previous_session_id = expected_session_id
            resuming_pending_checkout = (
                expected_payment_status == "pending"
                and expected_case_status == "vehicle_removal_pending_payment"
                and bool(previous_session_id)
            )
            if not resuming_pending_checkout:
                # Cualquier terminal/procesamiento/pago ajeno queda congelado.
                # La única excepción es reutilizar o reemplazar una sesión
                # propia ya verificada y no cobrable (expired).
                require_public_material_mutation_status(expected_case_status)
                require_public_material_mutation_payment_status(
                    expected_payment_status
                )
                if previous_session_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Existe una sesión previa que requiere conciliación",
                    )

            if resuming_pending_checkout:
                existing = stripe.checkout.Session.retrieve(previous_session_id)
                (
                    _,
                    existing_url,
                    existing_status,
                    _,
                    _,
                ) = _validated_checkout_session(existing, expected_metadata)
                if existing_status == "open":
                    return {
                        "ok": True,
                        "case_id": case_id,
                        "checkout_url": existing_url,
                    }
                if existing_status == "complete":
                    raise HTTPException(
                        status_code=409,
                        detail="El pago requiere conciliación antes de continuar",
                    )

            preparation_consent = build_vehicle_removal_preparation_consent()
            idempotency_seed = previous_session_id or "initial"
            idempotency_suffix = hashlib.sha256(
                idempotency_seed.encode("utf-8")
            ).hexdigest()[:16]
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                client_reference_id=case_id,
                customer_email=identity["email"],
                line_items=[{"price": price_id, "quantity": 1}],
                metadata=expected_metadata,
                # El retorno del navegador no acredita pago. La UI consulta el
                # estado autenticado y solo el webhook puede liquidarlo.
                success_url=(
                    f"{frontend_url}/eliminar-coche?case={case_id}&checkout=returned"
                ),
                cancel_url=(
                    f"{frontend_url}/eliminar-coche?case={case_id}&checkout=cancelled"
                ),
                idempotency_key=(
                    f"rtm-vr-v3-{case_id}-{idempotency_suffix}"
                ),
            )
            created_session_id = str(_stripe_value(session, "id") or "").strip()
            (
                session_id,
                checkout_url,
                session_status,
                amount_total,
                currency,
            ) = _validated_checkout_session(session, expected_metadata)
            if session_status != "open":
                raise HTTPException(
                    status_code=502,
                    detail="El proveedor de pago no abrió una sesión utilizable",
                )

            request_evidence = {
                "request_contract": _VEHICLE_REMOVAL_REQUEST_CONTRACT,
                "service_code": _VEHICLE_REMOVAL_SERVICE_CODE,
                "product_code": _VEHICLE_REMOVAL_PRODUCT_CODE,
                "quote_version": _VEHICLE_REMOVAL_QUOTE_VERSION,
                "target_status": "vehicle_removal_pending_payment",
                "plate_verification_status": plate_verification_status,
            }
            preparation_consent_evidence = {
                "accepted": preparation_consent["accepted"],
                "preparation_consent_version": preparation_consent["version"],
                "preparation_consent_sha256": preparation_consent["sha256"],
                "human_review_required": preparation_consent[
                    "human_review_required"
                ],
                "legal_representation": preparation_consent[
                    "legal_representation"
                ],
            }
            checkout_evidence = {
                "session_id": session_id,
                "amount_total": amount_total,
                "currency": currency,
                "product_code": expected_metadata["product_code"],
                "service_code": expected_metadata["service_code"],
                "checkout_contract": expected_metadata["checkout_contract"],
                "quote_version": expected_metadata["quote_version"],
            }
            updated = conn.execute(
                text(
                    """
                    UPDATE cases
                    SET status='vehicle_removal_pending_payment',
                        payment_status='pending',
                        product_code='ELIMINAR_COCHE',
                        contact_email=:email,
                        category='vehicle_removal',
                        interested_data=(
                            COALESCE(interested_data, '{}'::jsonb)
                            || jsonb_build_object(
                                'matricula', CAST(:plate AS text),
                                'matricula_verification_status',
                                    CAST(:plate_verification_status AS text),
                                'vehicle_removal_city', CAST(:city AS text),
                                'vehicle_removal_notes', CAST(:notes AS text),
                                'vehicle_removal_preparation_consent',
                                    CAST(:preparation_consent AS JSONB)
                            )
                        ),
                        authorized=FALSE,
                        authorized_at=NULL,
                        authorization_version=NULL,
                        authorization_ip=NULL,
                        authorization_user_agent=NULL,
                        authorization_full_name=NULL,
                        authorization_dni_nie=NULL,
                        authorization_email=NULL,
                        authorization_phone=NULL,
                        authorization_checks=NULL,
                        authorization_snapshot=NULL,
                        stripe_session_id=:stripe_session_id,
                        updated_at=NOW()
                    WHERE id=:case_id
                      AND COALESCE(status, '')=:expected_case_status
                      AND COALESCE(payment_status, '')=:expected_payment_status
                      AND COALESCE(stripe_session_id, '')=:expected_session_id
                    RETURNING id
                    """
                ),
                {
                    "case_id": case_id,
                    "email": identity["email"],
                    "plate": plate_clean,
                    "city": city_clean,
                    "notes": notes_clean or None,
                    "plate_verification_status": plate_verification_status,
                    "preparation_consent": json.dumps(
                        preparation_consent, ensure_ascii=False
                    ),
                    "stripe_session_id": session_id,
                    "expected_case_status": expected_case_status,
                    "expected_payment_status": expected_payment_status,
                    "expected_session_id": expected_session_id,
                },
            ).fetchone()
            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail="El expediente cambió al vincular la sesión de pago",
                )
            for event_type, event_payload in (
                ("vehicle_removal_request_created", request_evidence),
                (
                    "vehicle_removal_preparation_consent_accepted",
                    preparation_consent_evidence,
                ),
                ("vehicle_removal_checkout_session_created", checkout_evidence),
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO events (case_id, type, payload, created_at)
                        VALUES (:case_id, :event_type, CAST(:payload AS JSONB), NOW())
                        """
                    ),
                    {
                        "case_id": case_id,
                        "event_type": event_type,
                        "payload": json.dumps(event_payload, ensure_ascii=False),
                    },
                )

        transaction_committed = True
        return {"ok": True, "case_id": case_id, "checkout_url": checkout_url}

    except HTTPException:
        if created_session_id and not transaction_committed:
            _expire_checkout_session(created_session_id)
        raise
    except Exception as exc:
        if created_session_id and not transaction_committed:
            _expire_checkout_session(created_session_id)
        raise HTTPException(
            status_code=500,
            detail="No se pudo iniciar el pago de retirada de vehículo",
        ) from exc
