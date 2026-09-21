"""Document-based parking preparation. Advisory only; no procedural decisions."""
from __future__ import annotations

from copy import deepcopy
import unicodedata

from rtm_core.contracts import FactStatus, ResolutionStatus, ValidatedFacts
from rtm_core.family_core import _FOCUSED_TEXT_KEYS, resolve_family

PARKING_PREPARATION_VERSION = "rtm_traffic_parking_preparation_v1_0"
LSV = "https://www.boe.es/buscar/act.php?id=BOE-A-2015-11722"
RGC = "https://www.boe.es/buscar/act.php?id=BOE-A-2003-23514"
LPAC = "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565"
REFERENCES = (
    {"id": "proof", "title": "Ley de Tráfico · valor de la denuncia (art. 88)", "url": LSV + "#a88"},
    {"id": "procedure", "title": "Ley de Tráfico · procedimientos y pago reducido (arts. 93–96)", "url": LSV + "#a93"},
    {"id": "parking", "title": "Reglamento de Circulación · estacionamiento (art. 94)", "url": RGC + "#a94"},
    {"id": "access", "title": "Ley 39/2015 · acceso al expediente (art. 53)", "url": LPAC + "#a53"},
)

# Each field remains documentary input, never proof that its legal assessment
# is complete. A notification date cannot substitute for the date of conduct.
CHECKS = (
    ("procedure", "Trámite y notificación", "Determina qué documento se ha recibido y qué actuación corresponde. Revisa también las notificaciones posteriores.",
     (("fase_procedimental", "Fase"), ("tipo_documento", "Tipo de documento"), ("fecha_notificacion", "Notificación")), ("procedure",)),
    ("deadline", "Plazo para actuar", "Contrasta el vencimiento con la notificación, el trámite y el calendario aplicable. Esta guía no calcula ni reinicia plazos.",
     (("fecha_limite", "Vencimiento documentado"), ("fecha_notificacion", "Notificación")), ("procedure",)),
    ("location", "Lugar, fecha y hora del hecho", "Localiza la vía y el punto exacto. Comprueba la fecha y hora del hecho; la fecha del documento no las sustituye.",
     (("lugar_infraccion", "Lugar"), ("fecha_infraccion", "Fecha del hecho"), ("hora_infraccion", "Hora del hecho")), ("parking",)),
    ("rule", "Precepto y ordenanza", "Identifica el precepto denunciado, el municipio y la norma aplicable a la fecha de los hechos. No presupongas una ordenanza por el domicilio del cliente.",
     (("norma_hint", "Norma indicada"), ("articulo_infringido_num", "Precepto indicado"), ("ordenanza_aplicable", "Ordenanza indicada")), ("parking",)),
    ("conditions", "Señalización, horario y permiso", "Contrasta la prohibición concreta, el horario y, si procede, el tique o autorización. Comprueba que las imágenes correspondan al lugar y momento del hecho.",
     (("senalizacion_estacionamiento", "Señalización"), ("horario_estacionamiento", "Horario"), ("autorizacion_estacionamiento", "Permiso o tique")), ("parking",)),
    ("evidence", "Denuncia y pruebas disponibles", "Revisa quién denuncia, qué observó y las pruebas. La ausencia de una fotografía no demuestra por sí sola que la multa sea inválida.",
     (("tipo_denunciante", "Denunciante"), ("fotografia_vehiculo_presente", "Fotografía documentada"), ("prueba_estacionamiento", "Prueba revisada")), ("proof", "access")),
    ("payment", "Pago de la multa", "Comprueba si se pagó la multa con reducción y sus consecuencias para el trámite. El pago del servicio de RTM es independiente.",
     (("pago_multa_reducido", "Pago reducido de la multa"),), ("procedure",)),
    ("defense", "Motivo concreto y petición", "Vincula cada motivo a un dato o prueba contrastados y concreta la petición. Los datos que faltan en RTM no prueban un defecto de la Administración.",
     (("contradiccion_estacionamiento", "Contradicción documentada"),), ("proof",)),
)


def reviewed_fact(facts: ValidatedFacts, key: str):
    item = facts.facts.get(key)
    if (not item or item.status != FactStatus.VALIDATED or item.conflicts
            or not isinstance(item.value, (str, bool, int, float))
            or (isinstance(item.value, str) and not item.value.strip())):
        return None
    sources = [source for source in item.sources if source.document_id in facts.source_document_ids
               and source.page_index is not None and source.evidence and source.evidence.strip()]
    if not sources:
        return None
    return item.model_copy(update={"sources": sources})


