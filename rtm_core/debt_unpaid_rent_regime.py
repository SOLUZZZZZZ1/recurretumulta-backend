"""Selector conservador del régimen de alquiler impagado para RTM.

El módulo separa el encuadre sustantivo del arrendamiento, la reclamación de
cantidad, la recuperación de la posesión, el requisito previo de negociación,
la posible enervación y la vulnerabilidad procesal. No declara la deuda, no
calcula intereses, no acuerda un desahucio y no fija por sí solo prescripción,
competencia, enervación o suspensión del procedimiento.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


DEBT_UNPAID_RENT_REGIME_VERSION = "rtm_debt_unpaid_rent_regime_v1_0"

URBAN_LEASE_BASELINE_ON = date(1995, 1, 1)
HISTORICAL_CONTRACT_REVIEW_BEFORE = date(2019, 3, 6)
MASC_GENERAL_EFFECTIVE_ON = date(2025, 4, 3)
EXTRAORDINARY_2025_END = date(2025, 12, 31)
RDL_2_2026_EFFECTIVE_ON = date(2026, 2, 5)
RDL_2_2026_REPEALED_ON = date(2026, 2, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_unpaid_rent_urban_2026_v1"

ScopeCode = Literal["spain", "foreign", "unknown"]
LeaseKind = Literal[
    "urban_housing",
    "urban_non_housing",
    "room",
    "tourist",
    "rural",
    "public_social",
    "sublease",
    "mixed",
    "unknown",
]
ClaimantRole = Literal[
    "landlord",
    "documented_assignee",
    "insurer_subrogated",
    "tenant",
    "other",
    "unknown",
]
ClaimType = Literal[
    "rent_only",
    "possession_and_rent",
    "possession_only",
    "post_surrender_balance",
    "payment_plan",
    "tenant_defence",
    "unknown",
]
MascLayer = Literal["not_required", "required", "uncertain"]
ExtraordinarySuspensionState = Literal[
    "not_applicable",
    "historic_active_to_2025_12_31",
    "lapsed_2026_01_01_to_2026_02_04",
    "temporary_rdl_2_2026",
    "repealed_from_2026_02_28",
]

_LAU_BASIS = (
    (
        "Ley 29/1994, de Arrendamientos Urbanos, artículos 17 y 27, sobre "
        "determinación y pago de la renta y resolución por falta de pago de "
        "rentas o cantidades asumidas por la parte arrendataria."
    ),
    (
        "Código Civil, artículos 1091, 1100, 1101, 1124 y 1258, sobre fuerza "
        "obligatoria, mora, incumplimiento, resolución y buena fe contractual."
    ),
)
_PROCEDURAL_BASIS = (
    (
        "Ley 1/2000, de Enjuiciamiento Civil, artículos 250.1.1, 22.4 y 439.3, "
        "sobre reclamación de rentas, recuperación de la posesión, enervación y "
        "contenido de la demanda, sin anticipar el resultado del procedimiento."
    ),
)
_MASC_BASIS = (
    (
        "Ley Orgánica 1/2025, artículos 5, 7 y 10, sobre actividad negociadora "
        "previa, efectos sobre prescripción o caducidad y acreditación documental "
        "del intento, para demandas civiles presentadas desde el 3 de abril de 2025."
    ),
)
_VULNERABILITY_BASIS = (
    (
        "Ley 1/2000, de Enjuiciamiento Civil, artículo 441 y concordantes, sobre "
        "comunicación a servicios sociales y tratamiento procesal de posibles "
        "situaciones de vulnerabilidad en vivienda habitual."
    ),
)
_EXTRAORDINARY_BASIS = (
    (
        "Real Decreto-ley 11/2020 y modificaciones posteriores, junto con el "
        "breve periodo de vigencia del Real Decreto-ley 2/2026 entre el 5 y el "
        "27 de febrero de 2026; su posible efecto exige revisión temporal y no "
        "equivale a una suspensión extraordinaria general actualmente vigente."
    ),
)
_LIMITATION_BASIS = (
    (
        "Código Civil, artículos 1966 y 1973, como referencia para rentas "
        "periódicas e interrupción de la prescripción; cada vencimiento, el dies "
        "a quo y los actos interruptivos deben verificarse individualmente."
    ),
)


class DebtUnpaidRentRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    evaluation_date: date
    contract_date: Optional[date] = None
    lease_start_date: Optional[date] = None
    lease_end_date: Optional[date] = None
    first_unpaid_date: Optional[date] = None
    last_unpaid_date: Optional[date] = None
    prior_demand_date: Optional[date] = None
    prior_demand_received_date: Optional[date] = None
    masc_request_date: Optional[date] = None
    masc_received_date: Optional[date] = None
    court_filing_date: Optional[date] = None
    possession_return_date: Optional[date] = None

    scope: ScopeCode = "unknown"
    lease_kind: LeaseKind = "unknown"
    claimant_role: ClaimantRole = "unknown"
    claim_type: ClaimType = "unknown"
    habitual_dwelling: bool = False
    possession_recovery_requested: bool = False
    possession_returned: bool = False

    masc_layer: MascLayer = "uncertain"
    masc_required: bool = False
    masc_documented: bool = False
    masc_no_response_days: Optional[int] = None
    masc_filing_window_years: Optional[int] = None

    enervation_applicable: bool = False
    enervation_preclusion_possible: bool = False
    enervation_reason: Optional[str] = None
    enervation_requires_operator_review: bool = False

    general_vulnerability_review: bool = False
    extraordinary_suspension_state: ExtraordinarySuspensionState = "not_applicable"
    extraordinary_suspension_active: bool = False

    rent_limitation_candidate_years: Optional[int] = None
    historic_contract_review: bool = False
    ruleset: Optional[str] = None
    legal_basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reason: Optional[str] = None


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = (raw, raw.replace("/", "-"), raw.replace(".", "-"))
    for candidate in candidates:
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    for separator in ("/", "-", "."):
        parts = raw.split(separator)
        if len(parts) != 3:
            continue
        try:
            day, month, year = (int(part) for part in parts)
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {
        "si",
        "true",
        "1",
        "consta",
        "acreditado",
        "aportado",
        "vigente",
    }:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "no consta",
        "no acreditado",
        "no aportado",
        "extinguido",
    }:
        return False
    return None


def _scope(property_country: Any) -> ScopeCode:
    folded = _fold(property_country)
    if not folded:
        return "unknown"
    if "espana" in folded or "spain" in folded:
        return "spain"
    return "foreign"


def _lease_kind(
    use: Any,
    *,
    room_lease: Any,
    seasonal_lease: Any,
    tourist_lease: Any,
    rural_lease: Any,
    public_social_lease: Any,
    sublease: Any,
) -> LeaseKind:
    flags: list[LeaseKind] = []
    if _optional_bool(room_lease) is True:
        flags.append("room")
    if _optional_bool(tourist_lease) is True:
        flags.append("tourist")
    if _optional_bool(rural_lease) is True:
        flags.append("rural")
    if _optional_bool(public_social_lease) is True:
        flags.append("public_social")
    if _optional_bool(sublease) is True:
        flags.append("sublease")
    if len(set(flags)) > 1:
        return "mixed"
    if flags:
        return flags[0]

    folded = _fold(use)
    seasonal = _optional_bool(seasonal_lease)
    if seasonal is True:
        return "urban_non_housing"
    if any(
        marker in folded
        for marker in (
            "vivienda habitual",
            "vivienda permanente",
            "arrendamiento de vivienda",
            "uso residencial",
            "domicilio habitual",
        )
    ):
        return "urban_housing"
    if any(
        marker in folded
        for marker in (
            "local",
            "comercial",
            "oficina",
            "nave",
            "uso distinto de vivienda",
            "temporada",
        )
    ):
        return "urban_non_housing"
    return "unknown"


def _claimant_role(
    explicit: Any,
    *,
    landlord_claims: Any,
    tenant_defence: Any,
    assignment_documented: Any,
    insurer_subrogation: Any,
) -> ClaimantRole:
    if _optional_bool(tenant_defence) is True:
        return "tenant"
    if _optional_bool(insurer_subrogation) is True:
        return "insurer_subrogated"
    text = _fold(explicit)
    if any(marker in text for marker in ("arrendador", "propietario", "landlord")):
        return "landlord"
    if any(marker in text for marker in ("cesionario", "adquirente del credito")):
        return (
            "documented_assignee"
            if _optional_bool(assignment_documented) is True
            else "other"
        )
    if any(marker in text for marker in ("arrendatario", "inquilino", "tenant")):
        return "tenant"
    if _optional_bool(landlord_claims) is True:
        return "landlord"
    if text:
        return "other"
    return "unknown"


def _claim_type(
    *,
    possession_recovery_requested: Any,
    rent_claim_requested: Any,
    contract_termination_requested: Any,
    possession_returned: Any,
    payment_plan_requested: Any,
    tenant_defence: Any,
) -> ClaimType:
    if _optional_bool(tenant_defence) is True:
        return "tenant_defence"
    if _optional_bool(payment_plan_requested) is True:
        return "payment_plan"
    possession = _optional_bool(possession_recovery_requested) is True
    rent = _optional_bool(rent_claim_requested) is True
    termination = _optional_bool(contract_termination_requested) is True
    returned = _optional_bool(possession_returned) is True
    if returned and rent:
        return "post_surrender_balance"
    if possession and rent:
        return "possession_and_rent"
    if possession or termination:
        return "possession_only"
    if rent:
        return "rent_only"
    return "unknown"


def _extraordinary_state(
    evaluation_date: date,
) -> tuple[ExtraordinarySuspensionState, bool]:
    if evaluation_date <= EXTRAORDINARY_2025_END:
        return "historic_active_to_2025_12_31", True
    if evaluation_date < RDL_2_2026_EFFECTIVE_ON:
        return "lapsed_2026_01_01_to_2026_02_04", False
    if evaluation_date < RDL_2_2026_REPEALED_ON:
        return "temporary_rdl_2_2026", True
    return "repealed_from_2026_02_28", False


def _operator_review(
    *,
    common: dict[str, Any],
    reason: str,
    warnings: list[str] | None = None,
) -> DebtUnpaidRentRegimeDecision:
    return DebtUnpaidRentRegimeDecision(
        status="operator_review",
        **common,
        warnings=tuple(dict.fromkeys(warnings or [])),
        blocking_reason=reason,
    )


def resolve_debt_unpaid_rent_regime(
    *,
    evaluation_date: Any = None,
    contract_date: Any,
    lease_start_date: Any = None,
    lease_end_date: Any = None,
    first_unpaid_date: Any = None,
    last_unpaid_date: Any = None,
    prior_demand_date: Any = None,
    prior_demand_received_date: Any = None,
    masc_request_date: Any = None,
    masc_received_date: Any = None,
    court_filing_date: Any = None,
    possession_return_date: Any = None,
    property_country: Any,
    property_use: Any,
    room_lease: Any = None,
    seasonal_lease: Any = None,
    tourist_lease: Any = None,
    rural_lease: Any = None,
    public_social_lease: Any = None,
    sublease: Any = None,
    habitual_dwelling: Any = None,
    claimant_role: Any = None,
    landlord_claims: Any = None,
    tenant_defence: Any = None,
    assignment_documented: Any = None,
    insurer_subrogation: Any = None,
    possession_recovery_requested: Any = None,
    rent_claim_requested: Any = None,
    contract_termination_requested: Any = None,
    possession_returned: Any = None,
    payment_plan_requested: Any = None,
    judicial_action_intended: Any = None,
    execution_only: Any = None,
    masc_started: Any = None,
    masc_object_coincident: Any = None,
    masc_proof_documented: Any = None,
    prior_enervation: Any = None,
    payment_after_demand: Any = None,
    debt_paid: Any = None,
) -> DebtUnpaidRentRegimeDecision:
    evaluation = _parse_date(evaluation_date) or date.today()
    contract = _parse_date(contract_date)
    start = _parse_date(lease_start_date)
    end = _parse_date(lease_end_date)
    first_unpaid = _parse_date(first_unpaid_date)
    last_unpaid = _parse_date(last_unpaid_date)
    demand = _parse_date(prior_demand_date)
    demand_received = _parse_date(prior_demand_received_date)
    masc_request = _parse_date(masc_request_date)
    masc_received = _parse_date(masc_received_date)
    filing = _parse_date(court_filing_date)
    returned_on = _parse_date(possession_return_date)

    scope = _scope(property_country)
    lease = _lease_kind(
        property_use,
        room_lease=room_lease,
        seasonal_lease=seasonal_lease,
        tourist_lease=tourist_lease,
        rural_lease=rural_lease,
        public_social_lease=public_social_lease,
        sublease=sublease,
    )
    role = _claimant_role(
        claimant_role,
        landlord_claims=landlord_claims,
        tenant_defence=tenant_defence,
        assignment_documented=assignment_documented,
        insurer_subrogation=insurer_subrogation,
    )
    claim = _claim_type(
        possession_recovery_requested=possession_recovery_requested,
        rent_claim_requested=rent_claim_requested,
        contract_termination_requested=contract_termination_requested,
        possession_returned=possession_returned,
        payment_plan_requested=payment_plan_requested,
        tenant_defence=tenant_defence,
    )

    possession_requested = _optional_bool(possession_recovery_requested) is True
    returned = _optional_bool(possession_returned) is True or returned_on is not None
    habitual = _optional_bool(habitual_dwelling) is True
    action_intended = _optional_bool(judicial_action_intended)
    if action_intended is None:
        action_intended = claim in {
            "rent_only",
            "possession_and_rent",
            "possession_only",
            "post_surrender_balance",
        }
    execution = _optional_bool(execution_only) is True

    masc_required = bool(
        action_intended
        and not execution
        and (
            (filing is not None and filing >= MASC_GENERAL_EFFECTIVE_ON)
            or (filing is None and evaluation >= MASC_GENERAL_EFFECTIVE_ON)
        )
    )
    masc_layer: MascLayer = "required" if masc_required else "not_required"
    if action_intended is None:
        masc_layer = "uncertain"
    masc_documented = bool(
        _optional_bool(masc_started) is True
        and (masc_received is not None or masc_request is not None)
        and _optional_bool(masc_object_coincident) is True
        and _optional_bool(masc_proof_documented) is True
    )

    enervation_applicable = bool(
        possession_requested and claim in {"possession_and_rent", "possession_only"}
    )
    prior_enervated = _optional_bool(prior_enervation) is True
    paid_after = _optional_bool(payment_after_demand) is True
    enervation_preclusion_possible = False
    enervation_reason: Optional[str] = None
    if enervation_applicable:
        if prior_enervated:
            enervation_preclusion_possible = True
            enervation_reason = (
                "Consta una enervación anterior, pendiente de verificar en el "
                "procedimiento y respecto del mismo arrendamiento."
            )
        elif demand_received and filing:
            elapsed = (filing - demand_received).days
            if elapsed >= 30 and not paid_after:
                enervation_preclusion_possible = True
                enervation_reason = (
                    "Consta un requerimiento recibido al menos treinta días antes "
                    "de la demanda y no consta pago posterior; deben revisarse "
                    "contenido, recepción y excepciones antes de cerrar la enervación."
                )
            else:
                enervation_reason = (
                    "El intervalo o el pago posterior no permite afirmar la "
                    "exclusión de la enervación."
                )
        else:
            enervation_reason = (
                "Faltan la recepción del requerimiento o la fecha de demanda para "
                "valorar el régimen de enervación."
            )

    general_vulnerability = bool(
        habitual and possession_requested and claim in {"possession_and_rent", "possession_only"}
    )
    extraordinary_state, extraordinary_active = _extraordinary_state(evaluation)
    historic_contract = bool(contract and contract < HISTORICAL_CONTRACT_REVIEW_BEFORE)

    common: dict[str, Any] = {
        "evaluation_date": evaluation,
        "contract_date": contract,
        "lease_start_date": start,
        "lease_end_date": end,
        "first_unpaid_date": first_unpaid,
        "last_unpaid_date": last_unpaid,
        "prior_demand_date": demand,
        "prior_demand_received_date": demand_received,
        "masc_request_date": masc_request,
        "masc_received_date": masc_received,
        "court_filing_date": filing,
        "possession_return_date": returned_on,
        "scope": scope,
        "lease_kind": lease,
        "claimant_role": role,
        "claim_type": claim,
        "habitual_dwelling": habitual,
        "possession_recovery_requested": possession_requested,
        "possession_returned": returned,
        "masc_layer": masc_layer,
        "masc_required": masc_required,
        "masc_documented": masc_documented,
        "masc_no_response_days": 30 if masc_required else None,
        "masc_filing_window_years": 1 if masc_required else None,
        "enervation_applicable": enervation_applicable,
        "enervation_preclusion_possible": enervation_preclusion_possible,
        "enervation_reason": enervation_reason,
        "enervation_requires_operator_review": enervation_applicable,
        "general_vulnerability_review": general_vulnerability,
        "extraordinary_suspension_state": extraordinary_state,
        "extraordinary_suspension_active": extraordinary_active,
        "rent_limitation_candidate_years": 5,
        "historic_contract_review": historic_contract,
    }

    if evaluation > CURRENT_RULESET_SAFE_THROUGH:
        return _operator_review(
            common=common,
            reason=(
                "La fecha de evaluación supera el horizonte jurídico verificado "
                "para alquiler impagado. Deben versionarse las reformas posteriores."
            ),
        )
    if contract is None:
        return _operator_review(
            common=common,
            reason=(
                "Falta la fecha documental del contrato de arrendamiento y no puede "
                "seleccionarse con seguridad el régimen temporal."
            ),
        )
    if contract < URBAN_LEASE_BASELINE_ON:
        return _operator_review(
            common=common,
            reason=(
                "El arrendamiento es anterior al horizonte de la Ley 29/1994 y "
                "requiere revisar disposiciones transitorias y legislación histórica."
            ),
        )
    dated_values = (
        contract,
        start,
        end,
        first_unpaid,
        last_unpaid,
        demand,
        demand_received,
        masc_request,
        masc_received,
        filing,
        returned_on,
    )
    if any(value is not None and value > CURRENT_RULESET_SAFE_THROUGH for value in dated_values):
        return _operator_review(
            common=common,
            reason=(
                "Alguna fecha del contrato, impago, requerimiento o procedimiento "
                "supera el horizonte jurídico verificado."
            ),
        )
    if start and start < contract:
        return _operator_review(
            common=common,
            reason=(
                "La fecha de inicio aparece anterior al contrato; debe revisarse "
                "si existió un acuerdo previo, renovación o error documental."
            ),
        )
    if start and end and end < start:
        return _operator_review(
            common=common,
            reason="La fecha final del arrendamiento es anterior a su inicio.",
        )
    if start and first_unpaid and first_unpaid < start:
        return _operator_review(
            common=common,
            reason="El primer impago aparece anterior al inicio del arrendamiento.",
        )
    if first_unpaid and last_unpaid and last_unpaid < first_unpaid:
        return _operator_review(
            common=common,
            reason="El último periodo impagado aparece anterior al primero.",
        )
    if demand and demand_received and demand_received < demand:
        return _operator_review(
            common=common,
            reason="La recepción del requerimiento aparece anterior a su emisión.",
        )
    if masc_request and masc_received and masc_received < masc_request:
        return _operator_review(
            common=common,
            reason="La recepción de la solicitud negociadora aparece anterior a su envío.",
        )
    if start and returned_on and returned_on < start:
        return _operator_review(
            common=common,
            reason="La devolución de la posesión aparece anterior al inicio del contrato.",
        )
    if scope != "spain":
        reason = (
            "El inmueble no consta situado en España; deben determinarse ley, foro "
            "y procedimiento aplicables."
            if scope == "foreign"
            else (
                "Falta el país documental del inmueble y no puede confirmarse el "
                "régimen español."
            )
        )
        return _operator_review(common=common, reason=reason)
    if lease in {"unknown", "mixed"}:
        return _operator_review(
            common=common,
            reason=(
                "Debe determinarse si se arrienda una vivienda completa, un inmueble "
                "para uso distinto, una habitación o concurre otra modalidad."
            ),
        )
    unsupported = {
        "room": (
            "El alquiler de habitación puede quedar fuera del régimen ordinario de "
            "arrendamiento de vivienda y exige revisar contrato, uso y Código Civil."
        ),
        "tourist": (
            "El alojamiento turístico requiere normativa autonómica y contractual específica."
        ),
        "rural": "El arrendamiento rústico requiere su legislación especial.",
        "public_social": (
            "La vivienda pública o social puede estar sometida a régimen administrativo especial."
        ),
        "sublease": (
            "El subarriendo exige revisar autorización, contrato principal y legitimación."
        ),
    }
    if lease in unsupported:
        return _operator_review(common=common, reason=unsupported[lease])
    if role == "tenant" or claim == "tenant_defence":
        return _operator_review(
            common=common,
            reason=(
                "La actuación se plantea desde la posición de la parte arrendataria; "
                "debe dirigirse al especialista de oposición o defensa del deudor."
            ),
        )
    if role == "insurer_subrogated":
        return _operator_review(
            common=common,
            reason=(
                "La reclamación procede de una aseguradora subrogada; deben verificarse "
                "pago, alcance de la subrogación, legitimación y cantidades recuperadas."
            ),
        )
    if role not in {"landlord", "documented_assignee"}:
        return _operator_review(
            common=common,
            reason=(
                "No está acreditado que la parte reclamante sea arrendadora o cesionaria "
                "documentada del crédito."
            ),
        )
    if claim == "unknown":
        return _operator_review(
            common=common,
            reason=(
                "Debe concretarse si se reclama solo cantidad, resolución y posesión, "
                "saldo tras entrega de llaves o un acuerdo de pago."
            ),
        )
    if _optional_bool(debt_paid) is True:
        return _operator_review(
            common=common,
            reason=(
                "La deuda figura pagada; debe reconstruirse el saldo antes de mantener "
                "una reclamación de rentas."
            ),
        )

    basis = [*_LAU_BASIS, *_PROCEDURAL_BASIS, *_LIMITATION_BASIS]
    if masc_required:
        basis.extend(_MASC_BASIS)
    if general_vulnerability:
        basis.extend(_VULNERABILITY_BASIS)
    if habitual and possession_requested:
        basis.extend(_EXTRAORDINARY_BASIS)

    warnings = [
        (
            "La renta, los periodos y cada concepto repercutido deben provenir del "
            "contrato, recibos y movimientos; el sistema no recalcula ni actualiza "
            "la renta automáticamente."
        ),
        (
            "Suministros, comunidad, tributos y otros conceptos solo pueden reclamarse "
            "cuando exista base contractual o legal y cuantificación documentada."
        ),
        (
            "La fianza no se compensa automáticamente con rentas mientras siga la "
            "posesión ni sin liquidación o acuerdo documentado."
        ),
        (
            "El plazo de cinco años es solo un candidato para rentas periódicas; "
            "deben revisarse cada vencimiento y las interrupciones de prescripción."
        ),
        (
            "La exclusión de la enervación nunca debe afirmarse sin comprobar el "
            "procedimiento, el requerimiento, su recepción, el intervalo y los pagos."
        ),
        (
            "La recuperación de la posesión y la reclamación de cantidad son pretensiones "
            "relacionadas pero exigen revisar contrato vigente, entrega de llaves y saldo."
        ),
    ]
    if historic_contract:
        warnings.append(
            "El contrato es anterior a marzo de 2019 y exige revisar redacción aplicable, prórrogas y modificaciones."
        )
    if masc_required and not masc_documented:
        warnings.append(
            "La negociación previa parece exigible, pero no consta todavía acreditada con objeto coincidente y recepción."
        )
    if returned and possession_requested:
        warnings.append(
            "Consta devolución de la posesión; debe eliminarse la pretensión de desahucio y limitarse el análisis al saldo, si existe."
        )
    if extraordinary_state == "temporary_rdl_2_2026":
        warnings.append(
            "La fecha de evaluación cae en el breve periodo 5–27 de febrero de 2026; deben revisarse los efectos transitorios de la norma después derogada."
        )
    elif evaluation >= RDL_2_2026_REPEALED_ON:
        warnings.append(
            "No existe en esta fecha una suspensión extraordinaria general vigente hasta diciembre de 2026; permanecen las reglas procesales ordinarias de vulnerabilidad."
        )
    if general_vulnerability:
        warnings.append(
            "La vulnerabilidad exige información y decisión judicial; una alegación no determina automáticamente suspensión ni archivo."
        )
    if enervation_reason:
        warnings.append(enervation_reason)

    return DebtUnpaidRentRegimeDecision(
        status="current",
        **common,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
