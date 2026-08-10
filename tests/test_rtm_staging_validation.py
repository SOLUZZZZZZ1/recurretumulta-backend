from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from rtm_core.document_extraction import (
    ProviderDocumentResult,
    ProviderObservation,
)
from rtm_core.staging_validation import (
    LIVE_CONFIRMATION,
    STAGING_FIXTURE_SET_VERSION,
    STAGING_VALIDATION_VERSION,
    SYNTHETIC_MARKER,
    assert_live_synthetic_guard,
    fixture_root,
    run_synthetic_scenario,
    run_synthetic_staging_suite,
    staging_scenarios,
)


class _SyntheticFixtureProvider:
    version = "rtm_ci_synthetic_fixture_provider_v1"
    model = "deterministic-fixture-model"

    @staticmethod
    def _observation(field, value, evidence, *, confidence=0.99):
        return ProviderObservation(
            field=field,
            value=value,
            page_index=0,
            evidence=evidence,
            confidence=confidence,
            notes=[],
        )

    def extract_document(self, *, service, document, content):
        text = content.decode("utf-8")
        if SYNTHETIC_MARKER not in text:
            raise AssertionError("Se intentó usar un documento no sintético")

        if service == "debt":
            observations = [
                self._observation(
                    "descripcion_hecho",
                    "La factura F-2026-018 está vencida e impagada.",
                    "la factura F-2026-018 continúa impagada",
                ),
                self._observation(
                    "factura_numero",
                    "F-2026-018",
                    "FACTURA F-2026-018",
                ),
                self._observation(
                    "saldo_pendiente_eur",
                    "1.250,50 EUR",
                    "Saldo pendiente: 1.250,50 EUR",
                ),
                self._observation(
                    "fecha_vencimiento",
                    "01/07/2026",
                    "Fecha de vencimiento: 01/07/2026",
                ),
                self._observation(
                    "acreedor",
                    "PROVEEDOR DEMO, S.L.",
                    "Emisor: PROVEEDOR DEMO, S.L.",
                ),
            ]
        elif service == "administration":
            observations = [
                self._observation(
                    "descripcion_hecho",
                    "Se ha notificado una providencia de apremio con recargo.",
                    "PROVIDENCIA DE APREMIO",
                ),
                self._observation(
                    "expediente_ref",
                    "AP-2026-0042",
                    "Expediente: AP-2026-0042",
                ),
                self._observation(
                    "acto_administrativo",
                    "Providencia de apremio",
                    "PROVIDENCIA DE APREMIO",
                ),
                self._observation(
                    "importe_exigido_eur",
                    "840,00 EUR",
                    "Importe total exigido: 840,00 EUR",
                ),
                self._observation(
                    "principal_eur",
                    "700,00 EUR",
                    "Principal: 700,00 EUR",
                ),
                self._observation(
                    "recargo_eur",
                    "140,00 EUR",
                    "Recargo de apremio: 140,00 EUR",
                ),
                self._observation(
                    "fecha_notificacion",
                    "08/08/2026",
                    "Fecha de notificación: 08/08/2026",
                ),
            ]
        elif service == "travel":
            observations = [
                self._observation(
                    "descripcion_hecho",
                    "El vuelo RTM123 fue cancelado por la aerolínea.",
                    "el vuelo RTM123 ha sido CANCELADO",
                ),
                self._observation(
                    "numero_vuelo",
                    "RTM123",
                    "Número de vuelo: RTM123",
                ),
                self._observation(
                    "numero_reserva",
                    "TEST6A",
                    "Localizador de reserva: TEST6A",
                ),
                self._observation(
                    "fecha_vuelo",
                    "12/08/2026",
                    "Fecha del vuelo: 12/08/2026",
                ),
                self._observation(
                    "aerolinea",
                    "AEROLÍNEA DEMO",
                    "Aerolínea: AEROLÍNEA DEMO",
                ),
            ]
        elif service == "claims":
            observations = [
                self._observation(
                    "descripcion_hecho",
                    (
                        "El operador de telecomunicaciones siguió cobrando el "
                        "servicio de fibra después de la baja efectiva."
                    ),
                    "Pese a la baja efectiva, el operador de telecomunicaciones emitió la factura",
                ),
                self._observation(
                    "proveedor",
                    "OPERADOR TELECOM DEMO",
                    "Proveedor: OPERADOR TELECOM DEMO",
                ),
                self._observation(
                    "baja_solicitada_fecha",
                    "15/06/2026",
                    "Fecha de solicitud de baja: 15/06/2026",
                ),
                self._observation(
                    "fecha_baja_efectiva",
                    "30/06/2026",
                    "Fecha de baja efectiva comunicada: 30/06/2026",
                ),
                self._observation(
                    "importe_pagado_eur",
                    "79,90 EUR",
                    "efectuó un cobro de 79,90 EUR",
                ),
                self._observation(
                    "factura_numero",
                    "F-TEL-0726",
                    "emitió la factura F-TEL-0726",
                ),
            ]
        else:  # pragma: no cover - el catálogo de staging no registra otro caso
            raise AssertionError(f"Servicio sintético no previsto: {service}")

        return (
            ProviderDocumentResult(
                observations=observations,
                unresolved_fields=[],
                quality_flags=[],
                document_notes=["Proveedor determinista de CI."],
            ),
            "document_text",
            [],
        )


