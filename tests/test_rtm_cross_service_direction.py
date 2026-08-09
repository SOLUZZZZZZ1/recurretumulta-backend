from __future__ import annotations

from datetime import datetime, timezone
import unittest

from rtm_core.contracts import (
    FactStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.domain_catalog import (
    DOMAIN_CATALOG_VERSION,
    family_profile,
    registered_family_codes,
    service_profile,
)
from rtm_core.family_dispatch import (
    FAMILY_DISPATCH_VERSION,
    resolve_family,
)
from rtm_core.first_direction import (
    FIRST_DIRECTION_VERSION,
    build_first_direction,
)
from rtm_core.workspace_policy_ext import determine_workspace_stage


DOC_ID = "doc-cross-service-1"
NOW = datetime.now(timezone.utc)


def _source() -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        extraction_method="service-document+operator",
        evidence="fragmento documental validado",
        confidence=0.98,
    )


def _fact(value) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.98,
        sources=[_source()],
    )


def _facts(service: str, values: dict[str, object]) -> ValidatedFacts:
    return ValidatedFacts(
        case_id=f"case-{service}",
        service=service,
        extractor_version=f"{service}_facts_v1",
        facts={key: _fact(value) for key, value in values.items()},
        source_document_ids=[DOC_ID],
    )


