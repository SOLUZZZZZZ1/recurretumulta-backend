from __future__ import annotations

import unittest

from fastapi import HTTPException

from rtm_core.contracts import FactStatus, ResolutionStatus
from rtm_core.document_fact_catalog import (
    DOCUMENT_FACT_CATALOG_VERSION,
    canonical_document_service,
    fact_catalog_summary,
    field_spec,
    minimum_fact_keys,
    registered_fact_keys,
)
from rtm_core.document_normalization import (
    DOCUMENT_EXTRACTION_PACKET_VERSION,
    DOCUMENT_NORMALIZATION_VERSION,
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
    validate_packet_documents,
)
from rtm_core.family_dispatch import resolve_family


CASE_ID = "case-cross-documents-1"
DOC_1 = "doc-cross-documents-1"
DOC_2 = "doc-cross-documents-2"


def obs(
    field: str,
    value,
    *,
    document_id: str = DOC_1,
    evidence: str | None = "Fragmento documental visible",
    confidence: float = 0.99,
    source_type: str = "document_vision",
    method: str = "cross_service_document_v1",
) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=document_id,
        page_index=0,
        evidence=evidence,
        confidence=confidence,
        source_type=source_type,
        extraction_method=method,
    )


def packet(
    service: str,
    observations: list[DocumentObservation],
    *,
    documents: list[str] | None = None,
    quality: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> DocumentExtractionPacket:
    return DocumentExtractionPacket(
        case_id=CASE_ID,
        service=service,
        extractor_version=f"{service}_document_extractor_v1",
        source_document_ids=documents or [DOC_1],
        observations=observations,
        quality_flags=quality or [],
        declared_unresolved=unresolved or [],
    )


class DocumentFactCatalogTest(unittest.TestCase):
    def test_versions_and_satellites_are_explicit(self):
        self.assertEqual(
            DOCUMENT_FACT_CATALOG_VERSION,
            "rtm_document_fact_catalog_v1_2",
        )
        self.assertEqual(
            DOCUMENT_EXTRACTION_PACKET_VERSION,
            "rtm_document_extraction_packet_v1_0",
        )
        self.assertEqual(
            DOCUMENT_NORMALIZATION_VERSION,
            "rtm_document_normalization_v1_0",
        )
        for service in ("debt", "administration", "travel", "claims", "other"):
            with self.subTest(service=service):
                keys = registered_fact_keys(service)
                self.assertGreaterEqual(len(keys), 12)
                self.assertTrue(minimum_fact_keys(service))
                self.assertEqual(
                    fact_catalog_summary(service)["field_count"],
                    len(keys),
                )

    def test_aliases_and_traffic_boundary(self):
        self.assertEqual(
            field_spec("debt", "invoice_number").key,
            "factura_numero",
        )
        self.assertEqual(
            field_spec("administration", "administrative_act").key,
            "acto_administrativo",
        )
        self.assertEqual(
            field_spec("travel", "flight_number").key,
            "numero_vuelo",
        )
        self.assertEqual(
            field_spec("travel", "check_in_date").key,
            "estancia_inicio",
        )
        self.assertEqual(
            field_spec("travel", "total_booking_price").key,
            "precio_total_reserva_eur",
        )
        self.assertEqual(
            field_spec("travel", "organizer").key,
            "organizador_viaje",
        )
        self.assertEqual(
            field_spec("travel", "price_increase_percent").key,
            "incremento_precio_porcentaje",
        )
        self.assertEqual(
            field_spec("travel", "tourist_service_share_percent").key,
            "porcentaje_servicio_turistico",
        )
        self.assertEqual(
            field_spec("travel", "prior_claim_date").key,
            "reclamacion_previa_fecha",
        )
        self.assertEqual(
            field_spec("claims", "billing_period").key,
            "periodo_facturado",
        )
        with self.assertRaises(ValueError):
            canonical_document_service("traffic")


class DocumentNormalizationTest(unittest.TestCase):
    def test_four_satellites_reach_the_existing_family_dispatch(self):
        scenarios = (
            (
                "debt",
                [
                    obs(
                        "descripcion_hecho",
                        "La factura F-18 está vencida e impagada desde marzo.",
                        evidence="FACTURA F-18 — PENDIENTE DE PAGO",
                    ),
                    obs("factura_numero", "F-18", evidence="Factura F-18"),
                    obs(
                        "importe_deuda_eur",
                        "1.250,50 €",
                        evidence="TOTAL 1.250,50 EUR",
                    ),
                ],
                "factura_impagada",
                "debt.unpaid_invoice",
            ),
            (
                "administration",
                [
                    obs(
                        "tipo_documento",
                        "Providencia de apremio",
                        evidence="PROVIDENCIA DE APREMIO",
                    ),
                    obs(
                        "descripcion_hecho",
                        "Se exige principal y recargo de apremio.",
                        evidence="Principal 800 EUR; recargo 80 EUR",
                    ),
                    obs("principal_eur", "800", evidence="Principal 800 EUR"),
                    obs("recargo_eur", "80", evidence="Recargo 80 EUR"),
                ],
                "apremio_recaudacion",
                "administration.enforcement",
            ),
            (
                "travel",
                [
                    obs(
                        "descripcion_hecho",
                        "La aerolínea comunica que el vuelo RTM123 fue cancelado.",
                        evidence="Flight RTM123 has been cancelled",
                    ),
                    obs("numero_vuelo", "RTM123", evidence="Flight RTM123"),
                    obs("numero_reserva", "ABC123", evidence="Booking ABC123"),
                ],
                "vuelo_cancelado",
                "travel.flight_cancelled",
            ),
            (
                "claims",
                [
                    obs(
                        "descripcion_hecho",
                        "El operador de telecomunicaciones sigue cobrando fibra después de la baja.",
                        evidence="Factura posterior a la baja",
                    ),
                    obs("proveedor", "Operador Telecom", evidence="Operador Telecom"),
                    obs(
                        "fecha_baja_efectiva",
                        "15/06/2026",
                        evidence="Baja efectiva 15/06/2026",
                    ),
                ],
                "telecomunicaciones",
                "claims.telecommunications",
            ),
        )
        for service, observations, family, specialist in scenarios:
            with self.subTest(service=service):
                normalized = normalize_document_packet(
                    packet(service, observations)
                )
                resolution = resolve_family(normalized.facts)
                self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
                self.assertEqual(resolution.family, family)
                self.assertEqual(resolution.specialist, specialist)

    def test_money_date_time_integer_and_boolean_are_normalized(self):
        travel = normalize_document_packet(
            packet(
                "travel",
                [
                    obs(
                        "gastos_adicionales_eur",
                        "1.234,56 €",
                        evidence="Gastos 1.234,56 EUR",
                    ),
                    obs("fecha_vuelo", "2026/08/20", evidence="20/08/2026"),
                    obs(
                        "hora_salida_programada",
                        "09:35 h",
                        evidence="Scheduled 09:35",
                    ),
                    obs(
                        "numero_pasajeros",
                        "3 pasajeros",
                        evidence="Passengers 3",
                    ),
                ],
            )
        ).facts.facts
        self.assertEqual(travel["gastos_adicionales_eur"].value, 1234.56)
        self.assertEqual(travel["fecha_vuelo"].value, "2026-08-20")
        self.assertEqual(travel["hora_salida_programada"].value, "09:35")
        self.assertEqual(travel["numero_pasajeros"].value, 3)

        debt = normalize_document_packet(
            packet(
                "debt",
                [obs("deuda_pagada", "No", evidence="Deuda pagada: no")],
            )
        ).facts.facts
        self.assertFalse(debt["deuda_pagada"].value)

    def test_hotel_facts_are_typed_and_preserve_package_boundary(self):
        hotel = normalize_document_packet(
            packet(
                "travel",
                [
                    obs("fecha_reserva", "01/05/2026", evidence="Booked 01/05/2026"),
                    obs("estancia_inicio", "20/08/2026", evidence="Check-in 20/08/2026"),
                    obs("estancia_fin", "23/08/2026", evidence="Check-out 23/08/2026"),
                    obs("precio_total_reserva_eur", "720,00 €", evidence="Total 720 EUR"),
                    obs("numero_huespedes", "2 personas", evidence="Guests 2"),
                    obs("reserva_es_viaje_combinado", "No", evidence="Hotel only: no package"),
                ],
            )
        ).facts.facts
        self.assertEqual(hotel["fecha_reserva"].value, "2026-05-01")
        self.assertEqual(hotel["estancia_inicio"].value, "2026-08-20")
        self.assertEqual(hotel["estancia_fin"].value, "2026-08-23")
        self.assertEqual(hotel["precio_total_reserva_eur"].value, 720.0)
        self.assertEqual(hotel["numero_huespedes"].value, 2)
        self.assertFalse(hotel["reserva_es_viaje_combinado"].value)

    def test_package_travel_facts_are_typed_and_reach_family_dispatch(self):
        normalized = normalize_document_packet(
            packet(
                "travel",
                [
                    obs(
                        "descripcion_hecho",
                        "El viaje combinado fue cancelado por el organizador.",
                        evidence="VIAJE COMBINADO CANCELADO",
                    ),
                    obs("organizador_viaje", "Organizador Demo", evidence="Organizador Demo"),
                    obs("minorista_viaje", "Agencia Demo", evidence="Agencia Demo"),
                    obs("pais_organizador", "España", evidence="España"),
                    obs("fecha_inicio_viaje", "20/08/2026", evidence="Inicio 20/08/2026"),
                    obs("fecha_fin_viaje", "27/08/2026", evidence="Fin 27/08/2026"),
                    obs(
                        "servicios_viaje_incluidos",
                        "Vuelo de ida y vuelta y hotel siete noches",
                        evidence="Vuelo + hotel",
                    ),
                    obs("precio_total_viaje_eur", "2.400,00 €", evidence="Total 2.400 EUR"),
                    obs("incremento_precio_porcentaje", "8,5 %", evidence="Increase 8.5 percent"),
                    obs("porcentaje_servicio_turistico", "25,0 %", evidence="Share 25 percent"),
                    obs("servicio_turistico_esencial", "No", evidence="Not essential"),
                    obs("repatriacion_necesaria", "Sí", evidence="Repatriation required"),
                    obs("reserva_es_viaje_combinado", "Sí", evidence="Package travel: yes"),
                ],
            )
        )
        package = normalized.facts.facts
        self.assertEqual(package["fecha_inicio_viaje"].value, "2026-08-20")
        self.assertEqual(package["fecha_fin_viaje"].value, "2026-08-27")
        self.assertEqual(package["precio_total_viaje_eur"].value, 2400)
        self.assertEqual(package["incremento_precio_porcentaje"].value, 8.5)
        self.assertEqual(package["porcentaje_servicio_turistico"].value, 25)
        self.assertFalse(package["servicio_turistico_esencial"].value)
        self.assertTrue(package["repatriacion_necesaria"].value)
        self.assertTrue(package["reserva_es_viaje_combinado"].value)

        resolution = resolve_family(normalized.facts)
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "viaje_combinado")
        self.assertEqual(resolution.specialist, "travel.package")

    def test_family_strategy_draft_and_raw_ocr_are_rejected(self):
        result = normalize_document_packet(
            packet(
                "debt",
                [
                    obs("familia", "factura_impagada"),
                    obs("strategy", "Presentar monitorio"),
                    obs("draft", "Escrito completo"),
                    obs("raw_ocr_text", "FACTURA IMPAGADA"),
                ],
            )
        )
        rejected = {item.field: item.reason for item in result.rejected_observations}
        for key in ("familia", "strategy", "draft", "raw_ocr_text"):
            self.assertEqual(
                rejected[key],
                "field_is_not_a_document_fact",
            )
        self.assertFalse(result.accepted_fields)

    def test_weak_missing_ocr_and_client_candidates_keep_null(self):
        result = normalize_document_packet(
            packet(
                "debt",
                [
                    obs("importe_deuda_eur", "900 EUR", confidence=0.60),
                    obs("factura_numero", "F-55", evidence=None),
                    obs(
                        "acreedor",
                        "Empresa X",
                        source_type="ocr",
                        method="ocr_v1",
                    ),
                    obs(
                        "descripcion_hecho",
                        "El cliente dice que existe una deuda.",
                        source_type="client_statement",
                        method="intake_form_v1",
                    ),
                ],
            )
        )
        for key in (
            "importe_deuda_eur",
            "factura_numero",
            "acreedor",
            "descripcion_hecho",
        ):
            self.assertEqual(
                result.facts.facts[key].status,
                FactStatus.UNRESOLVED,
            )
            self.assertIsNone(result.facts.facts[key].value)

    def test_low_quality_requires_operator_document_review(self):
        automatic = normalize_document_packet(
            packet(
                "administration",
                [obs("expediente_ref", "ADM-99", evidence="ADM-99")],
                quality=["low_legibility"],
            )
        )
        self.assertEqual(
            automatic.facts.facts["expediente_ref"].status,
            FactStatus.UNRESOLVED,
        )

        reviewed = normalize_document_packet(
            packet(
                "administration",
                [
                    obs(
                        "expediente_ref",
                        "ADM-99",
                        evidence="ADM-99",
                        source_type="operator_document_review",
                        method="ops_document_review_v1",
                    )
                ],
                quality=["low_legibility"],
            )
        )
        self.assertEqual(
            reviewed.facts.facts["expediente_ref"].status,
            FactStatus.VALIDATED,
        )

    def test_single_value_conflict_is_null_and_narrative_set_is_merged(self):
        conflict = normalize_document_packet(
            packet(
                "debt",
                [
                    obs("importe_deuda_eur", "1.000 EUR", evidence="Saldo 1.000"),
                    obs(
                        "importe_deuda_eur",
                        "1.200 EUR",
                        document_id=DOC_2,
                        evidence="Total 1.200",
                    ),
                ],
                documents=[DOC_1, DOC_2],
            )
        )
        amount = conflict.facts.facts["importe_deuda_eur"]
        self.assertEqual(amount.status, FactStatus.CONFLICTED)
        self.assertIsNone(amount.value)
        self.assertTrue(amount.conflicts)

        merged = normalize_document_packet(
            packet(
                "claims",
                [
                    obs(
                        "descripcion_hecho",
                        "El proveedor confirmó la baja.",
                        evidence="Baja confirmada",
                    ),
                    obs(
                        "descripcion_hecho",
                        "Después emitió una nueva factura.",
                        document_id=DOC_2,
                        evidence="Factura posterior",
                    ),
                ],
                documents=[DOC_1, DOC_2],
            )
        )
        narrative = merged.facts.facts["descripcion_hecho"]
        self.assertEqual(narrative.status, FactStatus.VALIDATED)
        self.assertEqual(len(narrative.value), 2)
        self.assertNotIn("descripcion_hecho", merged.conflicted_fields)

    def test_wrong_service_declared_unresolved_and_foreign_documents(self):
        wrong = normalize_document_packet(
            packet(
                "debt",
                [obs("numero_vuelo", "RTM123", evidence="Flight RTM123")],
            )
        )
        self.assertEqual(
            wrong.rejected_observations[0].reason,
            "field_not_registered_for_service",
        )

        unresolved = normalize_document_packet(
            packet(
                "travel",
                [obs("numero_reserva", "ABC123", evidence="Booking ABC123")],
                unresolved=["numero_reserva"],
            )
        )
        self.assertEqual(
            unresolved.facts.facts["numero_reserva"].status,
            FactStatus.UNRESOLVED,
        )

        valid_packet = packet(
            "claims",
            [obs("proveedor", "Empresa X")],
        )
        validate_packet_documents(
            packet=valid_packet,
            available_document_ids={DOC_1},
        )
        with self.assertRaises(HTTPException):
            validate_packet_documents(
                packet=valid_packet,
                available_document_ids={DOC_2},
            )


if __name__ == "__main__":
    unittest.main()