class SyntheticStagingValidationTest(unittest.TestCase):
    def test_versions_fixtures_and_scenarios_are_explicit(self):
        self.assertEqual(
            STAGING_VALIDATION_VERSION,
            "rtm_synthetic_staging_validation_v1_0",
        )
        self.assertEqual(
            STAGING_FIXTURE_SET_VERSION,
            "rtm_synthetic_fixture_set_v1_0",
        )
        scenarios = staging_scenarios()
        self.assertEqual(
            {item.service for item in scenarios},
            {"debt", "administration", "travel", "claims"},
        )
        for item in scenarios:
            content = (fixture_root() / item.fixture_filename).read_text("utf-8")
            self.assertIn(SYNTHETIC_MARKER, content)

    def test_all_synthetic_satellites_reach_family_and_first_direction(self):
        report = run_synthetic_staging_suite(
            provider=_SyntheticFixtureProvider(),
        )
        self.assertTrue(report.passed, report.model_dump(mode="json"))
        self.assertFalse(report.live_provider)
        self.assertEqual(len(report.scenarios), 4)

        expected = {
            "debt": ("factura_impagada", "debt.unpaid_invoice"),
            "administration": (
                "apremio_recaudacion",
                "administration.enforcement",
            ),
            "travel": ("vuelo_cancelado", "travel.flight_cancelled"),
            "claims": (
                "telecomunicaciones",
                "claims.telecommunications",
            ),
        }
        for result in report.scenarios:
            with self.subTest(service=result.service):
                family, specialist = expected[result.service]
                self.assertTrue(result.passed, result.errors)
                self.assertEqual(result.family_status, "resolved")
                self.assertEqual(result.family, family)
                self.assertEqual(result.specialist, specialist)
                self.assertEqual(result.direction_source, "core_projection")
                self.assertEqual(result.direction_maturity, "orientation_only")
                self.assertFalse(result.generation_allowed)
                self.assertFalse(result.conflicted_fields)

    def test_report_never_contains_fixture_text_or_evidence(self):
        report = run_synthetic_staging_suite(
            provider=_SyntheticFixtureProvider(),
        )
        rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn("PROVEEDOR DEMO, S.L.", rendered)
        self.assertNotIn("FACTURA F-2026-018", rendered)
        self.assertNotIn("AEROLÍNEA DEMO", rendered)
        self.assertNotIn("OPERADOR TELECOM DEMO", rendered)
        self.assertNotIn("evidence", rendered.lower())

    def test_live_guard_requires_all_explicit_flags(self):
        blocked_env = {
            "RTM_ENV": "",
            "RTM_STAGING_CONFIRM": "",
            "RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, blocked_env, clear=False):
            with self.assertRaises(HTTPException) as blocked:
                assert_live_synthetic_guard()
        self.assertEqual(blocked.exception.status_code, 409)

        allowed_env = {
            "RTM_ENV": "staging",
            "RTM_STAGING_CONFIRM": LIVE_CONFIRMATION,
            "RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION": "1",
            "OPENAI_API_KEY": "synthetic-ci-key-not-used",
        }
        with patch.dict(os.environ, allowed_env, clear=False):
            assert_live_synthetic_guard()

    def test_tampered_fixture_without_marker_is_rejected(self):
        scenario = staging_scenarios(["debt"])[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / scenario.fixture_filename).write_text(
                "FACTURA REAL O NO MARCADA",
                encoding="utf-8",
            )
            with self.assertRaises(HTTPException) as blocked:
                run_synthetic_scenario(
                    scenario,
                    provider=_SyntheticFixtureProvider(),
                    root=root,
                )
        self.assertEqual(blocked.exception.status_code, 409)
        self.assertIn("marca", str(blocked.exception.detail).lower())

    def test_service_filter_never_runs_unselected_scenarios(self):
        report = run_synthetic_staging_suite(
            provider=_SyntheticFixtureProvider(),
            selected_services=["travel"],
        )
        self.assertTrue(report.passed)
        self.assertEqual([item.service for item in report.scenarios], ["travel"])


if __name__ == "__main__":
    unittest.main()