def _normal(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(value))
                             if not unicodedata.combining(c)).lower().split())


def build_parking_preparation(facts: ValidatedFacts) -> dict:
    allowed = {key for _, _, _, fields, _ in CHECKS for key, _ in fields}
    allowed.update({"hecho_denunciado_literal", "hecho_denunciado_resumido", "hecho_imputado",
                    "organismo", "expediente_ref", "matricula", "sancion_importe_eur"})
    allowed.update(_FOCUSED_TEXT_KEYS)
    # Resolve against all validated conduct, so another offence remains a
    # conflict instead of being hidden by the parking-specific display filter.
    reviewed = {key: item for key in facts.facts if (item := reviewed_fact(facts, key)) is not None}
    source_facts = facts.model_copy(update={"facts": reviewed})
    resolution = resolve_family(source_facts)
    ready = resolution.status == ResolutionStatus.RESOLVED and resolution.family == "estacionamiento" and not facts.conflicts
    result = {"version": PARKING_PREPARATION_VERSION, "case_id": facts.case_id,
              "status": "review_required" if ready else "unavailable", "family": "estacionamiento" if ready else None,
              "legal_review_pending": True, "ready_to_submit": False,
              "checked_on": "2026-09-21", "checks": [], "references": [], "pending_text": "",
              "reported_fact": None, "procedure_note": "", "payment_note": "",
              "message": "Preparación de estacionamiento: completa la revisión antes de formular los motivos."
                         if ready else "La guía de estacionamiento necesita un hecho documental confirmado y una familia sin conflicto. Revisa los hechos o continúa la revisión manual."}
    if not ready:
        return result
    result["references"] = deepcopy(list(REFERENCES))

    def field(key, label):
        item = reviewed.get(key) if key in allowed else None
        if not item:
            return {"key": key, "label": label, "value": None, "sources": []}
        return {"key": key, "label": label, "value": item.value,
                "sources": [{"document_id": s.document_id, "page_index": s.page_index} for s in item.sources]}

    evidence_keys = [key for entry in resolution.evidence for key in entry.source_fact_keys]
    fact_key = next((key for key in evidence_keys if key in reviewed and key in allowed), None)
    if fact_key:
        result["reported_fact"] = field(fact_key, "Hecho denunciado confirmado")
    result["checks"] = [{"id": code, "title": title, "instruction": instruction,
                         "fields": [field(key, label) for key, label in fields], "reference_ids": list(refs)}
                        for code, title, instruction, fields, refs in CHECKS]

    phases = [_normal(reviewed[key].value) for key in ("fase_procedimental", "tipo_documento") if key in reviewed]
    initial = {"denuncia", "notificacion de denuncia", "notificacion de denuncia e iniciacion", "iniciacion", "alegaciones"}
    specific = {"resolucion sancionadora", "resolucion", "notificacion de resolucion sancionadora",
                "providencia de apremio", "requerimiento de identificacion del conductor"}
    result["procedure_note"] = "Fase pendiente de confirmar: todavía no se propone un tipo de escrito."
    if phases and all(value in specific for value in phases):
        result["procedure_note"] = "Consta una resolución, actuación ejecutiva o identificación: determina el trámite específico antes de preparar alegaciones."
    elif phases and all(value in initial for value in phases):
        result["procedure_note"] = "Consta una fase inicial: contrasta la notificación, el pago reducido y el plazo antes de proponer alegaciones."

    paid = reviewed.get("pago_multa_reducido")
    result["payment_note"] = "Pago reducido de la multa pendiente de comprobar; el pago a RTM no permite deducirlo."
    if paid and paid.value is True:
        result["payment_note"] = "Consta pago reducido de la multa. Revisa sus efectos: no se propone el trámite ordinario de alegaciones (art. 94 de la Ley de Tráfico)."
    elif paid and paid.value is False:
        result["payment_note"] = "El dato revisado indica que no se ha pagado la multa con reducción. Contrasta que siga vigente antes de actuar."
    result["pending_text"] = "GUÍA DE ESTACIONAMIENTO · PENDIENTE DE REVISIÓN\n" + "\n".join(
        f"{index}. {check['title']}: {check['instruction']}" for index, check in enumerate(result["checks"], 1))
    return result
