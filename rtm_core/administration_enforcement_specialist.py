"""Especialista RTM para ``administration.apremio_recaudacion``.

Construye una Previa Jurídica conservadora frente a una providencia de apremio
o actuación recaudatoria equivalente. No reabre automáticamente el fondo de la
deuda, no elige por sí solo entre reposición y reclamación
económico-administrativa, no calcula plazos por calendario y no afirma una causa
de suspensión sin respaldo documental validado.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

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


ADMINISTRATION_ENFORCEMENT_SPECIALIST_VERSION = (
    "rtm_administration_enforcement_specialist_v1_0"
)

_GENERAL_ADMINISTRATIVE_BASIS = [
    (
        "Ley 39/2015, de 1 de octubre, artículos 35, 40, 97, 98 y 112, "
        "sin perjuicio del régimen especial de revisión aplicable."
    ),
]
_TAX_COLLECTION_BASIS = [
    (
        "Ley 58/2003, de 17 de diciembre, General Tributaria, artículos "
        "28, 62.5, 161, 165 y 167, cuando la deuda esté sometida a dicho régimen."
    ),
    (
        "Real Decreto 939/2005, de 29 de julio, Reglamento General de "
        "Recaudación, artículos 69 a 72, cuando resulte aplicable."
    ),
]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fold(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _decimal(value: Any) -> Optional[Decimal]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raw = str(value).strip().replace("€", "").replace("EUR", "").replace("eur", "")
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _required_missing(
    record: ValidatedFactsRecord,
) -> list[MissingItem]:
    groups = (
        (
            "enforcement_fact_missing",
            "Falta validar el hecho recaudatorio o la actuación ejecutiva notificada.",
            ("descripcion_hecho", "acto_administrativo"),
        ),
        (
            "enforcement_case_reference_missing",
            "Falta validar el número de expediente o referencia recaudatoria.",
            ("expediente_ref", "referencia_documento"),
        ),
        (
            "enforcement_authority_missing",
            "Falta validar el órgano o Administración que dicta la actuación.",
            ("organismo", "emisor_documento"),
        ),
        (
            "enforcement_subject_missing",
            "Falta validar la identidad documental de la persona obligada al pago.",
            ("administrado", "destinatario_documento"),
        ),
        (
            "enforcement_act_missing",
            "Falta validar que el documento es una providencia de apremio o actuación equivalente.",
            ("acto_administrativo", "tipo_documento"),
        ),
        (
            "enforcement_notification_missing",
            "Falta validar la fecha de notificación de la providencia o actuación.",
            ("fecha_notificacion",),
        ),
        (
            "enforcement_amount_missing",
            "Falta validar la deuda o el importe total exigido.",
            ("importe_exigido_eur", "principal_eur", "importe_reclamado_eur"),
        ),
    )
    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    deadline, _ = validated_value(record, "fecha_limite")
    if not _present(deadline):
        result.append(
            missing_item(
                "enforcement_deadline_missing",
                (
                    "Debe validarse la fecha límite exacta de pago y de "
                    "impugnación; el especialista no la calcula solo con la fecha "
                    "de notificación."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    appeal, _ = validated_value(record, "recurso_indicado")
    if not _present(appeal):
        result.append(
            missing_item(
                "enforcement_review_route_missing",
                (
                    "Debe identificarse el recurso o reclamación indicado en el "
                    "acto, el órgano competente y su plazo antes de congelar la Previa."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "enforcement_original_debt_review",
                (
                    "OPS debe incorporar la liquidación, sanción u obligación de "
                    "origen y comprobar su firmeza, exigibilidad y correspondencia "
                    "con la deuda apremiada."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "enforcement_original_notification_review",
                (
                    "Debe verificarse la notificación de la liquidación u obligación "
                    "de origen, con fecha, destinatario, contenido e intento o recepción."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "enforcement_payment_and_extinction_review",
                (
                    "Debe comprobarse si la deuda fue pagada, compensada, condonada "
                    "o extinguida total o parcialmente, y si existe prueba de ello."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "enforcement_suspension_requests_review",
                (
                    "Debe comprobarse la existencia y fecha de solicitudes de "
                    "aplazamiento, fraccionamiento, compensación o suspensión, así "
                    "como cualquier resolución dictada sobre ellas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "enforcement_limitation_review",
                (
                    "La prescripción solo puede valorarse después de reconstruir "
                    "el nacimiento de la deuda, vencimientos, interrupciones y "
                    "actuaciones notificadas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    resolution_outcome, _ = validated_value(record, "resolucion_sentido")
    if any(
        token in _fold(resolution_outcome)
        for token in ("anulad", "revocad", "dejado sin efecto", "estimad")
    ):
        result.append(
            missing_item(
                "enforcement_possible_annulment_review",
                (
                    "Consta una resolución que podría haber anulado, revocado o "
                    "dejado sin efecto el acto de origen; debe comprobarse antes "
                    "de continuar la recaudación."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    principal, _ = validated_value(record, "principal_eur")
    surcharge, _ = validated_value(record, "recargo_eur")
    total, _ = validated_value(record, "importe_exigido_eur")
    principal_decimal = _decimal(principal)
    surcharge_decimal = _decimal(surcharge)
    total_decimal = _decimal(total)
    if (
        principal_decimal is not None
        and surcharge_decimal is not None
        and total_decimal is not None
        and abs((principal_decimal + surcharge_decimal) - total_decimal)
        > Decimal("0.01")
    ):
        result.append(
            missing_item(
                "enforcement_total_breakdown_review",
                (
                    "El principal más el recargo no coincide con el total exigido; "
                    "deben identificarse intereses, costas, pagos, compensaciones "
                    "u otros componentes antes de aceptar la cuantía."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return result


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("/", "-"), raw.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _deadlines(
    record: ValidatedFactsRecord,
) -> list[Deadline]:
    deadline, deadline_key = validated_value(record, "fecha_limite")
    if _present(deadline) and deadline_key:
        parsed = _parse_datetime(deadline)
        if parsed is not None:
            return [
                Deadline(
                    label="Fecha límite indicada en la actuación recaudatoria",
                    due_at=parsed,
                    calculation_status="confirmed",
                    source_fact_keys=[deadline_key],
                    notes=[
                        (
                            "Es la fecha transcrita y validada del documento; OPS "
                            "debe confirmar qué actuación vence en ella."
                        )
                    ],
                )
            ]
        return [
            Deadline(
                label="Fecha límite indicada en la actuación recaudatoria",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[deadline_key],
                notes=[
                    "El documento contiene una fecha, pero no pudo convertirse con seguridad."
                ],
            )
        ]

    notification, notification_key = validated_value(record, "fecha_notificacion")
    notes = [
        (
            "No se calcula automáticamente el plazo porque depende del régimen "
            "aplicable, del tipo de actuación, de la fecha efectiva de notificación "
            "y del calendario de días hábiles."
        )
    ]
    if _present(notification) and notification_key:
        notes.append(f"Fecha de notificación validada: {notification}.")
    return [
        Deadline(
            label="Plazo de pago y de impugnación",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[],
            notes=notes,
        )
    ]


def build_administration_enforcement_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="administration",
        family="apremio_recaudacion",
        specialist="administration.enforcement",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    act, act_key = validated_value(
        facts_record,
        "acto_administrativo",
        "tipo_documento",
    )
    case_ref, case_ref_key = validated_value(
        facts_record,
        "expediente_ref",
        "referencia_documento",
    )
    authority, authority_key = validated_value(
        facts_record,
        "organismo",
        "emisor_documento",
    )
    subject_person, subject_person_key = validated_value(
        facts_record,
        "administrado",
        "destinatario_documento",
    )
    notification, notification_key = validated_value(
        facts_record,
        "fecha_notificacion",
    )
    deadline, deadline_key = validated_value(facts_record, "fecha_limite")
    principal, principal_key = validated_value(facts_record, "principal_eur")
    surcharge, surcharge_key = validated_value(facts_record, "recargo_eur")
    total, total_key = validated_value(facts_record, "importe_exigido_eur")
    norm, norm_key = validated_value(facts_record, "norma")
    article, article_key = validated_value(facts_record, "articulo")
    appeal, appeal_key = validated_value(facts_record, "recurso_indicado")
    procedure_type, procedure_type_key = validated_value(
        facts_record,
        "procedimiento_tipo",
    )
    documented_response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
        "resolucion_sentido",
    )

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("organismo", "Organismo", ""),
            ("administrado", "Persona obligada", ""),
            ("expediente_ref", "Expediente", ""),
            ("acto_administrativo", "Acto", ""),
            ("procedimiento_tipo", "Procedimiento", ""),
            ("fecha_notificacion", "Notificación", ""),
            ("fecha_limite", "Fecha límite", ""),
            ("principal_eur", "Principal", " €"),
            ("recargo_eur", "Recargo", " €"),
            ("importe_exigido_eur", "Total exigido", " €"),
            ("norma", "Norma indicada", ""),
            ("articulo", "Artículo indicado", ""),
            ("recurso_indicado", "Recurso indicado", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {fact}.")
        if fact_key:
            summary_keys.insert(0, fact_key)

    arguments = []

    title_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            act_key,
            case_ref_key,
            authority_key,
            subject_person_key,
            principal_key,
            total_key,
        ),
    )
    if title_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="enforcement_title_debt_and_subject_identification",
                title="Título ejecutivo e identificación de la deuda y del obligado",
                body=(
                    "La actuación recaudatoria debe enlazarse con una obligación "
                    "ejecutiva concreta y permitir identificar sin ambigüedad al "
                    "obligado, el concepto, el periodo, el principal y el acto del "
                    "que procede. OPS debe contrastar que la providencia coincide "
                    "con la liquidación, sanción u obligación originaria y que no "
                    "existen errores u omisiones que impidan identificar la deuda "
                    "o a su destinatario."
                ),
                source_fact_keys=title_sources,
                priority="primary",
                legal_basis=[
                    *_GENERAL_ADMINISTRATIVE_BASIS,
                    *_TAX_COLLECTION_BASIS,
                ],
            )
        )

    notification_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            act_key,
            notification_key,
            case_ref_key,
            response_key,
        ),
    )
    if notification_sources:
        notification_text = (
            str(notification)
            if _present(notification)
            else "pendiente de validar"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="enforcement_original_act_and_notification",
                title="Existencia y notificación válida del acto de origen",
                body=(
                    "Antes de mantener el apremio debe acreditarse la existencia "
                    "del acto que fija la deuda y su notificación íntegra al "
                    "interesado, incluida la información sobre recursos y plazos. "
                    f"La actuación analizada refleja una notificación {notification_text}; "
                    "debe cotejarse con la liquidación u obligación originaria, su "
                    "contenido, destinatario, canal y constancia de recepción."
                ),
                source_fact_keys=notification_sources,
                priority="primary",
                legal_basis=[
                    *_GENERAL_ADMINISTRATIVE_BASIS,
                    *_TAX_COLLECTION_BASIS,
                ],
            )
        )

    opposition_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            act_key,
            case_ref_key,
            notification_key,
            appeal_key,
            procedure_type_key,
            response_key,
        ),
    )
    if opposition_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="enforcement_statutory_opposition_grounds",
                title="Motivos legalmente admisibles de oposición al apremio",
                body=(
                    "Cuando resulte aplicable el régimen tributario, la oposición "
                    "a la providencia de apremio queda limitada a las causas "
                    "legalmente previstas: extinción o prescripción; solicitudes "
                    "presentadas en periodo voluntario u otras causas de suspensión; "
                    "falta de notificación o anulación de la liquidación; y errores "
                    "u omisiones que impidan identificar al deudor o la deuda. "
                    "RTM no selecciona automáticamente ninguna de estas causas: OPS "
                    "debe vincular cada alegación a documentos validados."
                ),
                source_fact_keys=opposition_sources,
                priority="primary",
                legal_basis=list(_TAX_COLLECTION_BASIS),
            )
        )

    amount_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            principal_key,
            surcharge_key,
            total_key,
            notification_key,
            deadline_key,
        ),
    )
    if amount_sources:
        principal_text = (
            f"{principal} €" if _present(principal) else "pendiente de validar"
        )
        surcharge_text = (
            f"{surcharge} €" if _present(surcharge) else "pendiente de validar"
        )
        total_text = f"{total} €" if _present(total) else "pendiente de validar"
        arguments.append(
            legal_argument(
                facts_record,
                code="enforcement_surcharge_and_amount_breakdown",
                title="Recargo, intereses, costas y total exigido",
                body=(
                    "La providencia debe desglosar el principal y el recargo del "
                    "periodo ejecutivo y permitir comprobar cualquier interés o "
                    "costa adicional. Los hechos validados reflejan un principal "
                    f"de {principal_text}, un recargo de {surcharge_text} y un total "
                    f"de {total_text}. No se acepta automáticamente el porcentaje "
                    "ni el total: deben verificarse el momento de inicio del periodo "
                    "ejecutivo, los pagos realizados y el régimen aplicable."
                ),
                source_fact_keys=amount_sources,
                priority="secondary",
                legal_basis=list(_TAX_COLLECTION_BASIS),
            )
        )

    suspension_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            act_key,
            notification_key,
            deadline_key,
            appeal_key,
            response_key,
        ),
    )
    if suspension_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="enforcement_suspension_and_immediate_protection",
                title="Suspensión y protección frente a actuaciones ejecutivas",
                body=(
                    "La mera impugnación no debe tratarse como suspensión automática. "
                    "Debe comprobarse el régimen específico, la solicitud formulada, "
                    "las garantías exigibles y cualquier causa de suspensión sin "
                    "garantía que resulte acreditada. Dado que el impago tras el "
                    "plazo puede conducir a embargo, OPS debe decidir de inmediato "
                    "si procede solicitar suspensión, aplazamiento, fraccionamiento "
                    "u otra medida compatible con la estrategia de fondo."
                ),
                source_fact_keys=suspension_sources,
                priority="secondary",
                legal_basis=[
                    *_GENERAL_ADMINISTRATIVE_BASIS,
                    *_TAX_COLLECTION_BASIS,
                ],
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
            *_required_missing(facts_record),
            *_review_missing(facts_record),
            *fact_review_items(facts_record, prefix="enforcement"),
        ]
    )

    destination = (
        str(authority).strip()
        if _present(authority)
        else "ÓRGANO DE RECAUDACIÓN PENDIENTE DE VALIDAR"
    )
    subject_parts = ["IMPUGNACIÓN DE ACTUACIÓN DE APREMIO"]
    if _present(case_ref):
        subject_parts.append(f"expediente {case_ref}")
    if _present(total):
        subject_parts.append(f"importe {total} €")
    subject = " — ".join(subject_parts)

    risks = [
        (
            "Los motivos de oposición a una providencia de apremio pueden estar "
            "legalmente tasados; discutir solo el fondo de la deuda puede ser "
            "insuficiente o improcedente en esta fase."
        ),
        (
            "La impugnación no equivale por sí sola a suspensión y pueden continuar "
            "las actuaciones recaudatorias si no se obtiene la medida adecuada."
        ),
        (
            "No se ha calculado ningún plazo ni porcentaje de recargo fuera de los "
            "datos documentales validados."
        ),
        (
            "Debe determinarse si la deuda es tributaria, sancionadora, local u otra "
            "de Derecho público antes de fijar definitivamente la vía de revisión."
        ),
    ]
    if _present(documented_response):
        risks.append(
            "Existe una respuesta o resolución documentada que debe valorarse íntegramente."
        )
    if _present(norm) or _present(article):
        risks.append(
            "La referencia normativa transcrita debe verificarse en su redacción aplicable."
        )

    goal = (
        "Obtener la anulación, rectificación o suspensión de la actuación "
        "recaudatoria cuando concurra una causa documentada; subsidiariamente, "
        "evitar recargos o actuaciones ejecutivas indebidas y ordenar la forma de pago."
    )
    primary_strategy = (
        "Reconstruir la deuda de origen, su notificación, el inicio del periodo "
        "ejecutivo, el desglose económico y las solicitudes o resoluciones que "
        "puedan afectar al apremio; después elegir únicamente una vía y motivos "
        "de oposición compatibles con el régimen aplicable."
    )
    if _present(appeal):
        primary_strategy += f" El documento indica como vía: {appeal}."

    problem_summary = (
        f"Se ha documentado una actuación de apremio: {fact}."
        if _present(fact)
        else (
            f"Se ha documentado una actuación recaudatoria: {act}."
            if _present(act)
            else "La actuación recaudatoria requiere completar sus hechos documentales."
        )
    )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="apremio_recaudacion",
        specialist="administration.enforcement",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=problem_summary,
        client_goal=goal,
        primary_strategy=primary_strategy,
        secondary_strategies=[
            (
                "Solicitar acceso y copia íntegra del expediente recaudatorio, "
                "incluidas liquidación, notificaciones y justificantes."
            ),
            (
                "Si no existe causa de oposición suficiente, valorar pago dentro "
                "del plazo, aplazamiento, fraccionamiento o compensación sin "
                "confundir esas opciones con una estimación del recurso."
            ),
            (
                "Si ya existe embargo, separar la impugnación de la providencia de "
                "los motivos específicos admisibles contra la diligencia de embargo."
            ),
        ],
        requested_outcomes=[
            (
                "Anulación o archivo del apremio cuando se acredite una causa "
                "legalmente admisible."
            ),
            (
                "Suspensión de las actuaciones ejecutivas cuando se cumplan los "
                "requisitos del régimen aplicable."
            ),
            (
                "Rectificación del principal, recargo, intereses o costas que no "
                "estén correctamente identificados o calculados."
            ),
            (
                "Acceso al expediente y aportación de la liquidación, notificaciones "
                "y antecedentes recaudatorios."
            ),
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=risks,
        destination=destination,
        document_type=(
            "RECURSO U OPOSICIÓN FRENTE A ACTUACIÓN DE APREMIO "
            "— VÍA PENDIENTE DE VALIDAR"
        ),
        subject=subject,
        legal_arguments=arguments,
        additional_requests=[
            "Liquidación, sanción u obligación de origen y justificante de notificación.",
            "Providencia de apremio completa, con fecha, firma o identificación del órgano.",
            "Desglose de principal, recargo, intereses, costas y pagos aplicados.",
            "Historial de aplazamientos, fraccionamientos, compensaciones y suspensiones.",
            "Indicación de recursos, órgano competente, plazo y efectos sobre la ejecución.",
            "Expediente recaudatorio íntegro e índice de documentos y actuaciones.",
        ],
        created_by_component=(
            "administration.enforcement:"
            f"{ADMINISTRATION_ENFORCEMENT_SPECIALIST_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
