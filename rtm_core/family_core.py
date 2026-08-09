"""Resolución única y determinista de familia jurídica RTM.

El resolver consume exclusivamente ``ValidatedFacts``. No lee OCR crudo, texto
completo del formulario, scoring legacy ni resultados de clasificadores
anteriores. Una etiqueta impresa como ``km/h`` nunca es suficiente para activar
Velocidad.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from rtm_core.contracts import (
    FactStatus,
    FamilyConflict,
    FamilyEvidence,
    FamilyResolution,
    ResolutionStatus,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.service_catalog import canonical_department


FAMILY_CORE_VERSION = "rtm_family_core_v1_0"


_FOCUSED_TEXT_KEYS = {
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_imputado",
    "hecho_validado",
    "conducta_imputada",
    "descripcion_infraccion",
    "descripcion_hecho",
    "literal_denuncia",
    "observacion_agente_validada",
}

_SPEED_MEASURED_KEYS = {
    "velocidad_medida_kmh",
    "velocidad_captada_kmh",
    "velocidad_detectada_kmh",
    "velocidad_registrada_kmh",
    "velocidad_circulacion_kmh",
}
_SPEED_LIMIT_KEYS = {
    "velocidad_limite_kmh",
    "limite_velocidad_kmh",
    "velocidad_maxima_kmh",
    "limite_via_kmh",
}
_SEMAPHORE_PHASE_KEYS = {
    "semaforo_fase",
    "fase_semaforo",
    "luz_semaforo",
    "estado_semaforo",
}

_SPECIALISTS = {
    "temeraria": "traffic.temeraria",
    "velocidad": "traffic.velocidad",
    "semaforo": "traffic.semaforo",
    "movil": "traffic.movil",
    "auriculares": "traffic.auriculares",
    "atencion": "traffic.atencion",
    "alcohol": "traffic.alcohol",
    "drogas": "traffic.drogas",
    "cinturon": "traffic.cinturon",
    "casco": "traffic.casco",
    "seguro": "traffic.seguro",
    "itv": "traffic.itv",
    "marcas_viales": "traffic.marcas_viales",
    "carril": "traffic.carril",
    "condiciones_vehiculo": "traffic.condiciones_vehiculo",
    "neumaticos": "traffic.neumaticos",
    "estiba": "traffic.estiba",
    "peso": "traffic.peso",
    "tacografo": "traffic.tacografo",
    "documentacion_transporte": "traffic.documentacion_transporte",
    "adr": "traffic.adr",
}


@dataclass(frozen=True)
class _Atom:
    key: str
    fact: ValidatedFact
    normalized: str
    document_ids: tuple[str, ...]


@dataclass
class _Candidate:
    family: str
    specialist: str
    confidence: float = 0.0
    evidence: list[FamilyEvidence] = field(default_factory=list)

    def add(
        self,
        *,
        code: str,
        description: str,
        keys: Iterable[str],
        document_ids: Iterable[str],
        confidence: float,
    ) -> None:
        source_keys = sorted({str(value) for value in keys if str(value)})
        source_docs = sorted({str(value) for value in document_ids if str(value)})
        if not source_keys:
            return
        marker = (code, tuple(source_keys), tuple(source_docs))
        existing = {
            (
                item.code,
                tuple(item.source_fact_keys),
                tuple(item.source_document_ids),
            )
            for item in self.evidence
        }
        if marker not in existing:
            self.evidence.append(
                FamilyEvidence(
                    code=code,
                    description=description,
                    source_fact_keys=source_keys,
                    source_document_ids=source_docs,
                    confidence=confidence,
                )
            )
        self.confidence = max(self.confidence, confidence)


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # Los objetos estructurados se consumen por clave; no se convierten en
        # una bolsa de texto que pueda reintroducir etiquetas del formulario.
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_normalize(item) for item in value if item is not None)

    raw = unicodedata.normalize("NFKD", str(value))
    ascii_value = "".join(ch for ch in raw if not unicodedata.combining(ch))
    ascii_value = ascii_value.lower().replace("\r", " ").replace("\n", " ")
    ascii_value = re.sub(r"[^a-z0-9%/.,:+-]+", " ", ascii_value)
    return re.sub(r"\s+", " ", ascii_value).strip()


def _document_ids(fact: ValidatedFact) -> tuple[str, ...]:
    return tuple(sorted({source.document_id for source in fact.sources}))


def _validated_atoms(facts: ValidatedFacts) -> list[_Atom]:
    atoms: list[_Atom] = []
    for key, fact in facts.facts.items():
        if fact.status is not FactStatus.VALIDATED:
            continue
        atoms.append(
            _Atom(
                key=str(key),
                fact=fact,
                normalized=_normalize(fact.value),
                document_ids=_document_ids(fact),
            )
        )
    return atoms


def _focused_text_atoms(atoms: Iterable[_Atom]) -> list[_Atom]:
    selected: list[_Atom] = []
    for atom in atoms:
        key = atom.key.lower().strip()
        if key in _FOCUSED_TEXT_KEYS:
            selected.append(atom)
            continue
        if any(token in key for token in ("raw", "ocr", "vision", "formulario", "template")):
            continue
        if key.endswith(("_literal", "_resumido", "_imputado", "_validado")):
            selected.append(atom)
    return selected


def _matches(atom: _Atom, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, atom.normalized) for pattern in patterns)


def _matching_atoms(atoms: Iterable[_Atom], patterns: Iterable[str]) -> list[_Atom]:
    compiled = tuple(patterns)
    return [atom for atom in atoms if atom.normalized and _matches(atom, compiled)]


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)", str(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _first_numeric(atoms: Iterable[_Atom], keys: set[str]) -> tuple[Optional[float], Optional[_Atom]]:
    for atom in atoms:
        if atom.key.lower() not in keys:
            continue
        value = _numeric(atom.fact.value)
        if value is not None:
            return value, atom
    return None, None


def _candidate(store: dict[str, _Candidate], family: str) -> _Candidate:
    if family not in store:
        store[family] = _Candidate(
            family=family,
            specialist=_SPECIALISTS[family],
        )
    return store[family]


def _add_text_rule(
    store: dict[str, _Candidate],
    atoms: list[_Atom],
    *,
    family: str,
    code: str,
    description: str,
    patterns: Iterable[str],
    confidence: float,
) -> None:
    matches = _matching_atoms(atoms, patterns)
    if not matches:
        return
    _candidate(store, family).add(
        code=code,
        description=description,
        keys=[atom.key for atom in matches],
        document_ids=[doc for atom in matches for doc in atom.document_ids],
        confidence=confidence,
    )


def _apply_structured_speed(store: dict[str, _Candidate], atoms: list[_Atom]) -> None:
    measured, measured_atom = _first_numeric(atoms, _SPEED_MEASURED_KEYS)
    limit, limit_atom = _first_numeric(atoms, _SPEED_LIMIT_KEYS)
    if measured is None or limit is None or measured_atom is None or limit_atom is None:
        return
    if not (0 < limit < measured <= 350):
        return
    _candidate(store, "velocidad").add(
        code="validated_speed_pair",
        description=(
            "Constan como hechos validados una velocidad medida y un límite de "
            "vía coherentes con un exceso de velocidad."
        ),
        keys=[measured_atom.key, limit_atom.key],
        document_ids=[*measured_atom.document_ids, *limit_atom.document_ids],
        confidence=0.99,
    )


def _apply_structured_semaphore(store: dict[str, _Candidate], atoms: list[_Atom]) -> None:
    matches = [
        atom
        for atom in atoms
        if atom.key.lower() in _SEMAPHORE_PHASE_KEYS
        and re.search(r"\b(rojo|roja|red)\b", atom.normalized)
    ]
    if not matches:
        return
    _candidate(store, "semaforo").add(
        code="validated_red_phase",
        description="La fase roja del semáforo consta como hecho estructurado validado.",
        keys=[atom.key for atom in matches],
        document_ids=[doc for atom in matches for doc in atom.document_ids],
        confidence=0.99,
    )


def _apply_explicit_rules(store: dict[str, _Candidate], text_atoms: list[_Atom]) -> None:
    _add_text_rule(
        store,
        text_atoms,
        family="temeraria",
        code="explicit_reckless_driving",
        description="El hecho validado atribuye expresamente conducción temeraria.",
        patterns=(
            r"\bconduccion\s+temeraria\b",
            r"\bconducir\s+de\s+forma\s+temeraria\b",
            r"\bconducia\s+de\s+forma\s+temeraria\b",
            r"\bconduccion\s+manifiestamente\s+temeraria\b",
        ),
        confidence=0.995,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="velocidad",
        code="explicit_speed_fact",
        description="El hecho validado describe expresamente un exceso de velocidad.",
        patterns=(
            r"\bexceso\s+de\s+velocidad\b",
            r"\bsuperar\w*\s+la\s+velocidad\s+maxima\b",
            r"\bvelocidad\s+(?:medida|captada|registrada|detectada)\s+(?:de\s+)?\d{2,3}\s*km/?h\b",
            r"\bcircul(?:ar|aba|ando)\s+a\s+\d{2,3}\s*km/?h.{0,100}\blimit(?:e|ado|ada)\b.{0,30}\d{2,3}\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="semaforo",
        code="explicit_red_light_fact",
        description="El hecho validado atribuye no respetar una señal luminosa roja.",
        patterns=(
            r"\bno\s+respetar\w*.{0,30}\bluz\s+roja\b",
            r"\bno\s+respetar\w*.{0,30}\bsemaforo\s+en\s+rojo\b",
            r"\bcruz(?:ar|o|aba|ando).{0,35}\bfase\s+roja\b",
            r"\brebas(?:ar|o|aba).{0,35}\blinea\s+de\s+detencion.{0,40}\broj[oa]\b",
            r"\bsemaforo\s+en\s+fase\s+roja\b",
        ),
        confidence=0.98,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="movil",
        code="explicit_mobile_use",
        description="El hecho validado atribuye el uso manual de un teléfono móvil.",
        patterns=(
            r"\butiliz(?:ar|aba|ando).{0,35}\btelefono\s+movil\b",
            r"\bmanipul(?:ar|aba|ando).{0,35}\btelefono\s+movil\b",
            r"\bsostener.{0,30}\btelefono\s+movil\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="auriculares",
        code="explicit_headphones",
        description="El hecho validado atribuye conducción usando auriculares o cascos de audio.",
        patterns=(
            r"\butiliz(?:ar|aba|ando).{0,30}\bauriculares\b",
            r"\bconduc(?:ir|ia|iendo).{0,40}\bcascos\s+de\s+audio\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="atencion",
        code="explicit_attention_failure",
        description="El hecho validado atribuye falta de atención o conducción negligente.",
        patterns=(
            r"\bfalta\s+de\s+atencion\b",
            r"\bno\s+mantener\w*.{0,25}\batencion\s+permanente\b",
            r"\bconduccion\s+negligente\b",
            r"\bconducir\s+de\s+forma\s+negligente\b",
            r"\bdistraccion\s+durante\s+la\s+conduccion\b",
        ),
        confidence=0.92,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="alcohol",
        code="explicit_alcohol_fact",
        description="El hecho validado atribuye una tasa de alcohol o negativa a la prueba.",
        patterns=(
            r"\btasa\s+de\s+alcohol\b",
            r"\balcoholemia\b",
            r"\bnegativ[ao].{0,35}\bprueba\s+de\s+alcohol\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="drogas",
        code="explicit_drugs_fact",
        description="El hecho validado atribuye presencia de drogas o negativa a la prueba.",
        patterns=(
            r"\bpresencia\s+de\s+drogas\b",
            r"\bprueba\s+de\s+drogas\s+positiva\b",
            r"\bnegativ[ao].{0,35}\bprueba\s+de\s+drogas\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="cinturon",
        code="explicit_seatbelt_fact",
        description="El hecho validado atribuye no usar el cinturón de seguridad.",
        patterns=(r"\bno\s+utiliz(?:ar|aba).{0,25}\bcinturon\b",),
        confidence=0.96,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="casco",
        code="explicit_helmet_fact",
        description="El hecho validado atribuye no usar el casco obligatorio.",
        patterns=(r"\bno\s+utiliz(?:ar|aba).{0,25}\bcasco\b",),
        confidence=0.96,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="seguro",
        code="explicit_insurance_fact",
        description="El hecho validado atribuye circular sin seguro obligatorio en vigor.",
        patterns=(
            r"\bcarecer\w*.{0,30}\bseguro\s+obligatorio\b",
            r"\bcircular.{0,30}\bsin\s+seguro\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="itv",
        code="explicit_itv_fact",
        description="El hecho validado atribuye circular con la ITV no vigente.",
        patterns=(
            r"\bitv\s+(?:caducada|desfavorable|negativa|no\s+vigente)\b",
            r"\binspeccion\s+tecnica.{0,35}\bno\s+vigente\b",
        ),
        confidence=0.97,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="marcas_viales",
        code="explicit_road_marking_fact",
        description="El hecho validado atribuye incumplir una marca vial concreta.",
        patterns=(
            r"\brebas(?:ar|o|aba).{0,30}\blinea\s+continua\b",
            r"\bno\s+respetar.{0,30}\bmarca\s+vial\b",
        ),
        confidence=0.94,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="carril",
        code="explicit_lane_fact",
        description="El hecho validado atribuye uso indebido de carril.",
        patterns=(
            r"\bcircular.{0,35}\bcarril\s+contrario\b",
            r"\buso\s+indebido\s+del\s+carril\b",
            r"\bcircular.{0,35}\bpor\s+el\s+arcen\b",
        ),
        confidence=0.94,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="neumaticos",
        code="explicit_tyres_fact",
        description="El hecho validado atribuye defectos reglamentarios en neumáticos.",
        patterns=(
            r"\bneumatic\w*.{0,35}\b(?:desgastad|deteriorad|sin\s+dibujo|profundidad)\w*\b",
        ),
        confidence=0.95,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="estiba",
        code="explicit_load_securing_fact",
        description="El hecho validado atribuye una estiba o sujeción incorrecta de la carga.",
        patterns=(
            r"\bestiba\s+incorrecta\b",
            r"\bcarga.{0,30}\b(?:mal\s+sujeta|sin\s+sujecion|desplazamiento)\b",
        ),
        confidence=0.95,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="peso",
        code="explicit_weight_fact",
        description="El hecho validado atribuye exceso de masa o peso autorizado.",
        patterns=(
            r"\bexceso\s+de\s+(?:peso|masa)\b",
            r"\bsuperar.{0,30}\bmasa\s+maxima\b",
        ),
        confidence=0.95,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="tacografo",
        code="explicit_tachograph_fact",
        description="El hecho validado atribuye una infracción de tacógrafo.",
        patterns=(r"\btacografo\b.{0,45}\b(?:manipulad|sin\s+tarjeta|descanso|conduccion)\w*\b",),
        confidence=0.94,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="documentacion_transporte",
        code="explicit_transport_documents_fact",
        description="El hecho validado atribuye falta de documentación de transporte.",
        patterns=(
            r"\bcarecer.{0,35}\bcarta\s+de\s+porte\b",
            r"\bno\s+presentar.{0,35}\bdocumentacion\s+de\s+transporte\b",
        ),
        confidence=0.93,
    )
    _add_text_rule(
        store,
        text_atoms,
        family="adr",
        code="explicit_adr_fact",
        description="El hecho validado atribuye una infracción de mercancías peligrosas ADR.",
        patterns=(
            r"\bmercancias\s+peligrosas\b",
            r"\badr\b.{0,30}\b(?:incumpl|documentacion|senalizacion|equipamiento)\w*\b",
        ),
        confidence=0.95,
    )


def _suppress_generic_candidates(store: dict[str, _Candidate]) -> None:
    # Las familias específicas mandan sobre categorías generales, pero no se
    # ocultan dos infracciones específicas distintas: eso requiere revisión OPS.
    if "temeraria" in store:
        store.pop("atencion", None)
    if "movil" in store or "auriculares" in store:
        store.pop("atencion", None)
    if any(family in store for family in ("seguro", "itv", "neumaticos")):
        store.pop("condiciones_vehiculo", None)


def resolve_family(facts: ValidatedFacts) -> FamilyResolution:
    """Devuelve la única salida de familia; nunca bloquea por sí misma."""

    service = canonical_department(facts.service)
    if service != "traffic":
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
                f"No existe todavía resolver de familia para el servicio '{service}'."
            ],
            specialist=None,
            locked=False,
        )

    atoms = _validated_atoms(facts)
    text_atoms = _focused_text_atoms(atoms)
    candidates: dict[str, _Candidate] = {}

    _apply_structured_speed(candidates, atoms)
    _apply_structured_semaphore(candidates, atoms)
    _apply_explicit_rules(candidates, text_atoms)
    _suppress_generic_candidates(candidates)

    ordered = sorted(
        candidates.values(),
        key=lambda item: (-item.confidence, item.family),
    )
    all_evidence = [
        evidence
        for candidate in ordered
        for evidence in candidate.evidence
    ]

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
                "No hay una frase factual explícita o hechos estructurados suficientes para resolver la familia."
            ],
            specialist=None,
            locked=False,
        )

    high_confidence = [candidate for candidate in ordered if candidate.confidence >= 0.90]
    if len(high_confidence) > 1:
        families = [candidate.family for candidate in high_confidence]
        return FamilyResolution(
            case_id=facts.case_id,
            service=facts.service,
            facts_version=facts.version,
            status=ResolutionStatus.CONFLICTED,
            family=None,
            confidence=max(candidate.confidence for candidate in high_confidence),
            evidence=all_evidence,
            conflicts=[
                FamilyConflict(
                    code="multiple_specific_families",
                    description=(
                        "Los hechos validados contienen señales explícitas de más de una familia específica."
                    ),
                    candidate_families=families,
                )
            ],
            unresolved=["OPS debe confirmar si existen varias infracciones o una lectura conflictiva."],
            specialist=None,
            locked=False,
        )

    top = ordered[0]
    if top.confidence < 0.90:
        return FamilyResolution(
            case_id=facts.case_id,
            service=facts.service,
            facts_version=facts.version,
            status=ResolutionStatus.OPERATOR_REVIEW,
            family=top.family,
            confidence=top.confidence,
            evidence=top.evidence,
            conflicts=[],
            unresolved=["La evidencia apunta a una familia, pero no alcanza el umbral de resolución automática."],
            specialist=top.specialist,
            locked=False,
        )

    return FamilyResolution(
        case_id=facts.case_id,
        service=facts.service,
        facts_version=facts.version,
        status=ResolutionStatus.RESOLVED,
        family=top.family,
        confidence=top.confidence,
        evidence=top.evidence,
        conflicts=[],
        unresolved=[],
        specialist=top.specialist,
        locked=False,
        resolved_at=datetime.now(timezone.utc),
    )
