"""Offline orchestration tests. Fixtures are not real Kiro/AWS execution evidence."""
import argparse
import importlib.util
import json
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import kiro_workflow as flow
import pilot


class WorkflowTests(unittest.TestCase):
    def test_default_assessment_limit_holds_a_full_export(self):
        self.assertEqual(flow.DEFAULTS['max_assessment_bytes'], 250000000)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'rule.cbl').write_text('IF AMOUNT = ZERO\n MOVE 1 TO ERROR-FLAG\nEND-IF\n')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.assessment = self.root / 'findings'
        self.assessment.mkdir()
        (self.assessment / 'rules.json').write_text('{"candidates":["reject zero amount"]}')
        self.commit = 'a' * 40
        self.dirty = False
        def git(root, *args):
            if args == ('rev-parse', 'HEAD'): return self.commit
            if args == ('remote', 'get-url', 'origin'): return 'https://example.invalid/source.git'
            if args == ('status', '--porcelain'): return ' M rule.cbl' if self.dirty else ''
            if args[0] == 'ls-files': return 'rule.cbl' if len(args) == 1 or args[-1] == 'rule.cbl' else ''
            raise AssertionError(args)
        for module in [flow, pilot]:
            mock = patch.object(module, 'git', side_effect=git)
            mock.start()
            self.addCleanup(mock.stop)
        args = argparse.Namespace(source=str(self.source), workspace=str(self.workspace), settings=None,
                                  objective='Validate one rule', assessment=str(self.assessment))
        self.directory = flow.initialize(args)
        self.messages = []

    def load(self): return json.loads((self.directory / 'run.json').read_text())
    def store(self, value): flow.private_save(self.directory / 'run.json', value)
    def scripted(self, replies):
        remaining = iter(replies)
        def runtime(run, directory, role, message):
            self.messages.append((role, json.loads(message)))
            return next(remaining)
        return runtime

    def candidate(self):
        return {'kind': 'candidate', 'specification': 'Fixture candidate: reject zero amount; no runtime proof.',
                'traceability': [{'rule_id': 'R1', 'source_path': 'rule.cbl', 'source_anchor': 'lines 1-3',
                    'scenario_id': str(i), 'expected_result': 'Source-derived fixture expectation',
                    'oracle': 'rule.cbl lines 1-3', 'status': 'NOT RUN'} for i in range(3)]}

    def read(self): return {'kind': 'read', 'area': 'source', 'paths': ['rule.cbl']}
    def review(self, decision='PASS'):
        return {'kind': 'review', 'decision': decision, 'report': 'Independent fixture inspection.',
                'findings': [] if decision == 'PASS' else [{'id': 'F1', 'severity': 'high', 'correction': 'Fix oracle'}]}

    def test_two_roles_and_automatic_handshake(self):
        result = flow.drive(self.directory, runtime=self.scripted([self.read(), self.candidate(), self.read(), self.review()]))
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertEqual([x[0] for x in self.messages], ['executor', 'executor', 'reviewer', 'reviewer'])
        self.assertNotIn('candidate', self.messages[0][1])
        self.assertIn('candidate', self.messages[2][1])
        self.assertEqual(self.messages[2][1]['history'], [])
        self.assertFalse(result['human_approved'])

    def test_rework_is_routed_without_user_prompt(self):
        replies = [self.read(), self.candidate(), self.read(), self.review('REWORK'),
                   self.read(), self.candidate(), self.read(), self.review()]
        result = flow.drive(self.directory, runtime=self.scripted(replies))
        self.assertEqual(result['round'], 2)
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertIn('previous_review', self.messages[4][1])
        self.assertTrue((self.directory / 'candidate-1.json').exists())
        self.assertTrue((self.directory / 'candidate-2.json').exists())

    def test_reviewer_cannot_skip_independent_source_read(self):
        result = flow.drive(self.directory, runtime=self.scripted([self.read(), self.candidate(), self.review()]))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('independently', result['reason'])

    def test_total_call_budget_survives_resume(self):
        run = self.load(); run['settings']['max_model_calls'] = 1; self.store(run)
        result = flow.drive(self.directory, runtime=self.scripted([self.read()]))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.load()['model_calls'], 1)
        self.assertIn('budget exhausted', result['reason'])

    def test_candidate_changed_before_review_blocks(self):
        run = self.load(); flow.collect_assessment(run, self.directory, str(self.assessment))
        runtime = self.scripted([self.read(), self.candidate()])
        flow.agent_step(run, self.directory, runtime); flow.agent_step(run, self.directory, runtime)
        (self.directory / 'candidate-1.json').write_text('{}')
        with self.assertRaisesRegex(flow.Blocked, 'changed after handoff'):
            flow.agent_step(run, self.directory, runtime)

    def test_scope_escape_is_rejected(self):
        run = self.load()
        with self.assertRaisesRegex(flow.Blocked, 'Rejected path'):
            flow.source_read(run, self.directory, 'executor', {'paths': ['../private.txt']})

    def test_partial_source_reads_keep_original_line_numbers(self):
        run = self.load()
        result = flow.source_read(run, self.directory, 'executor', {'paths':['rule.cbl'], 'start':2, 'end':3})
        self.assertTrue(result[0]['text'].startswith('2:'))
        self.assertNotIn('1: IF', result[0]['text'])

    def test_changed_source_never_reaches_agent(self):
        self.dirty = True
        result = flow.drive(self.directory, runtime=lambda *args: self.fail('Agent must not run'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('not clean', result['reason'])

    def test_runtime_setup_is_resumable(self):
        def unavailable(*args): raise flow.RuntimeUnavailable('Authenticate and resume')
        first = flow.drive(self.directory, runtime=unavailable)
        self.assertEqual(first['status'], 'WAITING_FOR_RUNTIME')
        result = flow.drive(self.directory, runtime=self.scripted([self.read(), self.candidate(), self.read(), self.review()]))
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertEqual(result['model_calls'], 5)

    def test_import_rejects_oversized_assessment(self):
        run = self.load(); run['settings']['max_assessment_bytes'] = 1
        with self.assertRaisesRegex(flow.Blocked, 'byte limit'):
            flow.collect_assessment(run, self.directory, str(self.assessment))

    def test_nested_aws_envelope_and_error(self):
        envelope = {'content':[{'type':'text','text':json.dumps({'success':True,'data':{'job':{'jobId':'fixture-job'}}})}], 'isError':False}
        self.assertEqual(flow.unwrap_mcp(envelope)['job']['jobId'], 'fixture-job')
        with self.assertRaises(flow.Blocked): flow.unwrap_mcp({'success':False,'error':{'code':'AUTH'}})

    def prepare_job(self):
        run = self.load(); run['assessment_input'] = None
        run['settings']['transform'] = {'workspace_id':'fixture-workspace', 'orchestrator_agent':'fixture-mainframe'}
        run['job_id'] = 'fixture-job'
        self.store(run)
        return run

    def test_mainframe_orchestrator_discovered_from_service(self):
        run=self.load();run['settings']['transform']={}
        class Client:
            def calls(self,requests):return [{'items':[{'name':'fixture-mainframe-v2','type':'ORCHESTRATOR_AGENT','jobOrchestrator':True,'ownerType':'INTERNAL_AGENT'}]}]
        self.assertEqual(flow.select_orchestrator(run,Client()),'fixture-mainframe-v2')
        self.assertEqual(run['settings']['transform']['orchestrator_agent'],'fixture-mainframe-v2')

    def test_ambiguous_orchestrators_not_guessed(self):
        run=self.load();run['settings']['transform']={}
        class Client:
            def calls(self,requests):return [{'items':[{'name':name,'type':'ORCHESTRATOR_AGENT','jobOrchestrator':True,'ownerType':'INTERNAL_AGENT'} for name in ['mainframe-one','mainframe-two']]}]
        with self.assertRaisesRegex(flow.Blocked,'No unique'):flow.select_orchestrator(run,Client())

    def test_explicit_orchestrator_never_discovered_again(self):
        run=self.load();run['settings']['transform']={'orchestrator_agent':'fixture-pinned'}
        class Client:
            def calls(self,requests):raise AssertionError('No discovery')
        self.assertEqual(flow.select_orchestrator(run,Client()),'fixture-pinned')

    def test_pending_task_pauses_even_when_job_is_executing(self):
        self.prepare_job()
        class Client:
            def calls(self,requests):return [{}, {'job':{'job':{'jobId':'fixture-job','statusDetails':{'status':'EXECUTING'}}},'tasks':{'hitlTasks':[{'status':'AWAITING_HUMAN_INPUT'}]}}]
        self.assertEqual(flow.drive(self.directory,transform=Client())['status'],'WAITING_FOR_HUMAN')

    def onboarding(self):
        run = self.prepare_job()
        run['settings']['transform']['connector_id'] = 'fixture-connector'
        run['uploaded_source'] = {'uri': 's3://fixture-bucket/source.zip'}
        snapshot = {'plan': {'steps': []}, 'messages': {'messages': [
            {'messageId':'new', 'createdAt':'2026-11-03', 'messageOrigin':'SYSTEM',
             'processingInfo':{'messageType':'FINAL_RESPONSE'},
             'text':'Please confirm custom assessment-only capabilities'},
            {'messageId':'old', 'createdAt':'2026-11-02', 'messageOrigin':'SYSTEM',
             'processingInfo':{'messageType':'FINAL_RESPONSE'}, 'text':'Old greeting'}]}}
        return run, snapshot, {'workspaceId':'fixture-workspace','jobId':'fixture-job'}

    def test_plan_selection_uses_pinned_context_and_never_repeats_same_reply(self):
        run, snapshot, identity = self.onboarding()
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                return [{}, {'accepted':True}]
        self.assertTrue(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertFalse(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual([name for name,_ in calls],['load_instructions','send_message'])
        sent = calls[-1][1]['text']
        for value in ['Validate one rule','Analyze code','Extract business logic','not deliverable approval',
                      'Do not include Analyze activity metrics']:
            self.assertIn(value,sent)
        for value in ['fixture-job','fixture-connector','s3://fixture-bucket/source.zip',self.commit]:
            self.assertNotIn(value,sent)
        self.assertTrue((self.directory/'plan-bootstrap-1-receipt.json').exists())

    def test_uncertain_plan_message_requires_reconciliation(self):
        run, snapshot, identity = self.onboarding()
        class Client:
            def calls(self, requests): raise flow.Blocked('Lost response')
        with self.assertRaises(flow.Blocked):
            flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot)
        recovered = self.load()
        self.assertTrue(flow.bootstrap_assessment_plan(recovered,self.directory,Client(),identity,snapshot))
        self.assertEqual(recovered['status'],'NEEDS_RECONCILIATION')

    def test_empty_chat_without_a_plan_sends_the_objective_once(self):
        run, snapshot, identity = self.onboarding()
        snapshot['messages'] = {'messages': []}
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                return [{}, {'accepted': True}]
        self.assertTrue(flow.bootstrap_assessment_plan(run, self.directory, Client(), identity, snapshot))
        sent = [args['text'] for name, args in calls if name == 'send_message']
        self.assertEqual(len(sent), 1)
        self.assertIn('Analyze code', sent[0])
        self.assertIn('Do not include Analyze activity metrics', sent[0])
        for value in ('fixture-job', 'fixture-connector', 's3://fixture-bucket/source.zip'):
            self.assertNotIn(value, sent[0])
        calls.clear()
        self.assertTrue(flow.bootstrap_assessment_plan(run, self.directory, Client(), identity, snapshot))
        self.assertEqual(calls, [])
        self.assertIn('waiting for AWS to generate the plan', run['reason'])

    def test_saved_initial_plan_receipt_is_not_sent_again(self):
        run, snapshot, identity = self.onboarding()
        snapshot['messages'] = {'messages': []}
        run['plan_bootstrap'] = {'sent_messages': [], 'attempts': 0}
        flow.private_save(self.directory / 'plan-bootstrap-1-receipt.json', {'accepted': True})
        class Client:
            def calls(self, requests): raise AssertionError('Recorded plan message must not be repeated')
        self.assertTrue(flow.bootstrap_assessment_plan(run, self.directory, Client(), identity, snapshot))
        self.assertIn('initial-no-plan', run['plan_bootstrap']['sent_messages'])

    def test_existing_plan_and_unrecognized_chat_never_trigger_selection(self):
        run, snapshot, identity = self.onboarding()
        class Client:
            def calls(self, requests): raise AssertionError('No chat mutation expected')
        snapshot['plan']['steps'] = [{'stepName':'Kick off'}]
        self.assertFalse(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        snapshot['plan']['steps'] = []
        snapshot['messages']['messages'][0]['text'] = 'Approve the generated business rules and deploy'
        self.assertFalse(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))

    def test_plan_message_receipt_recovers_after_local_state_write_interruption(self):
        run, snapshot, identity = self.onboarding()
        run['plan_bootstrap'] = {'pending':True,'pending_attempt':1,'pending_message':'new',
                                 'attempts':0,'sent_messages':[]}
        flow.private_save(self.directory/'plan-bootstrap-1-receipt.json',{'accepted':True})
        class Client:
            def calls(self, requests): raise AssertionError('Successful message must not be repeated')
        self.assertFalse(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertFalse(run['plan_bootstrap']['pending'])
        self.assertEqual(run['plan_bootstrap']['attempts'],1)

    def test_deliverable_task_prevents_plan_chat_mutation(self):
        run,snapshot,identity=self.onboarding()
        self.store(run)
        snapshot.update(job={'jobId':'fixture-job','status':'EXECUTING'},
                        tasks={'hitlTasks':[{'status':'AWAITING_APPROVAL'}]})
        class Client:
            def calls(self, requests):
                assert [key for key,_ in requests] == ['load_instructions','get_job_status']
                return [{},snapshot]
        self.assertEqual(flow.drive(self.directory,transform=Client())['status'],'WAITING_FOR_HUMAN')

    def test_repeated_onboarding_is_bounded(self):
        run, snapshot, identity = self.onboarding()
        class Client:
            def calls(self, requests): return [{}, {'accepted':True}]
        for number in range(3):
            snapshot['messages']['messages'][0]['messageId'] = str(number)
            flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot)
        self.assertEqual(run['plan_bootstrap']['attempts'],2)
        self.assertEqual(run['status'],'WAITING_FOR_HUMAN')

    def test_option_three_confirmation_is_sent_once_after_the_objective(self):
        run, snapshot, identity = self.onboarding()
        run['plan_bootstrap'] = {'pending':False,'attempts':2,'sent_messages':['older'],'confirmation_sent':False}
        snapshot['messages']['messages'][0]['text'] = (
            'Select option 3 to build a custom plan with Analyze code. Please confirm your choice.')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                return [{}, {'accepted':True}]
        self.assertTrue(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        sent = [args['text'] for name,args in calls if name=='send_message']
        self.assertEqual(len(sent),1)
        self.assertIn('Select option 3', sent[0])
        self.assertNotIn('fixture-job', sent[0])
        snapshot['messages']['messages'][0]['messageId'] = 'again'
        self.assertTrue(flow.bootstrap_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name=='send_message' for name,_ in calls),1)
        self.assertEqual(run['status'],'WAITING_FOR_HUMAN')

    def execution_fixture(self):
        run, snapshot, identity = self.onboarding()
        run['settings']['allow_new_assessments'] = True
        snapshot['plan']['steps'] = [{'stepName':name,'status':'NOT_STARTED'} for name in
            ['Kick off modernization','Analyze code','Analyze data','Generate technical documentation','Extract business logic']]
        snapshot['messages']['messages'][0]['text'] = 'The plan is ready. I can start the execution.'
        return run, snapshot, identity

    def test_completed_assessment_plan_exports_while_optional_invitation_remains(self):
        run, snapshot, identity = self.execution_fixture()
        for step in snapshot['plan']['steps']: step['status'] = 'SUCCEEDED'
        self.assertTrue(flow.assessment_plan_complete(snapshot))
        snapshot.update(job={'jobId':'fixture-job','status':'EXECUTING'}, tasks={'hitlTasks':[
            {'status':'AWAITING_HUMAN_INPUT','blockingType':'NON_BLOCKING'}]})
        class Client:
            def calls(self, requests): return [{},snapshot]
        with patch.object(flow,'export_findings') as export:
            flow.assessment_step(run,self.directory,Client())
            export.assert_called_once()
        self.assertEqual(run['assessment_completion']['service_job_status'],'EXECUTING')
        snapshot['plan']['steps'].pop()
        self.assertFalse(flow.assessment_plan_complete(snapshot))

    def test_execution_start_is_exact_and_receipted_once(self):
        run, snapshot, identity = self.execution_fixture()
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        for _ in range(2):
            self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name == 'send_message' for name,_ in calls),1)
        self.assertTrue((self.directory/'plan-execution-receipt.json').exists())

    def test_ready_to_execute_wording_starts_the_plan(self):
        run, snapshot, identity = self.execution_fixture()
        snapshot['messages']['messages'][0]['text'] = (
            'Plan generated successfully. Ready to execute. '
            'Would you like to proceed with running the plan now?')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name == 'send_message' for name,_ in calls),1)

    def test_activity_metrics_plan_is_rebuilt_once_and_not_started(self):
        run, snapshot, identity = self.execution_fixture()
        snapshot['plan']['steps'].append({'stepName':'Analyze activity metrics','status':'NOT_STARTED'})
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.exclude_activity_metrics(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name == 'send_message' for name,_ in calls),1)
        self.assertIn('without Analyze activity metrics', calls[-1][1]['text'])
        self.assertIn('Analyze data', calls[-1][1]['text'])
        self.assertFalse(flow.assessment_plan_authorized(snapshot['plan']['steps']))
        self.assertTrue(flow.exclude_activity_metrics(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name == 'send_message' for name,_ in calls),1)
        self.assertEqual(run['status'],'WAITING_FOR_HUMAN')

    def test_requested_source_uri_is_sent_once_from_the_recorded_upload(self):
        run, snapshot, identity = self.execution_fixture()
        archive = self.directory / 'source.zip'
        archive.write_bytes(b'synthetic archive')
        run['uploaded_source'] = {'uri': 's3://fixture-bucket/source.zip', 'sha256': flow.sha(archive)}
        snapshot['messages']['messages'][0]['text'] = (
            'Please provide the S3 URI for your source code. '
            'Once you provide that, I will configure the inputs and start the plan execution.')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.supply_recorded_source(run,self.directory,Client(),identity,snapshot))
        self.assertTrue(flow.supply_recorded_source(run,self.directory,Client(),identity,snapshot))
        sent = [args['text'] for name,args in calls if name=='send_message']
        self.assertEqual(len(sent),1)
        self.assertIn('s3://fixture-bucket/source.zip', sent[0])
        self.assertNotIn('fixture-job', sent[0])
        self.assertNotIn('fixture-workspace', sent[0])
        self.assertNotIn('fixture-connector', sent[0])
        snapshot['messages']['messages'][0]['text'] = 'The plan is ready. I can start the execution.'
        (self.directory / 'source-chat-receipt.json').unlink()
        self.assertFalse(flow.supply_recorded_source(run,self.directory,Client(),identity,snapshot))

    def test_markdown_plan_announcement_starts_without_treating_approval_as_a_request(self):
        run, snapshot, identity = self.execution_fixture()
        snapshot['messages']['messages'][0]['text'] = (
            'Perfect. Your assessment plan has been **generated** with the following capabilities: '
            'Analyze code, Analyze data, Generate technical documentation, and Extract business logic. '
            'Not included: Analyze activity metrics. '
            'Plan scope: Assess the entire supplied repository with human approval of findings preserved. '
            'What would you like to do next?')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.assessment_plan_announced(snapshot['messages']['messages'][0]['text']))
        self.assertFalse(flow.assessment_plan_announced('Please approve the findings before anything else.'))
        self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        sent = [args['text'] for name,args in calls if name=='send_message']
        self.assertEqual(len(sent),1)
        self.assertIn('start the existing assessment plan now', sent[0])

    def test_generated_plan_that_asks_for_inputs_starts(self):
        run, snapshot, identity = self.execution_fixture()
        snapshot['messages']['messages'][0]['text'] = (
            'Your assessment plan has been generated with the following capabilities: '
            'Analyze code, Analyze data, Generate technical documentation, and Extract business logic. '
            'Next step: configure your assessment inputs. '
            'Would you like to proceed with configuring your inputs?')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        sent = [args['text'] for name,args in calls if name=='send_message']
        self.assertEqual(len(sent),1)
        self.assertIn('Configure the assessment inputs', sent[0])
        self.assertIn('start the existing assessment plan', sent[0])
        self.assertNotIn('s3://', sent[0])

    def test_generated_plan_without_a_start_phrase_still_starts(self):
        run, snapshot, identity = self.execution_fixture()
        snapshot['messages']['messages'][0]['text'] = (
            'Your custom assessment plan has been generated successfully. '
            'What would you like to do next?')
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests); return [{}, {'accepted':True}]
        self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(sum(name == 'send_message' for name,_ in calls),1)

    def test_execution_start_rejects_added_capability_and_uncertain_send(self):
        run, snapshot, identity = self.execution_fixture()
        class Client:
            def calls(self, requests): raise flow.Blocked('lost response')
        snapshot['plan']['steps'].append({'stepName':'Deploy','status':'NOT_STARTED'})
        with self.assertRaisesRegex(flow.Blocked,'differs'):
            flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot)
        snapshot['plan']['steps'].pop()
        with self.assertRaisesRegex(flow.Blocked,'lost response'):
            flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot)
        self.assertTrue(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        self.assertEqual(run['status'],'NEEDS_RECONCILIATION')

    def test_execution_start_ignores_running_plan_and_unrecognized_chat(self):
        run, snapshot, identity = self.execution_fixture()
        class Client:
            def calls(self, requests): raise AssertionError('No send expected')
        snapshot['plan']['steps'][0]['status'] = 'EXECUTING'
        self.assertFalse(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))
        snapshot['plan']['steps'][0]['status'] = 'NOT_STARTED'
        snapshot['messages']['messages'][0]['text'] = 'Approve the generated findings'
        self.assertFalse(flow.start_assessment_plan(run,self.directory,Client(),identity,snapshot))

    def source_location_fixture(self):
        run=self.prepare_job();run['settings']['allow_new_assessments']=True
        archive=self.directory/'source.zip';archive.write_bytes(b'synthetic archive')
        run['uploaded_source']={'uri':'s3://fixture-bucket/source.zip','sha256':flow.sha(archive)}
        identity={'workspaceId':'fixture-workspace','jobId':'fixture-job'}
        task={'taskId':'fixture-task','uxComponentId':'SpecifyAssetsLocation','status':'AWAITING_HUMAN_INPUT',
              'category':'REGULAR','taskType':'NORMAL','jobMetadata':identity,
              '_outputSchema':{'type':'object','required':['assetLocation'],
                               'properties':{'assetLocation':{'type':'string'}}}}
        return run,identity,task

    def activity_metrics_task(self):
        return {'taskId':'fixture-metrics','uxComponentId':'MainframeSMFConfigureComponent',
                'status':'AWAITING_HUMAN_INPUT','blockingType':'BLOCKING','category':'REGULAR',
                'taskType':'NORMAL','jobMetadata':{'workspaceId':'fixture-workspace','jobId':'fixture-job'}}

    def test_blank_activity_metrics_form_is_confirmed_once(self):
        run, identity, _task = self.source_location_fixture()
        waiting = self.activity_metrics_task()
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                if requests[-1][0] == 'get_resource':
                    return [{}, {'task': waiting, 'agentArtifactContent': {'properties': {
                        'sourceCodeS3Path':'', 'scrtFileS3Path':'', 'smfNewCustomerFeatures':True}}}]
                return [{}, {'accepted':True}]
        self.assertTrue(flow.continue_without_activity_records(run,self.directory,Client(),identity,[waiting]))
        self.assertTrue(flow.continue_without_activity_records(run,self.directory,Client(),identity,[waiting]))
        submitted = [args for name, args in calls if name == 'complete_task']
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]['content'], '{}')
        self.assertEqual(submitted[0]['action'], 'REJECT')
        run['smf_source'] = {'uri':'s3://fixture-bucket/smf/'}
        self.assertFalse(flow.continue_without_activity_records(run,self.directory,Client(),identity,[waiting]))

    def test_filled_activity_metrics_path_is_not_replaced(self):
        run, identity, _task = self.source_location_fixture()
        waiting = self.activity_metrics_task()
        class Client:
            def calls(self, requests):
                return [{}, {'task': waiting, 'agentArtifactContent': {'properties': {
                    'sourceCodeS3Path':'s3://fixture-bucket/smf/','scrtFileS3Path':''}}}]
        with self.assertRaisesRegex(flow.Blocked, 'already names'):
            flow.continue_without_activity_records(run,self.directory,Client(),identity,[waiting])

    def test_source_task_submits_recorded_zip_once_without_other_options(self):
        run,identity,task=self.source_location_fixture();calls=[]
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                return [{}, {'task':task} if requests[-1][0]=='get_resource' else {'status':'SUBMITTED'}]
        for _ in range(2):
            self.assertTrue(flow.configure_source_task(run,self.directory,Client(),identity,[task]))
        submits=[args for name,args in calls if name=='complete_task']
        self.assertEqual(len(submits),1)
        self.assertEqual(json.loads(submits[0]['content']),{'assetLocation':'s3://fixture-bucket/source.zip'})
        self.assertTrue((self.directory/'source-location-submit-receipt.json').exists())

    def test_source_task_changed_schema_or_archive_is_not_submitted(self):
        run,identity,task=self.source_location_fixture()
        task['_outputSchema']['required'].append('newDecision')
        class Client:
            def calls(self, requests):
                assert requests[-1][0]=='get_resource'
                return [{},{'task':task}]
        with self.assertRaisesRegex(flow.Blocked,'schema changed'):
            flow.configure_source_task(run,self.directory,Client(),identity,[task])
        (self.directory/'source.zip').write_bytes(b'changed')
        with self.assertRaisesRegex(flow.Blocked,'archive changed'):
            flow.configure_source_task(run,self.directory,Client(),identity,[task])

    def test_uncertain_source_submission_is_not_repeated(self):
        run,identity,task=self.source_location_fixture()
        class Client:
            def calls(self, requests):
                if requests[-1][0]=='complete_task':raise flow.Blocked('Lost response')
                return [{},{'task':task}]
        with self.assertRaises(flow.Blocked):
            flow.configure_source_task(run,self.directory,Client(),identity,[task])
        run=self.load()
        self.assertTrue(flow.configure_source_task(run,self.directory,Client(),identity,[task]))
        self.assertEqual(run['status'],'NEEDS_RECONCILIATION')

    def test_nonblocking_collaborator_task_does_not_stop_assessment(self):
        self.prepare_job()
        class Client:
            def calls(self, requests):
                return [{},{'job':{'jobId':'fixture-job','status':'EXECUTING'},
                            'tasks':{'hitlTasks':[{'uxComponentId':'InviteCollaboratorsContent',
                              'status':'AWAITING_HUMAN_INPUT','blockingType':'NON_BLOCKING'}]},
                            '_pollingGuidance':{'hasPendingTasks':True}}]
        self.assertEqual(flow.drive(self.directory,transform=Client())['status'],'WAITING_FOR_ASSESSMENT')

    def test_aws_job_wait_and_resume_reuses_job(self):
        self.prepare_job()
        class Client:
            def __init__(self): self.names = []
            def calls(self, requests):
                self.names += [x[0] for x in requests]
                return [{}, {'job':{'jobId':'fixture-job','status':'RUNNING'}}]
        client = Client()
        first = flow.drive(self.directory, transform=client)
        second = flow.drive(self.directory, transform=client)
        self.assertEqual(first['status'], 'WAITING_FOR_ASSESSMENT')
        self.assertEqual(second['status'], 'WAITING_FOR_ASSESSMENT')
        self.assertNotIn('create_job', client.names)
        self.assertEqual(self.load()['job_id'], 'fixture-job')

    def test_aws_human_input_is_not_auto_submitted(self):
        self.prepare_job()
        class Client:
            def calls(self, requests):
                assert [x[0] for x in requests] == ['load_instructions','get_job_status']
                return [{}, {'job':{'jobId':'fixture-job','status':'AWAITING_HUMAN_INPUT'}}]
        result = flow.drive(self.directory, transform=Client())
        self.assertEqual(result['status'], 'WAITING_FOR_HUMAN')

    def test_failed_aws_job_stops_without_saying_it_continues(self):
        self.prepare_job()
        class Client:
            def calls(self, requests):
                return [{}, {'job':{'jobId':'fixture-job','status':'FAILED'}}]
        result = flow.drive(self.directory, transform=Client())
        self.assertEqual(result['status'], 'ASSESSMENT_FAILED')
        self.assertNotIn('continues', result['reason'])

    def test_unknown_creation_never_retries_create(self):
        run = self.prepare_job(); run['job_id'] = None; run['create_pending'] = True; self.store(run)
        class Client:
            def calls(self, requests): raise AssertionError('Must not call service on uncertain creation')
        result = flow.drive(self.directory, transform=Client())
        self.assertEqual(result['status'], 'NEEDS_RECONCILIATION')

    def test_receipted_creation_recovers_without_duplicate(self):
        run = self.prepare_job(); run['job_id'] = None; run['create_pending'] = True; self.store(run)
        flow.private_save(self.directory / 'create-job-receipt.json', {'job':{'jobId':'fixture-job'}})
        class Client:
            def calls(self, requests):
                assert all(name != 'create_job' for name, _ in requests)
                return [{}, {'job':{'jobId':'fixture-job','status':'RUNNING'}}]
        result = flow.drive(self.directory, transform=Client())
        self.assertEqual(result['status'], 'WAITING_FOR_ASSESSMENT')
        self.assertEqual(self.load()['job_id'], 'fixture-job')

    def test_new_assessment_is_created_once_and_receipted(self):
        run = self.prepare_job(); run['job_id'] = None
        run['settings']['allow_new_assessments'] = True
        run['settings']['transform']['upload_prefix'] = 's3://fixture-bucket/private-inputs'
        self.store(run)
        class Client:
            def __init__(self): self.created = 0
            def calls(self, requests):
                results = []
                for name, arguments in requests:
                    if name == 'create_job':
                        self.created += 1
                        assert 'source.zip' in arguments['objective']
                        results.append({'job':{'jobId':'fixture-job'}})
                    elif name == 'get_job_status':
                        results.append({'job':{'jobId':'fixture-job','status':'RUNNING'}})
                    else: results.append({})
                return results
        client = Client()
        completed = type('Result', (), {'returncode':0})()
        with patch.object(flow.subprocess, 'run', return_value=completed):
            first = flow.drive(self.directory, transform=client)
            second = flow.drive(self.directory, transform=client)
        self.assertEqual(client.created, 1)
        self.assertEqual(first['status'], 'WAITING_FOR_ASSESSMENT')
        self.assertEqual(second['status'], 'WAITING_FOR_ASSESSMENT')
        self.assertTrue((self.directory / 'create-job-receipt.json').is_file())

    def test_uncertain_new_creation_is_not_repeated(self):
        run = self.prepare_job(); run['job_id'] = None
        run['settings']['allow_new_assessments'] = True
        run['settings']['transform']['upload_prefix'] = 's3://fixture-bucket/private-inputs'
        self.store(run)
        class Client:
            def __init__(self): self.created = 0
            def calls(self, requests):
                if requests[0][0] == 'create_job':
                    self.created += 1
                    raise flow.Blocked('Connection lost after request')
                return [{}]
        client = Client()
        completed = type('Result', (), {'returncode':0})()
        with patch.object(flow.subprocess, 'run', return_value=completed):
            first = flow.drive(self.directory, transform=client)
            second = flow.drive(self.directory, transform=client)
        self.assertEqual(client.created, 1)
        self.assertEqual(first['status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(second['status'], 'NEEDS_RECONCILIATION')

    def test_poll_watcher_returns_without_stopping_aws_job(self):
        run = self.prepare_job(); run['settings']['max_polls_per_resume'] = 2; self.store(run)
        class Client:
            def __init__(self): self.polls = 0
            def calls(self, requests):
                self.polls += 1
                assert all(name != 'control_job' for name, _ in requests)
                return [{}, {'job':{'jobId':'fixture-job','status':'RUNNING'}}]
        client = Client()
        with patch.object(flow.time, 'sleep'):
            result = flow.drive(self.directory, wait=True, transform=client)
        self.assertEqual(client.polls, 2)
        self.assertEqual(result['status'], 'WAITING_FOR_ASSESSMENT')

    def test_real_subprocess_adapter_has_tool_free_profiles(self):
        run = self.load(); run['model_calls'] = 1
        program = 'import sys; sys.stdin.read(); print(\'{"kind":"blocked","reason":"fixture only"}\')'
        run['settings']['kiro_command'] = [sys.executable, '-c', program]
        with patch.dict(flow.os.environ, {'KIRO_API_KEY':'offline-fixture-value'}):
            result = flow.kiro_call(run, self.directory, 'executor', 'Synthetic fixture context')
        self.assertEqual(result['kind'], 'blocked')
        profile = json.loads((self.directory / 'runtime/executor/.kiro/agents/modernization-executor.json').read_text())
        self.assertEqual(profile['tools'], [])
        self.assertFalse(profile['includeMcpJson'])
        self.assertFalse(profile['includePowers'])
        self.assertEqual(profile['permissions']['rules'][0]['effect'], 'deny')

    def test_completed_assessment_drives_agents_automatically(self):
        run = self.prepare_job()
        run['settings']['transform']['output_location'] = str(self.assessment)
        self.store(run)
        class Client:
            def calls(self, requests): return [{}, {'job':{'jobId':'fixture-job','status':'COMPLETED'}}]
        result = flow.drive(self.directory, transform=Client(),
            runtime=self.scripted([self.read(),self.candidate(),self.read(),self.review()]))
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertEqual(self.load()['job_id'], 'fixture-job')

    def test_s3_findings_are_imported_without_creating_a_job(self):
        run = self.load(); run['assessment_input'] = 's3://fixture-bucket/findings/'
        self.store(run)
        calls = []
        def aws(command, **kwargs):
            calls.append(command)
            if 'list-objects-v2' in command:
                payload = {'Contents':[{'Key':'findings/rule.json','Size':20}]}
            elif 'get-object' in command:
                Path(command[-1]).write_text('{"candidate":"fixture"}')
                payload = {'ContentLength':23}
            else: raise AssertionError(command)
            return type('Result', (), {'returncode':0, 'stdout':json.dumps(payload)})()
        with patch.object(flow.subprocess, 'run', side_effect=aws):
            result = flow.drive(self.directory,
                runtime=self.scripted([self.read(),self.candidate(),self.read(),self.review()]))
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertEqual(self.load()['job_id'], None)
        self.assertTrue(all(command[:2] == ['aws','s3api'] for command in calls))

    def test_auto_export_uses_only_returned_artifact_identifiers(self):
        run = self.prepare_job()
        class Client:
            def calls(self, requests):
                if any(name == 'get_job_status' for name, _ in requests):
                    return [{}, {'job':{'jobId':'fixture-job','status':'COMPLETED'}}]
                if any(name == 'list_resources' for name, _ in requests):
                    return [{}, {'artifacts':[{'artifactId':'returned-fixture-artifact'}]}]
                for name, args in requests:
                    if name == 'get_resource':
                        assert args['artifactId'] == 'returned-fixture-artifact'
                        (Path(args['savePath']) / args['fileName']).write_text('Source rule findings fixture')
                return [{} for _ in requests]
        result = flow.drive(self.directory, transform=Client(),
            runtime=self.scripted([self.read(),self.candidate(),self.read(),self.review()]))
        self.assertEqual(result['status'], 'AWAITING_HUMAN_ACCEPTANCE')
        self.assertTrue((self.directory / 'assessment/finding-0.txt').is_file())

    def test_export_listing_traverses_returned_job_folders_and_rejects_escape(self):
        seen = []
        class Client:
            escape = False
            def calls(self, requests):
                args = requests[-1][1]; prefix = args.get('pathPrefix'); seen.append(prefix)
                if prefix is None:
                    return [{}, {'folders':['transform-output/fixture/docs/']}]
                return [{}, {'connectorId':'fixture-connector','folders':['transform-output/fixture/docs/'],
                             'assets':[{'assetKey': 'another-job/secrets.json' if self.escape else prefix+'rules.json'}]}]
        client = Client()
        result = flow.list_assessment_exports(self.directory,client,{'jobId':'fixture','workspaceId':'fixture'})
        self.assertEqual(seen,[None,'transform-output/fixture/docs/'])
        self.assertEqual(len(result['assets']),1)
        client.escape = True
        with self.assertRaisesRegex(flow.Blocked,'outside this job'):
            flow.list_assessment_exports(self.directory,client,{'jobId':'fixture','workspaceId':'fixture'})

    def test_export_deduplicates_and_batches_complete_downloads(self):
        run = self.prepare_job(); batches = []
        class Client:
            def calls(self, requests):
                if any(name == 'list_resources' for name,_ in requests):
                    return [{}, {'artifacts':[{'artifactId':str(i)} for i in range(21)] + [{'artifactId':'0'}]}]
                downloads = [args for name,args in requests if name == 'get_resource']
                batches.append(len(downloads))
                for args in downloads:
                    (Path(args['savePath'])/args['fileName']).write_text('finding')
                return [{} for _ in requests]
        flow.export_findings(run,self.directory,Client(),{'jobId':'fixture','workspaceId':'fixture'})
        self.assertEqual(batches,[10,10,1])
        self.assertEqual(len(run['assessment_index']),22)
        self.assertEqual(len(run['assessment_originals']),21)

    def test_export_preserves_binary_originals_and_extracts_zip_text(self):
        run = self.prepare_job()
        class Client:
            def calls(self, requests):
                if any(name == 'list_resources' for name,_ in requests):
                    return [{}, {'connectorId':'fixture-connector', 'assets':[
                        {'assetKey':'transform-output/fixture/report.zip'},
                        {'assetKey':'transform-output/fixture/report.pdf'}]}]
                for name,args in requests:
                    if name != 'get_resource': continue
                    dest = Path(args['savePath'])/args['fileName']
                    if args['assetKey'].endswith('.zip'):
                        with zipfile.ZipFile(dest,'w') as z:
                            z.writestr('rules/rule.json','{"rule":"reject empty"}')
                            z.writestr('report.pdf',b'%PDF-1.7\x00\xff')
                    else: dest.write_bytes(b'%PDF-1.7\x00\xff')
                return [{} for _ in requests]
        flow.export_findings(run,self.directory,Client(),{'jobId':'fixture','workspaceId':'fixture'})
        self.assertTrue(run['assessment_ready'])
        self.assertEqual(len(run['assessment_originals']),2)
        self.assertEqual(sum(x['binary_files_not_text_extracted'] for x in run['assessment_originals']),2)
        self.assertEqual(len(run['assessment_originals'][0]['text_files']),1)
        for item in run['assessment_originals']:
            self.assertEqual(flow.sha(self.directory/item['file']),item['sha256'])
        self.assertTrue((self.directory/'assessment/original-artifacts.json').exists())

    def test_export_missing_file_or_nested_results_cannot_be_sealed(self):
        run = self.prepare_job()
        class Client:
            folders = []
            def calls(self, requests):
                if any(name == 'list_resources' for name,_ in requests):
                    return [{}, {'artifacts':[{'artifactId':'fixture'}], 'folders':self.folders}]
                return [{} for _ in requests]
        client = Client()
        with self.assertRaisesRegex(flow.Blocked,'every expected file'):
            flow.export_findings(run,self.directory,client,{'jobId':'fixture','workspaceId':'fixture'})
        self.assertFalse(run['assessment_ready'])
        client.folders = ['nested/']
        with self.assertRaisesRegex(flow.Blocked,'outside this job'):
            flow.export_findings(run,self.directory,client,{'jobId':'fixture','workspaceId':'fixture'})
        self.assertFalse(run['assessment_ready'])

    def test_model_subprocess_timeout_is_terminated_and_resumable(self):
        run = self.load(); run['model_calls'] = 1
        run['settings']['call_timeout_seconds'] = 1
        run['settings']['kiro_command'] = [sys.executable, '-c', 'import time; time.sleep(10)']
        with patch.dict(flow.os.environ, {'KIRO_API_KEY':'offline-fixture-value'}):
            with self.assertRaisesRegex(flow.RuntimeUnavailable, 'terminated'):
                flow.kiro_call(run, self.directory, 'executor', 'Synthetic fixture context')
        self.assertTrue((self.directory / 'model-1-timeout.json').exists())

    def test_malformed_agent_reply_never_becomes_pass(self):
        with self.assertRaises(ValueError): flow.json_reply('I approve this without a JSON review.')
        with self.assertRaises(flow.Blocked): flow.json_reply('{"kind":"shell","command":"anything"}')

    def test_cli_does_not_report_success_for_blocked_controller(self):
        arguments = ['kiro_workflow.py', 'resume', '--run', str(self.directory)]
        with patch.object(sys, 'argv', arguments), patch.object(sys, 'stdout', io.StringIO()), \
             patch.object(flow, 'drive', return_value={'status':'BLOCKED','reason':'fixture failure'}):
            self.assertEqual(flow.main(), 2)

    def test_technical_pass_with_findings_is_rejected(self):
        review = self.review('REWORK'); review['decision'] = 'PASS'
        result = flow.drive(self.directory, runtime=self.scripted([self.read(),self.candidate(),self.read(),review]))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('no open findings', result['reason'])


if __name__ == '__main__': unittest.main()
