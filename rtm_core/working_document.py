"""Read-only working document from existing observations and reviewed facts.

This projection has no legal authority. It never calls an AI provider, promotes
facts, creates a LegalPreview, stores a resource, or changes the case state.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from case_authority import verify_signed_case_authority
from pdf_builder import build_pdf
from rtm_core import authority_repository as authority
from rtm_core import reanalysis_adapter as adapter
from rtm_core.repository import load_case_review_snapshot

VERSION = "rtm_working_document_v1"
LABELS = {
    "organismo": "Organismo", "expediente_ref": "Referencia", "matricula": "Matrícula",
    "fecha_documento": "Fecha del documento", "fecha_infraccion": "Fecha del hecho",
    "hora_infraccion": "Hora", "lugar_infraccion": "Lugar",
    "hecho_denunciado_literal": "Hecho que figura en el documento",
    "tipo_documento": "Tipo de documento", "fase_procedimental": "Fase",
    "titulo_documento": "Título del documento", "radar_modelo_hint": "Modelo de radar",
    "velocidad_medida_kmh": "Velocidad consignada", "velocidad_limite_kmh": "Límite consignado",
    "sancion_importe_eur": "Importe de sanción", "fecha_notificacion": "Fecha de recepción",
    "norma_hint": "Norma indicada", "articulo_infringido_num": "Artículo",
    "apartado_infringido_num": "Apartado", "puntos_detraccion": "Puntos",
    "document_subject_name": "Nombre en el documento", "document_subject_id": "Identificador en el documento",
    "full_name": "Nombre aportado", "dni_nie": "Identificador aportado",
    "domicilio_notif": "Domicilio aportado",
}
_HUMAN_SOURCE = "operator_document_review"
_MISSING_VALUES = {"none", "null", "nan", "undefined", "unknown", "desconocido", "n/a", "sin dato", "no consta"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _normal(value: Any) -> str:
    value = unicodedata.normalize("NFKD", "" if value is None else str(value))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _missing_observation(value: Any) -> bool:
    return not _present(value) or (isinstance(value, str) and value.strip().casefold() in _MISSING_VALUES)


def _marker(key: str, value: Any) -> str:
    # Exact concordance preserves meaningful letters (Peña is not Pena).
    normal = " ".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).casefold().split())
    if key in {"dni_nie", "document_subject_id"}:
        # Match final-generation identity checks without dropping meaningful
        # letters or leading zeros. Only formatting separators are ignored.
        normal = re.sub(r"[\s.\-]", "", normal)
    elif key == "matricula":
        normal = re.sub(r"[\s-]", "", normal)
    return normal


def _display(value: Any) -> str:
    if value is None:
        return "[pendiente]"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    return " ".join(str(value).replace("\x00", "").split())[:4000]


def _source(source: dict) -> dict:
    # A JSON observation envelope or a candidate note is not a documentary quote.
    evidence = source.get("evidence")
    quoted = isinstance(evidence, str) and bool(evidence.strip()) and not (
        evidence.lstrip().startswith(("{", "[")) or "candidate_only" in evidence
        or evidence.startswith("Lectura candidata no consolidada"))
    return {"document_id": source.get("document_id"), "page_index": source.get("page_index"),
        "evidence": evidence if quoted else None,
        "evidence_kind": "document_excerpt" if quoted else None,
        "source_type": source.get("source_type"), "extraction_method": source.get("extraction_method")}


def _observations(wrapper: dict, event: dict) -> dict[str, list]:
    """Use the existing structured adapter; never reconstruct values from notes."""
    core = adapter._mapping(wrapper.get("extracted"))
    result: dict[str, list] = {}
    for method, values, confidence, evidence, version in adapter._SOURCE_SPECS:
        for payload in (event, core):
            for candidate in adapter._source_candidates(payload, method=method, values_key=values,
                    confidence_key=confidence, evidence_key=evidence, version_key=version):
                if not _missing_observation(candidate.value):
                    result.setdefault(candidate.key, []).append(candidate)
    for key, value, raw_key, _ in adapter._flatten_values(core):
        if key and not _missing_observation(value):
            result.setdefault(key, []).append(adapter._Candidate(key=key, value=value, confidence=0.0,
                evidence=None, method="reanalysis_core", version=core.get("extractor_version"),
                source_key=raw_key, priority=10))
    return result


def _anchor_saved_reading(wrapper: dict, key: str, value: Any) -> dict | None:
    """Locate an existing value in one explicitly mapped saved page, without OCR.

    This is evidence for a proposal, not proof that the model read the original
    correctly. Ambiguous pages and unsupported numeric contexts stay unresolved.
    """
    raw = adapter._mapping(wrapper.get("extracted")).get("raw_text_blob")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 250_000:
        return None
    page_map = {}
    for page in wrapper.get("pages") or []:
        if not isinstance(page, dict) or type(page.get("page_index")) is not int:
            continue
        index = page["page_index"]
        if index < 1 or not page.get("document_id"):
            continue
        page_map.setdefault(index, set()).add(str(page["document_id"]))
    pieces = re.split(r"(?im)^=+\s*P[ÁA]GINA\s+(\d+)\s*=+[^\S\n]*$", raw)
    if len(pieces) < 3:
        return None
    displayed = _display(value)
    if isinstance(value, bool) or len(displayed) < 2:
        return None
    escaped = r"\s+".join(re.escape(part) for part in displayed.split())
    if key in {"velocidad_medida_kmh", "velocidad_limite_kmh"}:
        pattern = rf"(?<!\w){escaped}\s*km\s*/?\s*h(?!\w)"
    elif key == "articulo_infringido_num":
        pattern = rf"\b(?:art[íi]culo|art\.)\s*{escaped}(?!\w)"
    elif isinstance(value, (int, float)) or displayed.isdigit():
        return None
    else:
        pattern = rf"(?<!\w){escaped}(?!\w)"
    # Repeated values can play different roles (reference versus antenna ID).
    # Prefer an explicit field label, never a guessed occurrence or a fixture ID.
    context_pattern = None
    if key in {"expediente_ref", "matricula"}:
        label = r"(?:expediente|referencia)" if key == "expediente_ref" else r"matr[íi]cula"
        context_pattern = rf"(?m)^[^\n]{{0,30}}\b{label}\b[^\n]{{0,80}}(?:\n[ \t]*)?{escaped}(?!\w)"
    elif key == "velocidad_medida_kmh":
        context_pattern = rf"\b(?:circular\s+a|velocidad\s+(?:medida|registrada|consignada)\s*[:·]?)\s*{escaped}\s*km\s*/?\s*h(?!\w)"
    if context_pattern and re.search(context_pattern, raw, re.IGNORECASE):
        pattern = context_pattern
    matches = []
    for offset in range(1, len(pieces), 2):
        index, page_text = int(pieces[offset]), pieces[offset + 1]
        documents = page_map.get(index, set())
        if len(documents) != 1:
            continue
        for match in re.finditer(pattern, page_text, re.IGNORECASE):
            start = max(page_text.rfind("\n", 0, match.start()) + 1, match.start() - 100)
            end = page_text.find("\n", match.end())
            if end < 0:
                end = len(page_text)
            quote = page_text[start:min(end, match.end() + 140)].strip()
            matches.append({"document_id": next(iter(documents)), "page_index": index - 1,
                "evidence": quote, "evidence_kind": "document_excerpt",
                "source_type": "stored_document_reading", "extraction_method": "rtm_stored_text_anchor_v1"})
    # Multiple occurrences or pages can have different roles; do not choose one.
    return matches[0] if len(matches) == 1 else None


def _issue(code: str, message: str, keys=(), severity="review") -> dict:
    return {"code": code, "severity": severity, "message": message, "field_keys": list(keys)}


def compose_working_document(*, case_id: str, identity: dict, wrapper: dict, event: dict,
        facts_record=None, excluded_fields=(), documents=()) -> dict:
    """Pure, deterministic proposal; reviewed facts and exclusions take precedence."""
    observations = _observations(wrapper, event)
    document_ids, pages = adapter._document_sources(wrapper)
    current = facts_record.facts.facts if facts_record else {}
    excluded = set(excluded_fields)
    core = adapter._mapping(wrapper.get("extracted"))
    issuer_withdrawn = any(
        adapter._mapping(payload.get("traffic_generic_facts")).get("issuer_status") == "unresolved"
        and _present(adapter._mapping(payload.get("traffic_generic_facts")).get("legacy_issuer_rejected"))
        for payload in (core, event))
    for payload in (core, event):
        for entry in payload.get("critical_conflicts_resolved") or []:
            if isinstance(entry, dict) and entry.get("source") == "current_run_no_explicit_issuer":
                issuer_withdrawn = True
    fields, issues = [], []
    declared_keys = {"matricula"} if not _missing_observation(identity.get("matricula")) else set()
    for key in sorted((set(observations) | set(current) | declared_keys) - excluded):
        if key not in LABELS:
            continue
        fact = current.get(key)
        choices = sorted(observations.get(key, []),
                         key=lambda c: (c.confidence, c.priority, bool(c.evidence)), reverse=True)
        distinct = {_marker(key, c.value) for c in choices}
        reviewed = bool(fact and fact.status.value == "validated" and not fact.conflicts
                        and not _missing_observation(fact.value))
        conflict = bool(fact and (fact.status.value == "conflicted" or fact.conflicts))
        if fact and fact.status.value == "rejected":
            continue
        if reviewed:
            value = fact.value
            status = "reviewed" if any(s.source_type == _HUMAN_SOURCE for s in fact.sources) else "verified"
            sources = [_source(s.model_dump(mode="json")) for s in fact.sources]
            if any(v != _marker(key, value) for v in distinct):
                issues.append(_issue("older_reading_differs_" + key,
                    f"La lectura guardada difiere de {LABELS[key].lower()} registrado; se conserva la versión vigente.", [key]))
        elif key == "organismo" and issuer_withdrawn:
            value, status, sources = None, "missing", []
            issues.append(_issue("issuer_not_confirmed_in_current_reading",
                "El organismo de una lectura anterior no quedó confirmado en la lectura actual. Falta contrastarlo con el original.",
                [key], "blocking"))
        elif choices:
            chosen = choices[0]
            value, status = chosen.value, "conflict" if conflict or len(distinct) > 1 else "candidate"
            sources = [_source(s.model_dump(mode="json")) for s in
                       adapter._source_references(document_ids, pages, chosen)]
            if not any(s["evidence"] for s in sources):
                anchor = _anchor_saved_reading(wrapper, key, value)
                if anchor:
                    sources = [anchor]
        elif key == "matricula" and not _missing_observation(identity.get(key)):
            value, status, sources = identity[key], "declared", []
        else:
            value, status, sources = None, "conflict" if conflict else "missing", []
        if status == "conflict":
            issues.append(_issue("conflicting_" + key,
                f"Hay lecturas incompatibles de {LABELS[key].lower()}; no se afirma ese dato en el escrito.", [key]))
        matches = []
        declared_key = {"document_subject_name": "full_name", "document_subject_id": "dni_nie"}.get(key, key)
        declared = identity.get(declared_key)
        if key in {"matricula", "document_subject_name", "document_subject_id"} and status != "declared" and _present(declared) and _present(value):
            if _marker(key, declared) == _marker(key, value):
                matches.append({"source": "client_declaration", "value": declared, "rule": "normalized_exact_v1"})
            else:
                status = "conflict"
                issues.append(_issue("declared_value_differs_" + key,
                    f"{LABELS[key]}: lectura o versión vigente «{_display(value)[:160]}»; dato aportado «{_display(declared)[:160]}». Hay que resolver la diferencia.", [key], "blocking"))
        fields.append({"key": key, "label": LABELS[key], "value": value, "status": status,
                       "sources": sources, "matches": matches})
    for key in ("full_name", "dni_nie", "domicilio_notif"):
        value = identity.get(key)
        fields.append({"key": key, "label": LABELS[key], "value": value if not _missing_observation(value) else None,
            "status": "declared" if not _missing_observation(value) else "missing", "sources": [], "matches": []})

    by_key = {field["key"]: field for field in fields}
    identity_keys = ("document_subject_name", "document_subject_id")
    identity_mismatch = any(issue["code"] in {"declared_value_differs_" + key for key in identity_keys}
                            for issue in issues)
    case_mismatch = identity_mismatch or any(issue["code"] == "declared_value_differs_matricula" for issue in issues)
    matched_identity = [key for key in identity_keys if by_key.get(key, {}).get("matches")]
    if case_mismatch:
        # Preserve the two values in the comparison, but never print either as
        # the claimant for a case whose documentary identity is inconsistent.
        for key in ("full_name", "dni_nie", "domicilio_notif"):
            by_key[key]["status"] = "conflict"
    identity_concordance = {"status": "mismatch" if identity_mismatch else
        "concordant" if len(matched_identity) == 2 else "incomplete", "matched_fields": matched_identity}
    if identity_concordance["status"] == "incomplete":
        issues.append(_issue("document_identity_not_checked",
            "La identidad que figura en el documento sigue pendiente de comprobar. Los datos del interesado proceden del formulario.",
            list(identity_keys)))
    def value(key):
        field = by_key.get(key)
        return field["value"] if field and field["status"] not in {"conflict", "missing"} else None
    phase_values = [value(k) for k in ("tipo_documento", "fase_procedimental", "titulo_documento")]
    # Only documentary reading fields supply fallback context, never a legacy family or strategy.
    generic_phases = {"otro", "other", "unknown", "desconocido", "unresolved", "generic", "pendiente", "sin determinar"}
    phase_text = " ".join(_normal(v).replace("_", " ") for v in phase_values if v and _normal(v) not in generic_phases)
    raw_context = _normal(core.get("raw_text_blob") or core.get("vision_raw_text") or "")
    identification = bool(re.search(r"identifica(?:cion|r).{0,90}conductor|datos.{0,35}titular.{0,90}conductor", phase_text))
    incompatible_phase = bool(re.search(r"resolucion|resolution|apremio|ejecutiva|requerimiento(?: de)? pago|payment requirement|enforcement|\bfirme\b", phase_text))
    title_is_identification = "peticion de datos al titular" in phase_text
    if not identification and not incompatible_phase and not (
            {"tipo_documento", "fase_procedimental", "titulo_documento"} & excluded):
        raw_identification = bool(re.search(r"peticion de datos al titular|requerimiento.{0,70}identifica.{0,50}conductor", raw_context))
        identification = raw_identification or (title_is_identification and "conductor" in raw_context)
    phase_conflict = any(by_key.get(k, {}).get("status") == "conflict" for k in
                         ("tipo_documento", "fase_procedimental", "titulo_documento"))
    if identification and incompatible_phase:
        phase_conflict = True
        issues.append(_issue("incompatible_document_phase",
            "La lectura mezcla identificación con otra fase del procedimiento. Hay que contrastar el documento antes de elegir la actuación.",
            ["tipo_documento", "fase_procedimental"], "blocking"))
    if phase_conflict or case_mismatch:
        identification = False
    literal = value("hecho_denunciado_literal")
    speed = value("velocidad_medida_kmh")
    speed_signal = bool(_present(speed) or re.search(r"\b\d+\s*km/?h\b|cinemometro|radar", raw_context))
    if literal and speed_signal and re.search(r"semafor|luz roja", _normal(literal)):
        by_key["hecho_denunciado_literal"]["status"] = "conflict"
        issues.append(_issue("conduct_inconsistent_with_speed",
            "La lectura del hecho menciona un semáforo y también hay datos de velocidad. El borrador deja el hecho pendiente de contraste.",
            ["hecho_denunciado_literal", "velocidad_medida_kmh"], "blocking"))
        literal = None
    if not literal and ("hecho_denunciado_literal" in by_key):
        issues.append(_issue("conduct_pending", "El hecho literal sigue pendiente de contraste con el original.", ["hecho_denunciado_literal"]))

    document_kind = "driver_identification" if identification else "undetermined"
    title = ("Revisión de correspondencia entre documento y expediente" if case_mismatch else
             "Borrador de contestación al requerimiento de identificación"
             if identification else "Actuación pendiente de determinar")
    header = ["BORRADOR — PENDIENTE DE REVISIÓN", title,
        "Los datos de la lectura guardada siguen siendo propuestas; este borrador no confirma hechos ni contiene una aprobación.",
        *(["Identidad del documento pendiente de comprobar; los datos personales se muestran según el formulario."]
          if identity_concordance["status"] == "incomplete" else []),
        "", f"AL ORGANISMO: {_display(value('organismo'))}",
        f"REFERENCIA: {_display(value('expediente_ref'))}", "",
        f"Interesado/a (dato aportado): {_display(value('full_name'))}",
        f"Identificador (dato aportado): {_display(value('dni_nie'))}",
        f"Domicilio (dato aportado): {_display(value('domicilio_notif'))}", ""]
    if identification:
        body = ["I. ANTECEDENTES",
            f"Se prepara contestación a la petición de identificación relativa al vehículo con matrícula {_display(value('matricula'))} y referencia {_display(value('expediente_ref'))}.",
            "La petición solicita identificar a la persona que conducía. Los datos del interesado aportados al expediente no determinan quién conducía."]
        details = [f"{LABELS[k]}: {_display(value(k))}" for k in
                   ("fecha_infraccion", "hora_infraccion", "lugar_infraccion") if _present(value(k))]
        if details:
            body.append("En la lectura disponible figuran: " + "; ".join(details) + ".")
        body.append(f"Hecho consignado en la lectura, pendiente de contraste: {_display(literal)}")
        body.extend(["", "II. CONTESTACIÓN POR COMPLETAR",
            "Persona que conducía: [PENDIENTE DE APORTAR Y CONFIRMAR].",
            "Datos de identificación exigidos en el requerimiento: [PENDIENTES DE COMPLETAR CON LA INFORMACIÓN DE LA PERSONA CONDUCTORA].",
            "", "III. CIERRE PENDIENTE",
            "Completar los datos solicitados en el documento original y revisar la contestación antes de su firma o presentación."])
        issues.append(_issue("driver_identity_missing", "Falta indicar y confirmar quién conducía para completar la contestación.", [], "blocking"))
    elif case_mismatch:
        body = ["CORRESPONDENCIA POR RESOLVER",
            "Hay diferencias entre el documento recibido y los datos del expediente. No se prepara una contestación en nombre de una persona hasta resolverlas.", ""]
        body.extend(issue["message"] for issue in issues if issue["code"].startswith("declared_value_differs_"))
        issues.append(_issue("case_document_mismatch",
            "La propuesta de actuación está detenida hasta aclarar a quién y a qué vehículo corresponde el documento.",
            ["document_subject_name", "document_subject_id", "matricula"], "blocking"))
    else:
        body = ["DOCUMENTO RECIBIDO",
            f"La lectura disponible corresponde a: {_display(value('tipo_documento') or value('titulo_documento'))}.",
            f"Fase indicada: {_display(value('fase_procedimental'))}.",
            f"Vehículo: {_display(value('matricula'))}. Referencia: {_display(value('expediente_ref'))}.",
            "", "REDACCIÓN PENDIENTE",
            "Todavía no hay un redactor temprano compatible con esta fase documental. Hay que determinar la actuación y revisar sus hechos antes de preparar el escrito."]
        issues.append(_issue("unsupported_document_phase",
            "Hay que determinar el tipo de actuación; este borrador no propone unas alegaciones genéricas.",
            ["tipo_documento", "fase_procedimental"], "blocking"))
    if any(f["status"] == "candidate" for f in fields):
        issues.append(_issue("documentary_readings_pending",
            "Se reutiliza la lectura guardada. Las propuestas pueden contrastarse junto al escrito; no se han confirmado automáticamente.", severity="info"))
    content = "\n".join(header + body) + "\n"
    result = {"ok": True, "case_id": case_id, "version": VERSION, "status": "working_draft",
        "persisted": False, "can_save": False, "latest_id": None, "history": [],
        "final_resource_generated": False, "document_kind": document_kind, "title": title,
        "content": content, "fields": fields, "issues": issues,
        "identity_concordance": identity_concordance,
        "facts_id": facts_record.id if facts_record else None,
        "facts_payload_sha256": facts_record.payload_sha256 if facts_record else None}
    result["source_sha256"] = _digest({"projection": result, "extraction": wrapper, "event": event,
        "documents": list(documents), "excluded_fields": sorted(excluded)})
    return result


def _excluded_fields(conn, case_id, facts_id) -> set[str]:
    if not facts_id:
        return set()
    # Follow only the current snapshot's lineage; an unrelated old rehearsal
    # cannot remove fields from a fresh reanalysis version.
    rows = conn.execute(text("""
        WITH RECURSIVE lineage AS (
            SELECT id, supersedes_id, sequence FROM rtm_validated_facts WHERE id=:facts_id AND case_id=:case_id
            UNION
            SELECT f.id, f.supersedes_id, f.sequence FROM rtm_validated_facts f
            JOIN lineage l ON f.id=l.supersedes_id WHERE f.case_id=:case_id
        )
        SELECT e.payload FROM events e
        JOIN lineage l ON e.payload->>'facts_id'=CAST(l.id AS TEXT)
        WHERE e.case_id=:case_id AND e.type='rtm_validated_facts_reviewed'
        ORDER BY l.sequence,e.created_at,e.id
    """), {"case_id": case_id, "facts_id": facts_id}).fetchall()
    excluded = set()
    for row in rows:
        payload = adapter._mapping(row[0])
        excluded.update(payload.get("excluded_fields") or [])
        excluded.difference_update(payload.get("added_fields") or [])
        excluded.difference_update(payload.get("corrected_fields") or [])
    return excluded


def load_working_document(conn, case_id: str) -> dict:
    meta = authority._case_authority_meta(conn, case_id)
    authority._require_authority_work_allowed(meta)
    if meta["department"] != "traffic" or meta["case_type"] != "fine":
        raise HTTPException(409, "La propuesta de escrito corresponde a documentación de tráfico.")
    verify_signed_case_authority(conn, case_id)
    case = load_case_review_snapshot(conn, case_id)
    facts = authority.latest_validated_facts(conn, case_id, active_only=True)
    wrapper, event = adapter.load_latest_reanalysis_snapshot(conn, case_id)
    docs = [dict(row._mapping) for row in conn.execute(text(
        "SELECT id::text AS id,sha256 FROM documents WHERE case_id=:case_id AND kind='original' ORDER BY id"
    ), {"case_id": case_id}).fetchall()]
    return compose_working_document(case_id=case_id, identity=dict(case.interested_data),
        wrapper=wrapper, event=event, facts_record=facts,
        excluded_fields=_excluded_fields(conn, case_id, facts.id if facts else None), documents=docs)


def working_document_pdf(projection: dict, expected_source_sha256: str) -> bytes:
    if projection["source_sha256"] != expected_source_sha256:
        raise HTTPException(409, "La documentación ha cambiado. Actualiza el borrador antes de abrir el PDF.")
    return build_pdf(projection["title"], projection["content"])
