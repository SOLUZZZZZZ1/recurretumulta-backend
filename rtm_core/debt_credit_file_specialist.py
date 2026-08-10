"""Especialista RTM para ``debt.credit_file``.

Ordena expedientes relacionados con ASNEF y otros sistemas de información
crediticia. Distingue el derecho de acceso —cuando todavía no se conoce la
inclusión— de la rectificación, limitación o supresión de una inclusión ya
identificada. No consulta bases privadas, no presume que una deuda sea falsa,
no convierte una simple discrepancia en una reclamación vinculante y no promete
una baja automática.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import (
    Deadline,
    LegalPreview,
    MissingItem,
    MissingItemSeverity,
    PreviewStatus,
)
from rtm_core.cross_service_specialist_support import (
    CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION,
    dedupe_missing,
    document_uses,
    ensure_specialist_authority,
    fact_review_items,
    family_evidence_keys,
    legal_argument,
    missing_item,
    summary_rows,
    validated_source_keys,
    validated_value,
)


DEBT_CREDIT_FILE_SPECIALIST_VERSION = "rtm_debt_credit_file_specialist_v1_0"

_DATA_RIGHTS_BASIS = [
    (
        "Reglamento (UE) 2016/679, artículos 12, 15, 16, 17, 18 y 19, "
        "según el derecho ejercitado y la respuesta del responsable."
    ),
    (
        "Ley Orgánica 3/2018, de 5 de diciembre, artículos 12 a 16, "
        "sobre el ejercicio de los derechos de protección de datos."
    ),
]
_CREDIT_FILE_BASIS = [
    (
        "Ley Orgánica 3/2018, artículo 20, sobre sistemas de información "
        "crediticia."
    ),
    (
        "Ley Orgánica 3/2018, disposición adicional sexta, sobre el umbral "
        "mínimo del principal incorporable."
    ),
]

CreditFileRoute = Literal["access", "rights"]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _display(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "Sí" if value else "No"
    return str(value)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw = str(value or "").strip()
    if not raw:
        return None

    for candidate in (raw, raw.replace("/", "-"), raw.replace(".", "-")):
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


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raw = str(value).strip().replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "fichero_solvencia",
        "concepto_deuda",
        "fase_procedimental",
        "respuesta_documentada",
        "solucion_solicitada",
        "procedimiento_judicial",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _system_labels(record: ValidatedFactsRecord) -> list[str]:
    labels: list[str] = []
    explicit, _ = validated_value(record, "fichero_solvencia")
    if isinstance(explicit, (list, tuple, set)):
        labels.extend(str(item).strip() for item in explicit if str(item).strip())
    elif _present(explicit):
        labels.append(str(explicit).strip())

    text = _all_text(record)
    recognized = (
        ("asnef", "ASNEF"),
        ("equifax", "ASNEF / EQUIFAX"),
        ("badexcug", "BADEXCUG"),
        ("experian", "BADEXCUG / EXPERIAN"),
        ("rai", "RAI"),
    )
    for token, label in recognized:
        if token in text and not any(token in _fold(item) for item in labels):
            labels.append(label)

    return list(dict.fromkeys(item for item in labels if item))


def _route(record: ValidatedFactsRecord) -> CreditFileRoute:
    text = _all_text(record)
    access_markers = (
        "derecho de acceso",
        "solicitud de acceso",
        "saber si",
        "conocer si",
        "comprobar si",
        "consultar si",
        "obtener informe",
        "que datos constan",
    )
    rights_markers = (
        "supresion",
        "rectificacion",
        "limitacion",
        "solicitud de baja",
        "dar de baja",
        "eliminar los datos",
        "cancelacion de datos",
    )
    explicit_access = any(marker in text for marker in access_markers)
    explicit_rights = any(marker in text for marker in rights_markers)

    inclusion, _ = validated_value(record, "fecha_inclusion_fichero")
    creditor, _ = validated_value(record, "acreedor", "emisor_documento")
    amount, _ = validated_value(
        record,
        "importe_deuda_eur",
        "saldo_pendiente_eur",
        "importe_reclamado_eur",
    )
    paid, _ = validated_value(record, "deuda_pagada")
    disputed, _ = validated_value(record, "deuda_discutida")
    known_inclusion = any(
        (
            _present(inclusion),
            _present(creditor),
            _present(amount),
            paid is True,
            disputed is True,
        )
    )

    if explicit_access and not explicit_rights:
        return "access"
    if explicit_rights or known_inclusion:
        return "rights"
    return "access"


def _formal_dispute(record: ValidatedFactsRecord) -> tuple[bool, list[Optional[str]]]:
    procedure, procedure_key = validated_value(
        record,
        "procedimiento_judicial",
    )
    procedure_number, number_key = validated_value(
        record,
        "numero_procedimiento",
    )
    judicial_body, judicial_body_key = validated_value(
        record,
        "organo_judicial",
    )
    case_ref, case_ref_key = validated_value(record, "expediente_ref")
    text = _all_text(record)
    markers = (
        "demanda judicial",
        "procedimiento judicial",
        "reclamacion administrativa",
        "junta arbitral",
        "arbitraje de consumo",
        "procedimiento alternativo de resolucion",
    )
    result = any(
        (
            _present(procedure),
            _present(procedure_number),
            _present(judicial_body),
            _present(case_ref) and any(marker in text for marker in markers),
            any(marker in text for marker in markers),
        )
    )
    return result, [procedure_key, number_key, judicial_body_key, case_ref_key]


def _identity_error(record: ValidatedFactsRecord) -> bool:
    text = _all_text(record)
    return any(
        marker in text
        for marker in (
            "no es mi deuda",
            "deuda no es mia",
            "suplantacion",
            "identidad equivocada",
            "persona equivocada",
            "error de identidad",
        )
    )


def _strong_rights_ground(record: ValidatedFactsRecord) -> bool:
    paid, _ = validated_value(record, "deuda_pagada")
    amount, _ = validated_value(
        record,
        "importe_deuda_eur",
        "saldo_pendiente_eur",
        "importe_reclamado_eur",
    )
    amount_decimal = _decimal(amount)
    formal, _ = _formal_dispute(record)
    return any(
        (
            paid is True,
            amount_decimal is not None and amount_decimal < Decimal("50"),
            formal,
            _identity_error(record),
        )
    )


def _required_missing(
    record: ValidatedFactsRecord,
    route: CreditFileRoute,
    systems: list[str],
) -> list[MissingItem]:
    result: list[MissingItem] = []
    solution, _ = validated_value(record, "solucion_solicitada")
    if not _present(solution):
        result.append(
            missing_item(
                "credit_file_requested_outcome_missing",
                (
                    "Debe confirmarse si se solicita acceso, rectificación, "
                    "limitación, supresión o una combinación de derechos."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if not systems:
        result.append(
            missing_item(
                "credit_file_system_missing",
                (
                    "Debe seleccionarse el sistema de información crediticia al "
                    "que se dirigirá la solicitud."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif len(systems) > 1:
        result.append(
            missing_item(
                "credit_file_multiple_systems_split_required",
                (
                    "Hay varios sistemas identificados. Debe generarse una "
                    "solicitud separada y trazable para cada responsable."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if route == "access":
        return result

    groups = (
        (
            "credit_file_creditor_missing",
            "Falta validar el acreedor que comunicó la deuda.",
            ("acreedor", "emisor_documento"),
        ),
        (
            "credit_file_amount_missing",
            "Falta validar la cuantía identificadora de la deuda incluida.",
            (
                "importe_deuda_eur",
                "saldo_pendiente_eur",
                "importe_reclamado_eur",
            ),
        ),
        (
            "credit_file_due_date_missing",
            (
                "Falta validar el vencimiento de la obligación, necesario para "
                "revisar exigibilidad y permanencia máxima."
            ),
            ("fecha_vencimiento",),
        ),
    )
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    route: CreditFileRoute,
) -> list[MissingItem]:
    result: list[MissingItem] = [
        missing_item(
            "credit_file_identity_and_representation_review",
            (
                "Debe verificarse la identidad del afectado y, si actúa un "
                "representante, el documento de representación y el alcance del "
                "mandato antes de enviar la solicitud."
            ),
            MissingItemSeverity.HUMAN_REVIEW,
        )
    ]

    if route == "access":
        result.append(
            missing_item(
                "credit_file_access_response_review",
                (
                    "La respuesta de acceso deberá revisarse para identificar "
                    "acreedor, cuantía, vencimiento, origen, fechas, consultas y "
                    "destinatarios antes de preparar cualquier solicitud de baja."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        return result

    prior_demand, _ = validated_value(record, "requerimiento_previo_fecha")
    prior_channel, _ = validated_value(record, "requerimiento_previo_medio")
    file_warning, _ = validated_value(record, "fecha_requerimiento_fichero")
    inclusion_notice, _ = validated_value(record, "fecha_notificacion")
    inclusion_date, _ = validated_value(record, "fecha_inclusion_fichero")

    if not _present(prior_demand) or not _present(prior_channel):
        result.append(
            missing_item(
                "credit_file_prior_payment_demand_review",
                (
                    "Debe aportarse el requerimiento de pago previo, con fecha, "
                    "contenido, medio y prueba de recepción."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if not _present(file_warning):
        result.append(
            missing_item(
                "credit_file_prior_inclusion_information_review",
                (
                    "Debe comprobarse cuándo y cómo informó el acreedor de la "
                    "posibilidad de inclusión y qué sistemas identificó."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(inclusion_date) and not _present(inclusion_notice):
        result.append(
            missing_item(
                "credit_file_system_notification_review",
                (
                    "Consta una fecha de inclusión, pero falta la notificación del "
                    "responsable del sistema y la información sobre derechos."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    paid, _ = validated_value(record, "deuda_pagada")
    if paid is True:
        result.append(
            missing_item(
                "credit_file_paid_debt_active_status_review",
                (
                    "La deuda figura pagada. Debe comprobarse si la inclusión sigue "
                    "activa y exigirse, en su caso, la supresión inmediata."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    amount, _ = validated_value(
        record,
        "importe_deuda_eur",
        "saldo_pendiente_eur",
        "importe_reclamado_eur",
    )
    amount_decimal = _decimal(amount)
    if amount_decimal is not None and amount_decimal < Decimal("50"):
        result.append(
            missing_item(
                "credit_file_principal_below_threshold_review",
                (
                    "La cuantía total validada es inferior a 50 €. Debe comprobarse "
                    "el principal exacto, porque una deuda con principal inferior a "
                    "ese umbral no puede incorporarse al sistema."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    disputed, _ = validated_value(record, "deuda_discutida")
    formal, _ = _formal_dispute(record)
    if disputed is True and not formal:
        result.append(
            missing_item(
                "credit_file_informal_dispute_not_enough_review",
                (
                    "La deuda consta discutida, pero no se ha acreditado todavía "
                    "una reclamación administrativa o judicial ni un procedimiento "
                    "alternativo vinculante. La mera discrepancia no permite "
                    "presumir por sí sola una inclusión ilícita."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if formal:
        result.append(
            missing_item(
                "credit_file_formal_dispute_scope_review",
                (
                    "Debe comprobarse que el procedimiento formal afecta a la "
                    "existencia o cuantía exacta de la deuda incluida y que continúa "
                    "en tramitación o ha producido una resolución relevante."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    solution, _ = validated_value(record, "solucion_solicitada")
    solution_text = _fold(solution)
    explicit_erasure = any(
        token in solution_text
        for token in (
            "supres",
            "baja",
            "eliminar",
            "cancelacion",
        )
    )
    if explicit_erasure and not _strong_rights_ground(record):
        result.append(
            missing_item(
                "credit_file_erasure_ground_missing",
                (
                    "Se solicita la supresión, pero todavía no consta un fundamento "
                    "documental suficiente —pago, error de identidad, principal "
                    "inferior al mínimo o controversia formal vinculante—. Puede "
                    "ejercitarse acceso o limitación mientras se completa la prueba."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "credit_file_source_documents_review",
                (
                    "OPS debe obtener contrato, liquidación o documento de origen, "
                    "historial de pagos, cesiones y desglose de la deuda."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "credit_file_full_access_response_review",
                (
                    "Debe conservarse la respuesta completa del sistema, incluidos "
                    "origen de los datos, acreedor informante, fechas y destinatarios "
                    "o consultas comunicadas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )
    return result


def _deadlines(
    record: ValidatedFactsRecord,
    route: CreditFileRoute,
) -> list[Deadline]:
    deadlines = [
        Deadline(
            label="Respuesta al ejercicio de derechos",
            due_at=None,
            calculation_status="unresolved",
            notes=[
                (
                    "El responsable debe responder, con carácter general, en un "
                    "mes desde la recepción. La posible ampliación debe comunicarse "
                    "dentro del primer mes. RTM no calcula la fecha sin acuse de "
                    "recepción validado."
                )
            ],
        )
    ]

    if route == "rights":
        inclusion, inclusion_key = validated_value(
            record,
            "fecha_inclusion_fichero",
        )
        if _present(inclusion):
            deadlines.append(
                Deadline(
                    label="Notificación de la inclusión y bloqueo inicial",
                    due_at=None,
                    calculation_status="unresolved",
                    source_fact_keys=[inclusion_key] if inclusion_key else [],
                    notes=[
                        (
                            "Debe comprobarse la notificación dentro de los treinta "
                            "días siguientes a la comunicación de la deuda al "
                            "sistema y el bloqueo durante ese periodo."
                        )
                    ],
                )
            )

        due, due_key = validated_value(record, "fecha_vencimiento")
        if _present(due):
            deadlines.append(
                Deadline(
                    label="Permanencia máxima de los datos",
                    due_at=None,
                    calculation_status="unresolved",
                    source_fact_keys=[due_key] if due_key else [],
                    notes=[
                        (
                            "El límite máximo es de cinco años desde el vencimiento "
                            "de la obligación o del plazo periódico correspondiente. "
                            "Debe revisarse la naturaleza de la deuda y el día inicial "
                            "antes de fijar una fecha."
                        )
                    ],
                )
            )
    return deadlines


def build_debt_credit_file_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="debt",
        family="fichero_solvencia",
        specialist="debt.credit_file",
    )

    route = _route(facts_record)
    systems = _system_labels(facts_record)

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    system, system_key = validated_value(facts_record, "fichero_solvencia")
    creditor, creditor_key = validated_value(
        facts_record,
        "acreedor",
        "emisor_documento",
    )
    debtor, debtor_key = validated_value(
        facts_record,
        "deudor",
        "destinatario_documento",
    )
    concept, concept_key = validated_value(facts_record, "concepto_deuda")
    amount, amount_key = validated_value(
        facts_record,
        "importe_deuda_eur",
        "saldo_pendiente_eur",
        "importe_reclamado_eur",
    )
    due, due_key = validated_value(facts_record, "fecha_vencimiento")
    inclusion, inclusion_key = validated_value(
        facts_record,
        "fecha_inclusion_fichero",
    )
    payment_demand, payment_demand_key = validated_value(
        facts_record,
        "requerimiento_previo_fecha",
    )
    payment_channel, payment_channel_key = validated_value(
        facts_record,
        "requerimiento_previo_medio",
    )
    inclusion_warning, inclusion_warning_key = validated_value(
        facts_record,
        "fecha_requerimiento_fichero",
    )
    system_notice, system_notice_key = validated_value(
        facts_record,
        "fecha_notificacion",
    )
    paid, paid_key = validated_value(facts_record, "deuda_pagada")
    disputed, disputed_key = validated_value(facts_record, "deuda_discutida")
    procedure, procedure_key = validated_value(
        facts_record,
        "procedimiento_judicial",
    )
    procedure_number, procedure_number_key = validated_value(
        facts_record,
        "numero_procedimiento",
    )
    response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("fichero_solvencia", "Sistema de información crediticia", ""),
            ("acreedor", "Acreedor informante", ""),
            ("deudor", "Persona afectada", ""),
            ("concepto_deuda", "Concepto", ""),
            ("importe_deuda_eur", "Importe comunicado", " €"),
            ("saldo_pendiente_eur", "Saldo comunicado", " €"),
            ("fecha_vencimiento", "Vencimiento", ""),
            ("fecha_inclusion_fichero", "Fecha de inclusión", ""),
            ("requerimiento_previo_fecha", "Requerimiento de pago", ""),
            ("requerimiento_previo_medio", "Canal del requerimiento", ""),
            ("fecha_requerimiento_fichero", "Información previa sobre inclusión", ""),
            ("fecha_notificacion", "Notificación del sistema", ""),
            ("deuda_discutida", "Deuda discutida", ""),
            ("deuda_pagada", "Deuda pagada", ""),
            ("procedimiento_judicial", "Procedimiento formal", ""),
            ("numero_procedimiento", "Número de procedimiento", ""),
            ("solucion_solicitada", "Derecho o solución solicitada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {_display(fact)}.")
        if fact_key:
            summary_keys.insert(0, fact_key)

    arguments = []

    access_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            system_key,
            creditor_key,
            amount_key,
            inclusion_key,
            solution_key,
            response_key,
        ),
    )
    if access_sources:
        system_text = ", ".join(systems) if systems else "sistema pendiente de identificar"
        arguments.append(
            legal_argument(
                facts_record,
                code="credit_file_access_source_and_recipients",
                title="Acceso, origen de los datos y destinatarios",
                body=(
                    "La persona afectada puede solicitar confirmación de si se "
                    "tratan sus datos y obtener la información necesaria para "
                    "identificar la inclusión, su origen, acreedor, cuantía, fechas, "
                    "finalidad y destinatarios o consultas comunicables. El sistema "
                    f"identificado en el expediente es: {system_text}. La solicitud "
                    "de acceso no presupone que exista una inclusión ni que ésta sea "
                    "incorrecta."
                ),
                source_fact_keys=access_sources,
                priority="primary",
                legal_basis=list(_DATA_RIGHTS_BASIS),
            )
        )

    inclusion_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            system_key,
            creditor_key,
            amount_key,
            due_key,
            inclusion_key,
            payment_demand_key,
            payment_channel_key,
            inclusion_warning_key,
            system_notice_key,
        ),
    )
    if inclusion_sources:
        amount_text = f"{amount} €" if _present(amount) else "pendiente de validar"
        due_text = str(due) if _present(due) else "pendiente de validar"
        arguments.append(
            legal_argument(
                facts_record,
                code="credit_file_inclusion_requirements",
                title="Requisitos de la inclusión en el sistema",
                body=(
                    "La licitud de la inclusión exige comprobar, entre otros "
                    "extremos, que los datos proceden del acreedor, que la deuda es "
                    "cierta, vencida, exigible e impagada, que el principal alcanza "
                    "el umbral legal, que existieron requerimiento de pago e "
                    "información previa y que el sistema notificó la inclusión con "
                    "sus garantías. El expediente refleja una cuantía de "
                    f"{amount_text} y un vencimiento {due_text}; RTM no presume el "
                    "cumplimiento ni el incumplimiento de los requisitos que no "
                    "consten documentados."
                ),
                source_fact_keys=inclusion_sources,
                priority="primary",
                legal_basis=[*_CREDIT_FILE_BASIS, *_DATA_RIGHTS_BASIS],
            )
        )

    accuracy_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            creditor_key,
            amount_key,
            disputed_key,
            procedure_key,
            procedure_number_key,
            response_key,
            solution_key,
        ),
    )
    if accuracy_sources:
        formal, _ = _formal_dispute(facts_record)
        dispute_text = (
            "consta una controversia formal que debe vincularse a esta deuda"
            if formal
            else (
                "consta una discrepancia pendiente de acreditar por una vía formal"
                if disputed is True
                else "no consta una controversia formal validada"
            )
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="credit_file_accuracy_rectification_and_restriction",
                title="Exactitud, rectificación y limitación del tratamiento",
                body=(
                    "Los datos inexactos deben rectificarse y, cuando se impugna su "
                    "exactitud, puede solicitarse la limitación mientras se verifica "
                    "la información. En este expediente "
                    f"{dispute_text}. La AEPD no sustituye al órgano competente para "
                    "decidir la existencia o cuantía de la deuda, por lo que deben "
                    "separarse la controversia sobre la obligación y el ejercicio "
                    "de derechos sobre los datos."
                ),
                source_fact_keys=accuracy_sources,
                priority="primary",
                legal_basis=[*_DATA_RIGHTS_BASIS, *_CREDIT_FILE_BASIS],
            )
        )

    suppression_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            system_key,
            creditor_key,
            amount_key,
            due_key,
            inclusion_key,
            paid_key,
            disputed_key,
            procedure_key,
            procedure_number_key,
            solution_key,
        ),
    )
    if suppression_sources:
        paid_text = (
            "la deuda figura pagada"
            if paid is True
            else "el pago no consta validado"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="credit_file_erasure_payment_and_retention",
                title="Supresión por pago, inexactitud o permanencia indebida",
                body=(
                    "La supresión debe acordarse cuando desaparezca el "
                    "incumplimiento o concurra otro fundamento aplicable. El pago "
                    "determina la eliminación de los datos relativos a esa deuda y "
                    "la permanencia no puede exceder el límite legal desde el "
                    f"vencimiento. En el expediente {paid_text}. RTM no declara "
                    "automáticamente superado el plazo de cinco años ni ordena la "
                    "baja sin comprobar el vencimiento, la deuda concreta y las "
                    "posibles excepciones."
                ),
                source_fact_keys=suppression_sources,
                priority="primary",
                legal_basis=[*_DATA_RIGHTS_BASIS, *_CREDIT_FILE_BASIS],
            )
        )

    responsibility_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            system_key,
            creditor_key,
            response_key,
            solution_key,
        ),
    )
    if responsibility_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="credit_file_joint_responsibility_and_escalation",
                title="Responsabilidad del acreedor y del sistema",
                body=(
                    "El acreedor debe garantizar la existencia y exactitud de la "
                    "deuda comunicada y el sistema debe atender los derechos sobre "
                    "el tratamiento. La solicitud debe dirigirse al responsable "
                    "adecuado y comunicarse al acreedor cuando proceda. Solo después "
                    "de ejercitar los derechos y revisar la respuesta o su ausencia "
                    "se valorará una reclamación ante la AEPD, que no resolverá el "
                    "fondo civil o contractual de la deuda."
                ),
                source_fact_keys=responsibility_sources,
                priority="secondary",
                legal_basis=[*_DATA_RIGHTS_BASIS, *_CREDIT_FILE_BASIS],
            )
        )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa.",
        )

    source_keys = validated_source_keys(
        facts_record,
        [
            *family_evidence_keys(family_record),
            *summary_keys,
            *(key for argument in arguments for key in argument.source_fact_keys),
        ],
    )

    missing = dedupe_missing(
        [
            *_required_missing(facts_record, route, systems),
            *_review_missing(facts_record, route),
            *fact_review_items(facts_record, prefix="credit_file"),
        ]
    )

    system_text = ", ".join(systems) if systems else "PENDIENTE DE IDENTIFICAR"
    if route == "access":
        destination = (
            f"RESPONSABLE DEL SISTEMA DE INFORMACIÓN CREDITICIA {system_text}"
        )
        document_type = (
            "EJERCICIO DEL DERECHO DE ACCESO A SISTEMA DE INFORMACIÓN CREDITICIA"
        )
        requested_outcomes = [
            "Confirmación de si se tratan datos personales del afectado.",
            (
                "Copia e información sobre deuda, acreedor, origen, cuantía, "
                "vencimiento, inclusión, conservación y destinatarios."
            ),
            (
                "Identificación de las vías para rectificar, limitar o suprimir "
                "los datos cuando proceda."
            ),
            "Confirmación escrita de inexistencia de datos, si no hubiera inclusión.",
        ]
        primary_strategy = (
            "Ejercer primero el derecho de acceso ante cada sistema seleccionado, "
            "obtener una respuesta completa y utilizarla para decidir si procede "
            "rectificación, limitación, supresión o reclamación posterior."
        )
    else:
        destination = (
            f"RESPONSABLE DEL SISTEMA DE INFORMACIÓN CREDITICIA {system_text}"
        )
        if _present(creditor):
            destination += f" — CON COMUNICACIÓN AL ACREEDOR {creditor}"
        document_type = (
            "EJERCICIO DE DERECHOS DE ACCESO, RECTIFICACIÓN, LIMITACIÓN Y/O SUPRESIÓN"
        )
        requested_outcomes = [
            "Acceso completo a la inclusión y a su trazabilidad documental.",
            "Rectificación de cualquier dato inexacto o incompleto.",
            (
                "Limitación del tratamiento mientras se verifica la exactitud o "
                "la controversia formal acreditada."
            ),
            (
                "Supresión de la inclusión cuando se acredite pago, inexistencia, "
                "error de identidad, falta de requisitos o permanencia indebida."
            ),
            "Comunicación de la rectificación o supresión a los destinatarios procedentes.",
        ]
        primary_strategy = (
            "Reconstruir la deuda y la inclusión; verificar acreedor, cuantía, "
            "vencimiento, requerimiento, información previa y notificación; "
            "ejercitar acceso y el derecho corrector respaldado por la prueba; y "
            "escalar a la AEPD solo tras revisar la respuesta del responsable."
        )

    if _present(solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(solution)}."
        )

    subject_parts = ["SISTEMA DE INFORMACIÓN CREDITICIA", system_text]
    if _present(creditor):
        subject_parts.append(f"acreedor {creditor}")
    if _present(amount):
        subject_parts.append(f"deuda {amount} €")

    risks = [
        (
            "El acceso debe dirigirse al responsable del sistema, no a la AEPD, "
            "que no conserva el contenido de estos ficheros."
        ),
        (
            "La AEPD no decide si una deuda existe o cuál es su cuantía; esa "
            "controversia debe plantearse ante el acreedor y la vía competente."
        ),
        (
            "La supresión del dato no extingue por sí sola la deuda ni impide las "
            "acciones de cobro que sean jurídicamente procedentes."
        ),
        (
            "Cada sistema requiere una solicitud separada y prueba de identidad o "
            "representación suficiente."
        ),
        (
            "Una eventual indemnización por daños exige un análisis separado de "
            "causalidad, perjuicio, vía y prueba."
        ),
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="fichero_solvencia",
        specialist="debt.credit_file",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una cuestión relativa a {system_text}: {_display(fact)}."
            if _present(fact)
            else "Se ha documentado una posible cuestión de información crediticia."
        ),
        client_goal=(
            "Conocer los datos tratados y, cuando la documentación lo justifique, "
            "obtener su rectificación, limitación o supresión."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            "Ejercitar derechos también frente al acreedor informante cuando proceda.",
            (
                "Plantear la controversia sobre la deuda ante consumo, arbitraje, "
                "órgano sectorial o jurisdicción competente."
            ),
            (
                "Acudir al Delegado de Protección de Datos o a la AEPD tras una "
                "respuesta insuficiente o la falta de contestación en plazo."
            ),
        ],
        requested_outcomes=requested_outcomes,
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, route),
        risks=risks,
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Documento de identidad del afectado y autorización del representante, si existe.",
            "Respuesta íntegra al derecho de acceso y datos de contacto del responsable.",
            "Identidad del acreedor y documento de origen de la deuda.",
            "Desglose del principal, conceptos, vencimiento, pagos y saldo.",
            "Requerimiento previo de pago y prueba de recepción.",
            "Información contractual o posterior sobre la posible inclusión.",
            "Notificación del sistema tras la inclusión y fecha de recepción.",
            "Prueba de pago, error de identidad o procedimiento formal que afecte a la deuda.",
            "Relación de destinatarios o consultas comunicables y medidas adoptadas.",
        ],
        created_by_component=(
            "debt.credit_file:"
            f"{DEBT_CREDIT_FILE_SPECIALIST_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
