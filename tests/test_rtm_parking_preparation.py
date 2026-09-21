import json
from pathlib import Path
import unittest

from fastapi import HTTPException
from rtm_core.authority_repository import model_digest
from rtm_core.contracts import FactStatus, ResolutionStatus, ValidatedFacts
from rtm_core.family_core import _FOCUSED_TEXT_KEYS, resolve_family
from rtm_core.specialist_dispatch import build_legal_preview
from rtm_core.traffic_parking_preparation import build_parking_preparation
from tests.test_rtm_velocity_semaforo_specialists import _fact, _records

CASE = '11111111-1111-4111-8111-111111111111'
DOC = '22222222-2222-4222-8222-222222222222'
TEXT = 'Estacionar en una zona de prueba señalizada.'


def replace(record, **updates):
    return record.model_copy(update=updates)


def snapshot(**changes):
    values = {'hecho_denunciado_literal': TEXT, 'organismo': 'Ayuntamiento ficticio RTM',
              'expediente_ref': 'RTM-PRUEBA-001', 'matricula': 'RTM-TEST-002',
              'fecha_documento': '2026-09-17', 'fecha_notificacion': '2026-09-18',
              'sancion_importe_eur': 200, **changes}
    return ValidatedFacts(case_id=CASE, service='traffic', extractor_version='synthetic-test',
                          source_document_ids=[DOC], facts={key: _fact(DOC, value) for key, value in values.items()})


