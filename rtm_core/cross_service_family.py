"""Resolución conservadora de familias para satélites no tráfico.

Consume exclusivamente hechos validados. No usa OCR crudo, comentarios libres
sin procedencia, scoring legacy ni resultados anteriores de clasificación. Las
reglas identifican familias amplias para orientar el estudio; no sustituyen al
especialista jurídico ni habilitan Generate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from rtm_core.contracts import (
    FactStatus,
    FamilyConflict,
    FamilyEvidence,
    FamilyResolution,
    ResolutionStatus,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.domain_catalog import family_profile, registered_family_codes
from rtm_core.service_catalog import canonical_department


CROSS_SERVICE_FAMILY_VERSION = "rtm_cross_service_family_v1_0"


@dataclass(frozen=True)
class _Atom:
    key: str
    fact: ValidatedFact
    text: str
    document_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Rule:
    family: str
    code: str
    description: str
    patterns: tuple[str, ...]
    confidence: float
    priority: int


@dataclass
class _Candidate:
    family: str
    specialist: str
    confidence: float = 0.0
    priority: int = 0
    evidence: list[FamilyEvidence] = field(default_factory=list)

    def add(self, rule: _Rule, atoms: Iterable[_Atom]) -> None:
        matched = list(atoms)
        if not matched:
            return
        source_keys = sorted({atom.key for atom in matched})
        source_docs = sorted(
            {document_id for atom in matched for document_id in atom.document_ids}
        )
        marker = (rule.code, tuple(source_keys), tuple(source_docs))
        existing = {
            (
                evidence.code,
                tuple(evidence.source_fact_keys),
                tuple(evidence.source_document_ids),
            )
            for evidence in self.evidence
        }
        if marker not in existing:
            self.evidence.append(
                FamilyEvidence(
                    code=rule.code,
                    description=rule.description,
                    source_fact_keys=source_keys,
                    source_document_ids=source_docs,
                    confidence=rule.confidence,
                )
            )
        self.confidence = max(self.confidence, rule.confidence)
        self.priority = max(self.priority, rule.priority)


def _fold(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"[^a-z0-9%/.,:+@€-]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _safe_atoms(facts: ValidatedFacts) -> list[_Atom]:
    atoms: list[_Atom] = []
    for key, fact in facts.facts.items():
        if fact.status is not FactStatus.VALIDATED:
            continue
        key_low = str(key).strip().lower()
        if any(
            token in key_low
            for token in (
                "raw",
                "ocr",
                "vision",
                "prompt",
                "template",
                "classifier",
                "classification",
                "scoring",
                "draft",
                "strategy",
            )
        ):
            continue
        text_value = _fold(fact.value)
        if not text_value:
            continue
        atoms.append(
            _Atom(
                key=str(key),
                fact=fact,
                text=text_value,
                document_ids=tuple(
                    sorted({source.document_id for source in fact.sources})
                ),
            )
        )
    return atoms


def _matching_atoms(atoms: list[_Atom], patterns: tuple[str, ...]) -> list[_Atom]:
    return [
        atom
        for atom in atoms
        if any(re.search(pattern, atom.text, flags=re.IGNORECASE) for pattern in patterns)
    ]


_RULES: dict[str, tuple[_Rule, ...]] = {
    "debt": (
        _Rule("fichero_solvencia", "explicit_credit_file", "Los hechos validados se refieren a una inclusión o baja en un fichero de solvencia.", (r"\b(?:asnef|equifax|badexcug|fichero\s+de\s+(?:morosos|solvencia))\b", r"\binclusion\b.{0,50}\bfichero\b"), 0.98, 100),
        _Rule("monitorio", "explicit_payment_order", "Los hechos validados identifican un procedimiento monitorio.", (r"\bprocedimiento\s+monitorio\b", r"\bpeticion\s+inicial\s+de\s+monitorio\b", r"\brequerimiento\s+judicial\s+de\s+pago\b"), 0.98, 100),
        _Rule("oposicion_deudor", "explicit_debtor_defence", "Los hechos validados describen oposición o defensa frente a una deuda reclamada.", (r"\boposicion\s+(?:al|a\s+la)\s+(?:monitorio|deuda|reclamacion)\b", r"\bdeuda\s+(?:no\s+reconocida|discutida|ya\s+pagada)\b", r"\bdefensa\s+del\s+deudor\b"), 0.96, 95),
        _Rule("alquiler_impagado", "explicit_unpaid_rent", "Los hechos validados identifican rentas o cantidades de arrendamiento impagadas.", (r"\b(?:alquiler|renta|mensualidad)\w*\b.{0,55}\bimpagad\w*\b", r"\bimpago\s+de\s+(?:alquiler|rentas)\b", r"\barrendatari\w*\b.{0,55}\bdebe\b"), 0.97, 90),
        _Rule("factura_impagada", "explicit_unpaid_invoice", "Los hechos validados identifican una factura vencida e impagada.", (r"\bfactura\w*\b.{0,55}\bimpagad\w*\b", r"\bimpago\s+de\s+factura\b", r"\bfactura\s+vencida\b"), 0.97, 90),
        _Rule("prestamo_deuda", "explicit_loan_or_acknowledgement", "Los hechos validados identifican un préstamo o reconocimiento de deuda.", (r"\bprestamo\b.{0,60}\b(?:impagado|vencido|devolucion|deuda)\b", r"\breconocimiento\s+de\s+deuda\b", r"\bdocumento\s+de\s+deuda\b"), 0.96, 90),
        _Rule("insolvencia", "explicit_insolvency", "Los hechos validados señalan una situación de insolvencia o concurso.", (r"\binsolvencia\b", r"\bconcurso\s+de\s+acreedores\b", r"\bsin\s+bienes\s+embargables\b"), 0.95, 85),
        _Rule("negociacion_deuda", "explicit_settlement", "Los hechos validados se refieren a una negociación o acuerdo de pago.", (r"\bacuerdo\s+de\s+pago\b", r"\bplan\s+de\s+pagos\b", r"\bquita\s+y\s+espera\b", r"\bfraccionamiento\s+de\s+deuda\b"), 0.94, 80),
        _Rule("requerimiento_pago", "explicit_payment_demand", "Los hechos validados identifican una reclamación o requerimiento de pago.", (r"\brequerimiento\s+(?:fehaciente\s+)?de\s+pago\b", r"\breclamacion\s+extrajudicial\s+de\s+(?:deuda|pago)\b", r"\bcarta\s+de\s+pago\b"), 0.94, 75),
    ),
    "administration": (
        _Rule("responsabilidad_patrimonial", "explicit_public_liability", "Los hechos validados se refieren a daños atribuidos al funcionamiento de una Administración.", (r"\bresponsabilidad\s+patrimonial\b", r"\bdano\w*\b.{0,70}\bfuncionamiento\s+(?:normal|anormal)\b", r"\bindemnizacion\b.{0,60}\badministracion\b"), 0.98, 100),
        _Rule("silencio_administrativo", "explicit_administrative_silence", "Los hechos validados identifican falta de resolución dentro del plazo administrativo.", (r"\bsilencio\s+administrativo\b", r"\bsin\s+respuesta\b.{0,60}\bsolicitud\b", r"\bplazo\s+para\s+resolver\b.{0,50}\btranscurrid\w*\b"), 0.97, 100),
        _Rule("apremio_recaudacion", "explicit_enforcement", "Los hechos validados identifican una providencia de apremio o actuación recaudatoria.", (r"\bprovidencia\s+de\s+apremio\b", r"\bvia\s+ejecutiva\b", r"\brecargo\s+de\s+apremio\b", r"\bdiligencia\s+de\s+embargo\b"), 0.98, 95),
        _Rule("subvencion", "explicit_grant", "Los hechos validados se refieren a una subvención, justificación o reintegro.", (r"\bsubvencion\b", r"\breintegro\s+de\s+subvencion\b", r"\bjustificacion\s+de\s+la\s+ayuda\b"), 0.96, 90),
        _Rule("licencia", "explicit_licence", "Los hechos validados se refieren a una licencia o autorización administrativa.", (r"\blicencia\b", r"\bautorizacion\s+administrativa\b", r"\bpermiso\s+administrativo\b"), 0.95, 88),
        _Rule("tributos", "explicit_tax", "Los hechos validados identifican una liquidación, impuesto o actuación tributaria.", (r"\bliquidacion\s+(?:provisional|tributaria)\b", r"\b(?:iva|irpf|ibi|iae|impuesto|tributo)\b", r"\bagencia\s+tributaria\b", r"\baeat\b"), 0.95, 88),
        _Rule("sancion_administrativa", "explicit_administrative_sanction", "Los hechos validados identifican un procedimiento sancionador administrativo.", (r"\bprocedimiento\s+sancionador\b", r"\bpropuesta\s+de\s+sancion\b", r"\bresolucion\s+sancionadora\b", r"\binfraccion\s+administrativa\b"), 0.96, 85),
        _Rule("requerimiento", "explicit_administrative_requirement", "Los hechos validados identifican un requerimiento de la Administración.", (r"\brequerimiento\s+de\s+(?:subsanacion|documentacion|informacion|cumplimiento)\b", r"\bsubsanar\b.{0,60}\bplazo\b"), 0.94, 80),
        _Rule("recurso_administrativo", "explicit_administrative_appeal", "Los hechos validados identifican un recurso administrativo, sin prejuzgar el asunto de fondo.", (r"\brecurso\s+de\s+(?:alzada|reposicion)\b", r"\brecurso\s+administrativo\b"), 0.92, 60),
    ),
    "travel": (
        _Rule("denegacion_embarque", "explicit_denied_boarding", "Los hechos validados identifican una denegación de embarque.", (r"\bdenegacion\s+de\s+embarque\b", r"\boverbooking\b", r"\bsobreventa\b.{0,35}\bvuelo\b"), 0.98, 100),
        _Rule("vuelo_cancelado", "explicit_flight_cancellation", "Los hechos validados identifican la cancelación de un vuelo.", (r"\bvuelo\b.{0,40}\bcancelad\w*\b", r"\bcancelacion\s+del\s+vuelo\b"), 0.98, 98),
        _Rule("retraso_vuelo", "explicit_flight_delay", "Los hechos validados identifican un retraso de vuelo.", (r"\bvuelo\b.{0,45}\bretras\w*\b", r"\bretraso\s+del\s+vuelo\b", r"\bllegada\b.{0,45}\bhoras?\s+de\s+retraso\b"), 0.97, 95),
        _Rule("equipaje", "explicit_baggage", "Los hechos validados identifican pérdida, demora o daños de equipaje.", (r"\bequipaje\b.{0,55}\b(?:perdid|extraviad|danad|deteriorad|retrasad|demorad)\w*\b", r"\bparte\s+de\s+irregularidad\s+de\s+equipaje\b", r"\bpir\b.{0,30}\bequipaje\b"), 0.97, 92),
        _Rule("viaje_combinado", "explicit_package_travel", "Los hechos validados identifican un viaje combinado o paquete turístico.", (r"\bviaje\s+combinado\b", r"\bpaquete\s+turistico\b", r"\borganizador\b.{0,50}\bviaje\b"), 0.96, 90),
        _Rule("seguro_viaje", "explicit_travel_insurance", "Los hechos validados identifican una cobertura o siniestro de seguro de viaje.", (r"\bseguro\s+de\s+viaje\b", r"\bpoliza\b.{0,50}\bviaje\b", r"\bsiniestro\b.{0,50}\bviaje\b"), 0.95, 88),
        _Rule("hotel", "explicit_hotel", "Los hechos validados identifican un problema con hotel o alojamiento.", (r"\bhotel\b.{0,60}\b(?:cancelad|incumpl|cerrad|distint|defect|reserva)\w*\b", r"\balojamiento\b.{0,60}\b(?:cancelad|incumpl|reserva|defect)\w*\b"), 0.94, 85),
        _Rule("agencia_plataforma", "explicit_travel_intermediary", "Los hechos validados identifican una incidencia con agencia o plataforma de reservas.", (r"\bagencia\s+de\s+viajes\b", r"\bplataforma\s+de\s+reservas\b", r"\bintermediari\w*\b.{0,50}\breserva\b"), 0.92, 70),
    ),
    "claims": (
        _Rule("telecomunicaciones", "explicit_telecommunications", "Los hechos validados identifican un problema de telecomunicaciones.", (r"\b(?:telefonia|telecomunicaciones|operador\s+movil|fibra|internet)\b", r"\bportabilidad\b", r"\bpermanencia\b.{0,35}\boperador\b"), 0.96, 95),
        _Rule("energia", "explicit_energy", "Los hechos validados identifican un problema de energía o suministro.", (r"\b(?:electricidad|gas|comercializadora|distribuidora|suministro\s+electrico)\b", r"\blectura\s+del\s+contador\b"), 0.96, 95),
        _Rule("seguros", "explicit_insurance_claim", "Los hechos validados identifican una reclamación de seguro.", (r"\bpoliza\b.{0,50}\b(?:siniestro|cobertura|indemnizacion|rechazo)\b", r"\baseguradora\b", r"\bperitacion\b"), 0.96, 95),
        _Rule("banca", "explicit_banking", "Los hechos validados identifican un problema bancario o de medio de pago.", (r"\b(?:banco|entidad\s+bancaria|tarjeta|transferencia|cargo\s+no\s+reconocido)\b", r"\boperacion\s+no\s+autorizada\b", r"\bprestamo\s+hipotecario\b"), 0.95, 92),
        _Rule("comercio_electronico", "explicit_ecommerce", "Los hechos validados identifican una compra o contratación electrónica.", (r"\b(?:pedido|compra)\s+online\b", r"\bcomercio\s+electronico\b", r"\bmarketplace\b", r"\bproducto\b.{0,45}\bno\s+entregad\w*\b"), 0.95, 90),
        _Rule("servicios_profesionales", "explicit_professional_service", "Los hechos validados identifican un servicio profesional discutido.", (r"\bservicios?\s+profesional(?:es)?\b", r"\bhoja\s+de\s+encargo\b", r"\bhonorarios\b.{0,50}\b(?:incumpl|disput|factura)\w*\b"), 0.93, 85),
        _Rule("consumo", "explicit_consumer_claim", "Los hechos validados identifican una reclamación general de consumo.", (r"\breclamacion\s+de\s+consumo\b", r"\bgarantia\s+del\s+producto\b", r"\bproducto\s+defectuoso\b", r"\bservicio\s+no\s+prestado\b"), 0.90, 60),
    ),
}


def _candidate_for(
    store: dict[str, _Candidate],
    *,
    service: str,
    family: str,
) -> _Candidate:
    profile = family_profile(service, family)
    if not profile:
        raise RuntimeError(
            f"Familia {service}.{family} no registrada en el catálogo {registered_family_codes(service)}"
        )
    if family not in store:
        store[family] = _Candidate(
            family=family,
            specialist=profile.specialist,
        )
    return store[family]


def _suppress_lower_priority(candidates: list[_Candidate]) -> list[_Candidate]:
    if not candidates:
        return []
    highest = max(candidate.priority for candidate in candidates)
    # Una señal de trámite genérico no debe competir con una familia de fondo
    # claramente identificada. Señales próximas conservan el conflicto.
    return [candidate for candidate in candidates if candidate.priority >= highest - 8]


def resolve_cross_service_family(facts: ValidatedFacts) -> FamilyResolution:
    service = canonical_department(facts.service)
    if service not in _RULES:
        return FamilyResolution(
            case_id=facts.case_id,
            service=facts.service,
            facts_version=facts.version,
            status=ResolutionStatus.UNRESOLVED,
            family=None,
            confidence=0.0,
            evidence=[],
            conflicts=[],
            unresolved=[
                f"El servicio '{service}' todavía no dispone de reglas de familia registradas."
            ],
            specialist=None,
            locked=False,
        )

    atoms = _safe_atoms(facts)
    candidates: dict[str, _Candidate] = {}
    for rule in _RULES[service]:
        matches = _matching_atoms(atoms, rule.patterns)
        if not matches:
            continue
        _candidate_for(candidates, service=service, family=rule.family).add(rule, matches)

    ordered = sorted(
        _suppress_lower_priority(list(candidates.values())),
        key=lambda candidate: (-candidate.priority, -candidate.confidence, candidate.family),
    )
    if not ordered:
        return FamilyResolution(
            case_id=facts.case_id,
            service=facts.service,
            facts_version=facts.version,
            status=ResolutionStatus.UNRESOLVED,
            family=None,
            confidence=0.0,
            evidence=[],
            conflicts=[],
            unresolved=[
                "Los hechos validados permiten orientar el servicio, pero no contienen una señal factual suficiente para fijar una familia concreta."
            ],
            specialist=None,
            locked=False,
        )

    if len(ordered) > 1:
        families = [candidate.family for candidate in ordered]
        return FamilyResolution(
            case_id=facts.case_id,
            service=facts.service,
            facts_version=facts.version,
            status=ResolutionStatus.CONFLICTED,
            family=None,
            confidence=max(candidate.confidence for candidate in ordered),
            evidence=[
                evidence
                for candidate in ordered
                for evidence in candidate.evidence
            ],
            conflicts=[
                FamilyConflict(
                    code=f"multiple_{service}_families",
                    description=(
                        "Los hechos validados sostienen más de una familia específica; "
                        "OPS debe revisar si existen varios asuntos o si falta precisión documental."
                    ),
                    candidate_families=families,
                )
            ],
            unresolved=["Debe resolverse el conflicto de familia antes de bloquear el especialista."],
            specialist=None,
            locked=False,
        )

    selected = ordered[0]
    return FamilyResolution(
        case_id=facts.case_id,
        service=facts.service,
        facts_version=facts.version,
        status=ResolutionStatus.RESOLVED,
        family=selected.family,
        confidence=selected.confidence,
        evidence=selected.evidence,
        conflicts=[],
        unresolved=[],
        specialist=selected.specialist,
        locked=False,
        resolved_at=datetime.now(timezone.utc),
    )