class CrossServiceFamilyAndDirectionTest(unittest.TestCase):
    def test_versions_are_explicit(self):
        self.assertEqual(DOMAIN_CATALOG_VERSION, "rtm_domain_catalog_v1_0")
        self.assertEqual(FAMILY_DISPATCH_VERSION, "rtm_family_dispatch_v1_0")
        self.assertEqual(
            FIRST_DIRECTION_VERSION,
            "rtm_first_direction_projection_v1_0",
        )

    def test_catalog_covers_all_satellites_and_many_families(self):
        for service in (
            "traffic",
            "debt",
            "administration",
            "travel",
            "claims",
            "other",
        ):
            with self.subTest(service=service):
                self.assertEqual(service_profile(service).department, service)
                self.assertTrue(registered_family_codes(service))

        self.assertIsNotNone(family_profile("debt", "fichero_solvencia"))
        self.assertIsNotNone(
            family_profile("administration", "responsabilidad_patrimonial")
        )
        self.assertIsNotNone(family_profile("travel", "vuelo_cancelado"))
        self.assertIsNotNone(family_profile("claims", "telecomunicaciones"))

    def test_debt_invoice_family_is_resolved_from_validated_fact(self):
        resolution = resolve_family(
            _facts(
                "debt",
                {
                    "descripcion_hecho": (
                        "La factura 2026-18 está vencida e impagada desde marzo."
                    )
                },
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "factura_impagada")
        self.assertEqual(resolution.specialist, "debt.unpaid_invoice")

    def test_administration_enforcement_family_is_resolved(self):
        resolution = resolve_family(
            _facts(
                "administration",
                {
                    "tipo_documento": "Providencia de apremio",
                    "descripcion_hecho": "Se exige principal y recargo de apremio.",
                },
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "apremio_recaudacion")
        self.assertEqual(resolution.specialist, "administration.enforcement")

    def test_travel_cancellation_family_is_resolved(self):
        resolution = resolve_family(
            _facts(
                "travel",
                {
                    "descripcion_hecho": "El vuelo fue cancelado por la aerolínea.",
                    "numero_vuelo": "RTM123",
                },
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "vuelo_cancelado")
        self.assertEqual(resolution.specialist, "travel.flight_cancelled")

    def test_claims_telecommunications_family_is_resolved(self):
        resolution = resolve_family(
            _facts(
                "claims",
                {
                    "descripcion_hecho": (
                        "El operador de telecomunicaciones sigue cobrando fibra "
                        "después de la baja."
                    )
                },
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "telecomunicaciones")

    def test_raw_ocr_never_resolves_a_cross_service_family(self):
        resolution = resolve_family(
            _facts(
                "travel",
                {"raw_ocr_text": "VUELO CANCELADO EQUIPAJE PERDIDO"},
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(resolution.family)

    def test_multiple_specific_travel_families_return_conflict(self):
        resolution = resolve_family(
            _facts(
                "travel",
                {
                    "descripcion_hecho": (
                        "El vuelo acumuló un retraso y finalmente fue cancelado."
                    )
                },
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.CONFLICTED)
        self.assertIsNone(resolution.family)
        self.assertEqual(
            set(resolution.conflicts[0].candidate_families),
            {"vuelo_cancelado", "retraso_vuelo"},
        )

    def test_unavailable_specialist_still_produces_first_direction(self):
        facts = _facts(
            "debt",
            {
                "descripcion_hecho": "Factura vencida e impagada.",
                "importe_deuda_eur": 1250,
                "fecha_vencimiento": "2026-07-01",
            },
        )
        resolution = resolve_family(facts)
        facts_record = {
            "id": "facts-1",
            "frozen": True,
            "invalidated_at": None,
            "facts": facts.model_dump(mode="json"),
        }
        family_record = {
            "id": "family-1",
            "locked": True,
            "invalidated_at": None,
            "resolution": resolution.model_dump(mode="json"),
        }
        next_step = {
            "stage": "initial_direction_review",
            "primary_action": "review_first_direction",
            "actions": [
                {
                    "code": "review_first_direction",
                    "label": "Revisar el primer rumbo del expediente",
                }
            ],
        }

        direction = build_first_direction(
            case_id=facts.case_id,
            case_payload={
                "department": "debt",
                "case_type": "invoice",
                "status": "family_locked",
                "payment_status": "paid",
                "authorized": True,
            },
            readiness={"ready": True, "blocking_issues": []},
            latest_facts=facts_record,
            latest_family=family_record,
            latest_preview=None,
            next_step=next_step,
            registered_specialists=(
                "traffic.velocidad",
                "traffic.semaforo",
                "traffic.temeraria",
            ),
        )

        self.assertEqual(direction.source, "core_projection")
        self.assertEqual(direction.maturity, "orientation_only")
        self.assertEqual(direction.family, "factura_impagada")
        self.assertFalse(direction.specialist_available)
        self.assertFalse(direction.authoritative)
        self.assertFalse(direction.generation_allowed)
        self.assertTrue(direction.what_we_found)
        self.assertIn("factura", direction.primary_direction.lower())
        self.assertTrue(
            any("Generate" in warning for warning in direction.warnings)
        )

    def test_workspace_stops_at_direction_when_specialist_is_missing(self):
        result = determine_workspace_stage(
            case_id="case-debt",
            case_status="family_locked",
            payment_status="paid",
            authorized=True,
            readiness_ready=True,
            reanalysis_available=True,
            latest_facts={
                "id": "facts-1",
                "frozen": True,
                "invalidated_at": None,
            },
            latest_family={
                "id": "family-1",
                "locked": True,
                "invalidated_at": None,
                "resolution": {"status": "resolved"},
            },
            latest_preview=None,
            latest_resource=None,
            service="debt",
            specialist_available=False,
        )
        self.assertEqual(result["stage"], "initial_direction_review")
        self.assertEqual(result["primary_action"], "review_first_direction")

    def test_workspace_uses_safe_reanalysis_only_for_traffic(self):
        traffic = determine_workspace_stage(
            case_id="case-traffic",
            case_status="core_review_pending",
            payment_status="paid",
            authorized=True,
            readiness_ready=True,
            reanalysis_available=False,
            service="traffic",
        )
        self.assertEqual(traffic["primary_action"], "run_safe_reanalysis")
        self.assertEqual(
            traffic["actions"][0]["endpoint"],
            "/ops/core/cases/case-traffic/reanalysis/run",
        )

        debt = determine_workspace_stage(
            case_id="case-debt",
            case_status="core_review_pending",
            payment_status="paid",
            authorized=True,
            readiness_ready=True,
            reanalysis_available=False,
            service="debt",
        )
        self.assertEqual(debt["stage"], "service_fact_extraction_pending")
        self.assertFalse(
            any(
                action.get("endpoint") == "/ops/cases/case-debt/reanalyze"
                for action in debt["actions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
