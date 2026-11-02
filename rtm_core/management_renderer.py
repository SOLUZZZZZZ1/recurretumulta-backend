"""Pure reviewed-text renderer reused verbatim from October generation_gateway.
Source commit: a983c6ea34c02eb5e6e47031db4d4695d3338478.
Only presentation functions are shared. Production authorization, paid-contract,
source integrity and review gates live in management_drafts; no historical guard
has been removed or bypassed. This renderer calls no AI and invents no facts.
"""
import re
from typing import Mapping, Any
from fastapi import HTTPException

def _value(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _clean_text(value: str) -> str:
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def render_legal_preview(preview, case_meta: Mapping[str, Any]) -> str:
    """Render determinista: usa exclusivamente previa + identidad persistida."""

    interested = case_meta.get("interested_data")
    if not isinstance(interested, dict):
        interested = {}
    full_name = _value(interested, "full_name", "name")
    dni = _value(interested, "dni_nie", "dni", "identity_number")
    address = _value(interested, "domicilio_notif", "domicilio", "address")
    if not full_name or not dni or not address:
        raise HTTPException(status_code=409, detail="Faltan datos de identidad para redactar")

    destination = _clean_text(preview.destination or "").upper()
    document_type = _clean_text(preview.document_type or "ESCRITO").upper()
    subject = _clean_text(
        preview.subject
        or f"{document_type} — {case_meta.get('expediente_ref') or preview.family}"
    )

    lines: list[str] = [
        destination,
        "",
        document_type,
        "",
        f"ASUNTO: {subject}",
        "",
        (
            f"D./Dña. {full_name}, con DNI/NIE {dni}, y domicilio a efectos de "
            f"notificaciones en {address}, comparece y, como mejor proceda, EXPONE:"
        ),
        "",
        "I. ANTECEDENTES Y HECHOS",
        "",
    ]
    if preview.problem_summary:
        lines.extend([_clean_text(preview.problem_summary), ""])
    for index, fact in enumerate(preview.validated_facts_summary, start=1):
        lines.append(f"{index}) {_clean_text(fact)}")
    lines.extend(["", "II. ALEGACIONES Y FUNDAMENTOS", ""])
    lines.extend([
        "ENFOQUE PRINCIPAL",
        _clean_text(preview.primary_strategy or ""),
        "",
    ])

    for index, argument in enumerate(preview.legal_arguments, start=1):
        lines.append(f"ALEGACIÓN {index} — {_clean_text(argument.title).upper()}")
        lines.append("")
        lines.append(_clean_text(argument.body))
        if argument.legal_basis:
            lines.append("")
            lines.append("Fundamentos normativos indicados en la previa:")
            for basis in argument.legal_basis:
                lines.append(f"• {_clean_text(basis)}")
        lines.append("")

    if preview.secondary_strategies:
        lines.extend(["ENFOQUES SUBSIDIARIOS", ""])
        for index, strategy in enumerate(preview.secondary_strategies, start=1):
            lines.append(f"{index}) {_clean_text(strategy)}")
        lines.append("")

    lines.extend(["III. SOLICITA", ""])
    for index, outcome in enumerate(preview.requested_outcomes, start=1):
        lines.append(f"{index}) {_clean_text(outcome)}")

    if preview.additional_requests:
        lines.extend(["", "OTROSÍ DIGO", ""])
        for index, request in enumerate(preview.additional_requests, start=1):
            lines.append(f"{index}) {_clean_text(request)}")

    lines.extend(["", "Por ser conforme a Derecho, se solicita."])
    return _clean_text("\n".join(lines)) + "\n"
