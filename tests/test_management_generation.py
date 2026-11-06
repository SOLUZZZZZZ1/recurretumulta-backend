"""Payment fulfillment contracts. Provider output is explicitly synthetic."""
import copy
from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sqlalchemy import text
from tests import test_management_journey as journey_fixture
from rtm_core import management_generation as gen
from rtm_core.management_contract import ManagementRejected
from rtm_core.management_payments import apply_provider_read, obligation
from rtm_core.management_drafts import version, save as save_draft
from rtm_core.management_generation_provider import generate


def output(body='Argumento sintético que desarrolla exclusivamente los hechos revisados.'):
    return {'proposed':{'primary_strategy':'Estrategia sintética ceñida a los hechos revisados.',
        'legal_arguments':[{'title':'Argumento de ensayo','body':body,'fact_keys':['facts']}],
        'requested_outcomes':['Petición sintética para este ensayo.'],'review_notes':['Revisión profesional pendiente.']},
        'provider':'synthetic','model':'fixture','response_id':'fixture','prompt_version':'rtm_paid_generation_v1'}


class GenerationTest(unittest.TestCase):
    def setUp(self):
        self.f=journey_fixture.JourneyTest();self.f.setUp();self.conn=self.f.conn
        @contextmanager
        def begin():yield self.conn
        self.engine=SimpleNamespace(begin=begin)
        self.docs=patch.object(gen,'document_row',return_value={});self.docs.start()
        self.custody=patch.object(gen,'read_custody',return_value=(b'synthetic',self.f.docs[0]['observed_sha256'],'verified'));self.custody.start()

    def tearDown(self):
        self.custody.stop();self.docs.stop();self.f.tearDown()

    def paid(self):
        q,row=self.f.reserved();self.f.settle(row);return q,row

    def run_job(self,provider=lambda inputs:output(),now=1000):
        return gen.run_one(self.engine,self.f.policy,provider=provider,now=now)

    def test_partial_unpaid_unknown_and_mismatched_amount_never_queue(self):
        self.assertEqual(gen.projection(self.conn,self.f.case)['state'],'awaiting_payment')
        q,row=self.f.reserved()
        for status in ('open','complete'):
            apply_provider_read(self.conn,row,self.f.provider(row,status),provider_account_id=self.f.policy.account_id)
            self.assertIsNone(gen.job_row(self.conn,self.f.case))
        bad=self.f.provider(row);bad['amount_total']-=1
        with self.assertRaises(ManagementRejected):apply_provider_read(self.conn,row,bad,provider_account_id=self.f.policy.account_id)
        self.assertIsNone(gen.job_row(self.conn,self.f.case))

    def test_payment_without_browser_queues_once_and_generates_once(self):
        q,row=self.paid();self.f.settle(row);self.f.settle(row,'evt_other')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_generation_jobs')).scalar(),1)
        calls=[]
        self.assertTrue(self.run_job(lambda inputs:(calls.append(inputs),output())[1]))
        self.assertFalse(self.run_job(lambda _:self.fail('Completed job repeated')))
        self.assertEqual(len(calls),1)
        draft=version(self.conn,self.f.case)['snapshot']
        self.assertIn('Argumento sintético',draft['content']);self.assertIn('PERSONA SINTÉTICA',draft['content'])
        self.assertIsNone(draft['author']['id']);self.assertIsNone(draft['reviewed_legal_inputs'])
        self.assertFalse(draft['approved']);self.assertFalse(draft['presented'])
        self.assertEqual(gen.projection(self.conn,self.f.case,public=True),{'state':'completed'})
        self.f.settle(row,'evt_after_generation')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_draft_versions')).scalar(),1)

    def test_payment_and_queue_roll_back_together_then_recover_same_notice(self):
        quote,row=self.f.reserved()
        savepoint=self.conn.begin_nested()
        self.f.settle(row)
        self.assertIsNotNone(gen.job_row(self.conn,self.f.case))
        savepoint.rollback()
        self.assertIsNone(gen.job_row(self.conn,self.f.case))
        self.assertNotEqual(obligation(self.conn,obligation_id=row['id'])['state'],'confirmed')
        self.f.settle(row)
        self.assertEqual(gen.job_row(self.conn,self.f.case)['state'],'pending')

    def test_transient_failure_retains_payment_and_retries_same_job(self):
        _,row=self.paid();jid=gen.job_row(self.conn,self.f.case)['id']
        def fail(_):raise TimeoutError('private payload must not be persisted')
        self.run_job(fail);self.assertEqual(gen.projection(self.conn,self.f.case)['state'],'retry')
        self.assertFalse(self.run_job(now=1059));self.assertTrue(self.run_job(now=1060))
        self.assertEqual(gen.job_row(self.conn,self.f.case)['id'],jid)
        self.assertEqual(obligation(self.conn,obligation_id=row['id'])['state'],'confirmed')
        self.assertEqual(gen.projection(self.conn,self.f.case)['state'],'completed')

    def test_changed_source_blocks_then_explicit_retry_after_repair(self):
        self.paid()
        with patch.object(gen,'read_custody',return_value=(b'changed','0'*64,'verified')):
            self.run_job(lambda _:self.fail('Provider must not see changed sources'))
        job=gen.job_row(self.conn,self.f.case);self.assertEqual(job['state'],'blocked')
        self.assertEqual(job['last_code'],'DRAFT_SOURCE_CHANGED_REVIEW_REQUIRED')
        gen.retry(self.conn,self.f.case,job['id'],self.f.policy,self.f.actor.operator_id)
        self.run_job();self.assertEqual(gen.projection(self.conn,self.f.case)['state'],'completed')

    def test_changed_identity_during_model_call_never_saves_stale_output(self):
        self.paid()
        def changed(inputs):
            identity={**inputs['case']['interested_data'],'full_name':'OTRA IDENTIDAD SINTÉTICA'}
            self.conn.execute(text('UPDATE cases SET interested_data=:data WHERE id=:id'),{'data':json.dumps(identity),'id':self.f.case})
            return output()
        self.run_job(changed)
        self.assertEqual(gen.job_row(self.conn,self.f.case)['last_code'],'GENERATION_INPUT_CHANGED')
        self.assertIsNone(version(self.conn,self.f.case))

    def test_second_worker_and_stale_lease_cannot_duplicate_or_overwrite(self):
        self.paid();calls=[]
        def first(inputs):
            self.assertFalse(self.run_job(lambda _:self.fail('Active lease duplicated'),now=1001))
            self.assertTrue(self.run_job(lambda _:(calls.append('recovered'),output('Resultado sintético de la recuperación tras expirar la reserva.'))[1],now=1301))
            return output('RESULTADO TARDÍO SINTÉTICO QUE NO PUEDE SOBRESCRIBIR.')
        self.run_job(first)
        self.assertEqual(calls,['recovered']);content=version(self.conn,self.f.case)['snapshot']['content']
        self.assertIn('recuperación tras expirar',content);self.assertNotIn('RESULTADO TARDÍO',content)
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_draft_versions')).scalar(),1)

    def test_operator_version_created_during_generation_is_preserved(self):
        quote,row=self.paid()
        def concurrent(inputs):
            with patch('rtm_core.management_drafts.document_row',return_value={}),patch('rtm_core.management_drafts.read_custody',return_value=(b'synthetic',self.f.docs[0]['observed_sha256'],'verified')):
                save_draft(self.conn,case_id=self.f.case,author=self.f.actor,body=self.f.draft_input(quote),policy=self.f.policy)
            return output()
        self.run_job(concurrent)
        self.assertEqual(gen.job_row(self.conn,self.f.case)['last_code'],'EXISTING_VERSION_PRESERVED')
        self.assertEqual(version(self.conn,self.f.case)['snapshot']['author']['id'],self.f.actor.operator_id)

    def test_foreign_scope_and_unknown_fact_are_denied(self):
        self.paid();policy=copy.copy(self.f.policy);policy.account_id='acct_other'
        with self.assertRaises(ManagementRejected):gen.run_one(self.engine,policy,provider=lambda _:self.fail('Wrong account reached provider'))
        bad=output();bad['proposed']['legal_arguments'][0]['fact_keys']=['invented_fact']
        self.run_job(lambda _:bad)
        self.assertEqual(gen.job_row(self.conn,self.f.case)['last_code'],'GENERATION_UNSUPPORTED_FACT_REFERENCE')
        self.assertIsNone(version(self.conn,self.f.case))

    def test_real_sdk_wire_contract_with_synthetic_http(self):
        import httpx,openai
        self.paid();job=gen.job_row(self.conn,self.f.case);inputs=gen.material(self.conn,job,self.f.policy)
        captured=[];actual=openai.OpenAI
        def http(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'id':'chatcmpl_fixture','object':'chat.completion','created':1,'model':'fixture',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(output()['proposed'])}}]})
        def factory(**kwargs):return actual(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(http)))
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic-not-a-key','RTM_MANAGEMENT_GENERATION_MODEL':'fixture'}),patch('openai.OpenAI',side_effect=factory):
            result=generate(inputs)
        self.assertEqual(result['provider'],'openai');self.assertEqual(len(captured),1)
        self.assertEqual(captured[0]['max_completion_tokens'],10000)
        self.assertEqual(captured[0]['response_format']['type'],'json_schema')
        self.assertNotIn('PERSONA SINTÉTICA',captured[0]['messages'][1]['content'])