class ParkingPreparationTests(unittest.TestCase):
    def test_every_supported_documentary_conduct_key_keeps_its_provenance(self):
        for key in _FOCUSED_TEXT_KEYS:
            with self.subTest(key=key):
                facts = snapshot()
                facts.facts = {key: _fact(DOC, TEXT)}
                guide = build_parking_preparation(facts)
                self.assertEqual(guide['status'], 'review_required')
                self.assertEqual(guide['reported_fact']['key'], key)
                self.assertEqual(guide['reported_fact']['sources'][0]['document_id'], DOC)

    def test_explicit_conduct_resolves_family_but_never_locks_or_approves(self):
        for value in [TEXT, 'Hecho ficticio: '+TEXT, 'Estacionamiento en doble fila.',
                      'Aparcar en zona de carga y descarga.', 'Se denuncia por estacionar sobre la acera.']:
            with self.subTest(value=value):
                result = resolve_family(snapshot(hecho_denunciado_literal=value))
                self.assertEqual(result.family, 'estacionamiento')
                self.assertFalse(result.locked)
                self.assertEqual(result.evidence[0].source_fact_keys, ['hecho_denunciado_literal'])
                self.assertEqual(result.evidence[0].source_document_ids, [DOC])

    def test_negated_incidental_and_template_mentions_do_not_resolve_parking(self):
        for value in ['No estacionar en doble fila.', 'Niega haber estacionado.',
                      'El vehículo estaba estacionado con ITV caducada.',
                      'La señal indica prohibición de estacionar.', 'El vehículo se encontraba estacionado.',
                      'No consta estacionamiento indebido.', 'Estacionamiento no denunciado.',
                      'Conducir sin seguro y después aparcar.', 'Se permite estacionar en la zona.']:
            with self.subTest(value=value):
                self.assertNotEqual(resolve_family(snapshot(hecho_denunciado_literal=value)).family, 'estacionamiento')
        facts = snapshot(hecho_denunciado_literal='Sin conducta confirmada', raw_text_ocr=TEXT, formulario_literal=TEXT)
        self.assertIsNone(resolve_family(facts).family)

    def test_two_offences_remain_a_conflict(self):
        facts = snapshot(hecho_imputado='Circular a 121 km/h teniendo limitada la velocidad a 90 km/h.')
        self.assertEqual(resolve_family(facts).status, ResolutionStatus.CONFLICTED)
        self.assertEqual(build_parking_preparation(facts)['status'], 'unavailable')

    def test_unreviewed_conflicted_missing_evidence_and_foreign_sources_are_ignored(self):
        for update in [{'status': FactStatus.UNRESOLVED}, {'conflicts': ['Conflicto de lectura']},
                       {'sources': []}, {'sources': [_fact('another-case-doc', TEXT).sources[0]]},
                       {'sources': [_fact(DOC,TEXT).sources[0].model_copy(update={'page_index': None})]}]:
            facts = snapshot()
            facts.facts['hecho_denunciado_literal'] = facts.facts['hecho_denunciado_literal'].model_copy(update=update)
            guide = build_parking_preparation(facts)
            self.assertEqual(guide['status'], 'unavailable')
            self.assertFalse(guide['checks'])
            self.assertFalse(guide['pending_text'])

    def test_guide_preserves_unknowns_and_does_not_invent_a_deadline_or_defense(self):
        facts = snapshot(payment_status='paid', raw_text_ocr='HIDDEN RAW', lugar_infraccion={'fake':'object'})
        before = facts.model_dump_json()
        guide = build_parking_preparation(facts)
        self.assertEqual(guide['status'], 'review_required')
        self.assertEqual(len(guide['checks']), 8)
        self.assertTrue(guide['legal_review_pending'])
        self.assertFalse(guide['ready_to_submit'])
        fields = {field['key']:field for check in guide['checks'] for field in check['fields']}
        for key in ['fecha_infraccion', 'hora_infraccion', 'lugar_infraccion', 'fecha_limite', 'pago_multa_reducido']:
            self.assertIsNone(fields[key]['value'])
            self.assertEqual(fields[key]['sources'], [])
        self.assertIn('pendiente',guide['procedure_note'])
        self.assertIn('pendiente',guide['payment_note'])
        self.assertNotIn('HIDDEN RAW', json.dumps(guide))
        self.assertEqual(before, facts.model_dump_json())

    def test_reduced_fine_payment_is_strict_and_distinct_from_rtm_payment(self):
        for value, expected in [(True, 'Consta pago reducido'), (False, 'no se ha pagado'),
                                ('false', 'pendiente'), ('true', 'pendiente'), (1, 'pendiente')]:
            with self.subTest(value=value):
                guide=build_parking_preparation(snapshot(pago_multa_reducido=value))
                self.assertIn(expected, guide['payment_note'])

    def test_phase_is_not_inferred_from_dates_or_incidental_words(self):
        for phase in ['Resolución sancionadora', 'Providencia de apremio', 'Requerimiento de identificación del conductor',
                      'No consta denuncia', 'Denuncia o resolución, pendiente', 'No firme; pendiente']:
            guide=build_parking_preparation(snapshot(fase_procedimental=phase))
            self.assertNotIn('Consta una fase inicial',guide['procedure_note'])
        guide=build_parking_preparation(snapshot(fase_procedimental='notificación de denuncia e iniciación'))
        self.assertIn('Consta una fase inicial',guide['procedure_note'])

    def test_specialist_keeps_a_blocked_draft_with_no_invented_grounds(self):
        facts, family = _records(CASE, DOC, snapshot().facts)
        preview = build_legal_preview(facts, family)
        self.assertEqual(preview.family, 'estacionamiento')
        self.assertEqual(preview.status.value, 'draft')
        self.assertIsNone(preview.approved_by)
        self.assertIsNone(preview.frozen_at)
        self.assertEqual(preview.legal_arguments, [])
        self.assertEqual(preview.requested_outcomes, [])
        self.assertEqual(preview.deadlines[0].calculation_status, 'unresolved')
        self.assertEqual(len(preview.missing_items), 8)
        self.assertTrue(all(item.severity.value == 'blocking' for item in preview.missing_items))
        self.assertEqual(preview.documents_used[0].pages_used, [0])

    def test_specialist_requires_exact_frozen_locked_chain_and_never_mutates_it(self):
        facts, family = _records(CASE, DOC, snapshot().facts)
        cases = [(replace(facts, frozen=False), family), (facts, replace(family, locked=False)),
                 (replace(facts, case_id='other'),family), (facts,replace(family, validated_facts_id='other')),
                 (facts,replace(family, payload_sha256='a'*64)),
                 (replace(facts, payload_sha256='b'*64),family),
                 (replace(facts, facts=facts.facts.model_copy(update={'frozen':False})),family)]
        before = model_digest(facts.facts), model_digest(family.resolution)
        for pair in cases:
            with self.assertRaises(HTTPException):build_legal_preview(*pair)
        self.assertEqual(before,(model_digest(facts.facts),model_digest(family.resolution)))


if __name__ == '__main__': unittest.main()
