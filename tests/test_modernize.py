"""Offline acceptance and failure-path tests for the four-phase CLI."""
import argparse
import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import modernize as app
import modernize_artifacts as artifacts
import modernize_models as models
import kiro_workflow as kw
import pilot


class PreparationTests(unittest.TestCase):
    def test_model_budget_change_retains_consumed_calls_and_does_not_clear_refusal(self):
        self.project['pipeline']={'calls':10,'role':'executor','status':'PROVIDER_REVIEW_REQUIRED'}
        self.project['stage']='PROVIDER_REVIEW_REQUIRED'
        result=app.set_model_budget(self.work,self.project,argparse.Namespace(max_calls=64,by='Fixture owner',note='Full scope'))
        self.assertEqual(result['calls_consumed'],10)
        self.assertEqual(self.project['stage'],'PROVIDER_REVIEW_REQUIRED')
        self.assertEqual(self.project['events'][-1]['to'],64)
        with self.assertRaises(app.Blocked):
            app.set_model_budget(self.work,self.project,argparse.Namespace(max_calls=10,by='Fixture owner',note='invalid'))

    def test_reviewed_request_correction_retains_refusal_and_stops_on_new_refusal(self):
        self.baseline()
        area=self.work/'preparation';area.mkdir()
        refusal={'provider':'claude','stdout':json.dumps({'is_error':True,'result':'safeguards flagged [reasoning_extraction]'})}
        app.write(area/'provider-10.json',refusal)
        self.project['stage']='PROVIDER_REVIEW_REQUIRED'
        self.project['pipeline']={'calls':10,'role':'executor','status':'PROVIDER_REVIEW_REQUIRED'}
        approvals=copy.deepcopy(self.project['approvals'])
        args=argparse.Namespace(receipt='provider-10.json',by='Fixture owner',note='Replace free-form notes with cited findings')
        result=app.recover_provider_request(self.work,self.project,args)
        self.assertEqual(result['calls_consumed'],10)
        self.assertEqual(self.project['approvals'],approvals)
        self.assertEqual(app.read(area/'provider-10.json'),refusal)
        models.check_provider_refusals(area)
        with self.assertRaises(app.Blocked): app.recover_provider_request(self.work,self.project,args)
        app.write(area/'provider-11.json',refusal)
        with self.assertRaises(app.ProviderSafetyRefusal): models.check_provider_refusals(area)

    def test_saved_provider_safeguard_refusal_blocks_without_new_model_calls(self):
        self.baseline()
        area=self.work/'preparation';area.mkdir()
        app.write(area/'provider-1.json', {'provider':'claude','stdout':json.dumps({
            'is_error':True,'result':'Safeguards flagged this message [reasoning_extraction]'})})
        with self.assertRaises(app.ProviderSafetyRefusal):
            app.legacy_pipeline(self.work,self.project,self.args('validate'),
                         runtime=lambda *args: self.fail('Must not retry refused request'))
        self.assertEqual(self.project['pipeline']['calls'],0)

    def test_preparation_context_compacts_without_mutating_read_receipts(self):
        history = [{'request':{'area':'source','paths':[str(n)]}, 'response':[
            {'area':'source','path':str(n),'text':str(n)*2000}]} for n in range(3)]
        history[0]['request']['notes'] = 'private planning text must not be replayed'
        original = copy.deepcopy(history)
        message = app.bounded_preparation_context({'history':history,'working_notes':'Claim to verify'},3500)
        decoded = json.loads(message)
        self.assertLessEqual(len(message.encode()),3500)
        self.assertEqual(history,original)
        self.assertNotIn('private planning text',message)
        self.assertIn('earlier_reads',decoded)
        self.assertEqual(decoded['history'][-1]['response'][0]['path'],'2')

    def test_assessment_catalog_names_evidence_and_omits_logs(self):
        run = {'assessment_index':[{'path':'a.txt','bytes':10},{'path':'b.txt','bytes':20}],
               'assessment_originals':[{'source':{'assetKey':'job/logs/run.log'},'text_files':[{'file':'a.txt','member':'run.log'}]},
                {'source':{'assetKey':'job/results/rules.zip'},'text_files':[{'file':'b.txt','member':'program/rules.json'}]}]}
        self.assertEqual(app.assessment_catalog(run),[{'path':'b.txt','bytes':20,'label':'program/rules.json'}])

    def test_controller_upgrade_preserves_verified_assessment_approval(self):
        self.baseline()
        before = copy.deepcopy(self.project['approvals'])
        self.project['pipeline'] = {'calls':2,'contexts':{'executor':[]}}
        app.upgrade_framework(self.work,self.project,argparse.Namespace(note='Controller fix'))
        self.assertEqual(self.project['approvals'],before)
        self.assertEqual(self.project['pipeline']['calls'],2)

    def test_framework_upgrade_retains_assessment_and_audits_change(self):
        self.project['framework_commit'] = 'b' * 40
        directory = self.work / 'existing-run'
        directory.mkdir()
        run = {'framework_commit':'b'*40,'source':self.project['source'],
               'job_id':'existing-job','create_pending':False,'events':[]}
        app.write(directory/'run.json',run)
        self.project['assessments']['run-a'] = {'directory':str(directory)}
        result = app.upgrade_framework(self.work,self.project,argparse.Namespace(note='Fix execution checkpoint'))
        updated = app.read(directory/'run.json')
        self.assertEqual(updated['job_id'],'existing-job')
        self.assertEqual(updated['framework_commit'],'a'*40)
        self.assertEqual(updated['events'][-1]['from'],'b'*40)
        self.assertEqual(result['status'],'FRAMEWORK_UPDATED')

    def test_framework_upgrade_rejects_approved_or_dirty_project(self):
        self.project['approvals']['assessment'] = {'sha256':'approved'}
        with self.assertRaises(app.Blocked):
            app.upgrade_framework(self.work,self.project,argparse.Namespace(note='fix'))
        self.project['approvals'].clear();self.dirty=True
        with self.assertRaises(app.Blocked):
            app.upgrade_framework(self.work,self.project,argparse.Namespace(note='fix'))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'private'
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'rule.cbl').write_text('IF AMOUNT = ZERO\n MOVE 1 TO ERROR-FLAG\nEND-IF\n')
        self.findings = self.root / 'findings'
        self.findings.mkdir()
        (self.findings / 'rules.json').write_text('{"rules":["reject zero amount"]}')
        self.dirty = False
        def git(root, *args):
            if args == ('rev-parse', 'HEAD'): return 'a' * 40
            if args == ('remote', 'get-url', 'origin'): return 'https://example.invalid/source.git'
            if args == ('status', '--porcelain'): return 'dirty' if self.dirty else ''
            if args[0] == 'ls-files': return 'rule.cbl' if len(args) == 1 or args[-1] == 'rule.cbl' else ''
            raise AssertionError(args)
        for module in (app, kw, pilot):
            mock = patch.object(module, 'git', side_effect=git)
            mock.start(); self.addCleanup(mock.stop)
        args = app.parser().parse_args(['--workspace', str(self.work), 'init', '--source', str(self.source), '--scope', 'Zero-amount journey'])
        app.initialize(args, self.work)
        self.project = app.read(self.work / 'project.json')
        self.messages = []

    def args(self, *values):
        return app.parser().parse_args(['--workspace', str(self.work), *values])

    def assess(self, label='a'):
        return app.assessment(self.work, self.project, self.args('assess', '--label', label, '--findings', str(self.findings)))

    def approve(self, phase, *extra):
        return app.approve(self.work, self.project, self.args('approve', phase, '--by', 'Fixture owner', '--note', 'Fixture review', *extra))

    def baseline(self):
        self.assess()
        self.approve('assessment', '--label', 'a', '--confirm-source-match')

    def spec(self):
        return {'scope': 'Zero-amount journey', 'excluded_scope': 'Other journeys and runtime equivalence',
            'rules': [{'id': 'R1', 'statement': 'Reject zero', 'source_path': 'rule.cbl', 'source_anchor': 'lines 1-3', 'evidence_status': 'SOURCE_VERIFIED'}],
            'sections': {k: {'specification': 'Fixture ' + k, 'implementation_prompt': 'Implement approved ' + k, 'rule_ids': ['R1']} for k in artifacts.SECTIONS},
            'questions': [], 'tests': [{'id': 'T-' + category, 'rule_id': 'R1', 'category': category, 'given': 'fixture input', 'when': 'submit', 'then': 'fixture outcome', 'oracle': 'rule.cbl', 'status': 'NOT RUN'} for category in ('normal', 'rejection', 'boundary')],
            'work_items': [{'id': 'W1', 'title': 'Fixture task', 'depends_on': [], 'acceptance': 'Test against approved oracle'}],
            'monorepo_layout': ['apps/ui: proposed interface', 'services/core: proposed domain']}

    def screens(self):
        return {'screens': [{'id': 's1', 'title': 'Amount', 'purpose': 'Validate amount', 'rule_ids': ['R1'],
                            'fields': ['Amount'], 'actions': ['Submit'], 'states': ['Normal', 'Zero rejected'],
                            'uncertainties': 'Runtime equivalence is unproven'}],
                'apis':[{'id':'validate-amount','method':'POST','path':'/amount/validate',
                         'purpose':'Validate amount','authentication':'Proposed authenticated caller',
                         'uncertainties':'No runtime parity proof','request':{'amount':'number'},
                         'responses':{'200':'valid','422':'zero rejected'},'rule_ids':['R1']}]}

    def replies(self, review='PASS'):
        return [{'kind': 'read', 'area': 'source', 'paths': ['rule.cbl']},
                {'kind': 'read', 'area': 'assessment', 'paths': ['rules.json']},
                {'kind': 'candidate', 'specification': self.spec()},
                {'kind': 'read', 'area': 'source', 'paths': ['rule.cbl']},
                {'kind': 'read', 'area': 'assessment', 'paths': ['rules.json']},
                {'kind': 'review', 'decision': review, 'report': 'Fixture independent review',
                 'findings': [] if review == 'PASS' else ['Revise boundary'] }]

    def runtime(self, replies):
        sequence = iter(replies)
        def call(provider, run, directory, role, message):
            self.messages.append((provider, role, json.loads(message)))
            return next(sequence)
        return call

    def validated(self):
        self.baseline()
        return app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(self.replies()))

    def site(self):
        self.validated()
        self.approve('validation')
        return app.review_site(self.work, self.project, self.args('review-site'), self.runtime([self.screens()]))

    def test_four_phase_acceptance_no_cloud_or_application_build(self):
        result = self.site()
        self.assertEqual(result['status'], 'AWAITING_SPECIFICATIONS_APPROVAL')
        with self.assertRaisesRegex(pilot.Blocked, 'approval'):
            app.package(self.work, self.project)
        self.approve('specifications')
        result = app.package(self.work, self.project)
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertIn('ui-prompt.md', archive.namelist())
            self.assertIn('AGENTS.md', archive.namelist())
            self.assertIn('SKILL.md', archive.namelist())
            self.assertEqual(json.loads(archive.read('ai-dlc.json'))['implementation_status'], 'NOT_STARTED')
            self.assertIn('infrastructure-prompt.md', archive.namelist())
            self.assertNotIn('project.json', archive.namelist())
            self.assertNotIn('source.zip', archive.namelist())
            self.assertTrue(all(not x.endswith(('.cs', '.tf')) for x in archive.namelist()))
            self.assertEqual(json.loads(archive.read('traceability.json'))['tests'][0]['status'], 'NOT RUN')
        self.assertEqual(self.project['stage'], 'PACKAGED')

    def test_package_refuses_secret_and_writes_no_zip(self):
        self.baseline()
        replies = self.replies()
        replies[2]['specification']['sections']['ui']['implementation_prompt'] += '\nAKIAIOSFODNN7EXAMPLE\n'
        app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(replies))
        self.approve('validation')
        app.review_site(self.work, self.project, self.args('review-site'), self.runtime([self.screens()]))
        self.approve('specifications')
        with self.assertRaises(pilot.Blocked) as caught:
            app.package(self.work, self.project)
        message = str(caught.exception)
        self.assertIn('ui-prompt.md', message)
        self.assertIn('cloud access key', message)
        self.assertNotIn('AKIA', message)
        self.assertNotEqual(self.project['stage'], 'PACKAGED')
        self.assertFalse(list((self.work / 'packages').glob('*.zip')))

    def test_skipped_verification_needs_a_second_acceptance_before_package(self):
        self.site()
        self.approve('specifications')
        self.project['verification_skip'] = {
            'owner': 'Fixture owner', 'reason': 'Preview AWS findings',
            'assessment_sha256': self.project['assessments']['a']['sha256'], 'at': 0}
        app.persist(self.work, self.project)
        with self.assertRaisesRegex(pilot.Blocked, 'second acceptance'):
            app.package(self.work, self.project)
        self.assertNotEqual(self.project['stage'], 'PACKAGED')
        with self.assertRaisesRegex(pilot.Blocked, 'different person'):
            app.approve(self.work, self.project, self.args(
                'approve', 'verification-skip', '--by', 'Fixture owner', '--note', 'Same person'))
        app.approve(self.work, self.project, self.args(
            'approve', 'verification-skip', '--by', 'Second owner', '--note', 'Verification was skipped'))
        result = app.package(self.work, self.project)
        self.assertEqual(result['status'], 'PACKAGED')
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(json.loads(archive.read('provenance.json'))['verification'], 'SKIPPED_BY_OWNER')
            self.assertEqual(json.loads(archive.read('ai-dlc.json'))['verification'], 'SKIPPED_BY_OWNER')

    def test_invalid_candidate_gets_correction_without_bypassing_review(self):
        self.baseline()
        replies = self.replies()
        invalid = copy.deepcopy(replies[2])
        del invalid['specification']['monorepo_layout']
        replies.insert(2, invalid)
        result = app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(replies))
        self.assertEqual(result['calls'], 7)
        self.assertEqual(result['status'], 'AWAITING_VALIDATION_APPROVAL')
        correction = self.messages[3][2]['candidate_correction']
        self.assertIn('array of strings', correction['error'])
        self.assertNotIn('monorepo_layout', correction['rejected_specification'])
        self.assertTrue((self.work/'preparation/reply-3.json').exists())
        self.assertEqual(self.project['pipeline']['role'], 'reviewer')

    def test_preparation_produces_html_before_human_validation(self):
        self.validated()
        report = self.project['preparation_report']
        content = (self.work / report['path']).read_text()
        self.assertIn('SME and architect review', content)
        self.assertIn('Business rules', content)
        self.assertEqual(report['specification'], self.project['specification']['sha256'])

    def test_preparation_report_tamper_blocks_human_approval(self):
        self.validated()
        (self.work / self.project['preparation_report']['path']).write_text('changed')
        with self.assertRaisesRegex(pilot.Blocked, 'Preparation report changed'):
            self.approve('validation')

    def test_preparation_report_tamper_after_approval_blocks_business_site(self):
        self.validated()
        self.approve('validation')
        (self.work / self.project['preparation_report']['path']).write_text('changed')
        with self.assertRaisesRegex(pilot.Blocked, 'Preparation report changed'):
            app.review_site(self.work, self.project, self.args('review-site'), self.runtime([self.screens()]))

    def test_phase_one_stops_and_never_calls_agents(self):
        self.assertEqual(self.assess()['status'], 'AWAITING_ASSESSMENT_APPROVAL')
        with self.assertRaisesRegex(pilot.Blocked, 'Approve Phase 1'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'), lambda *a: self.fail('No model before approval'))

    def test_existing_findings_require_source_match_attestation(self):
        self.assess()
        with self.assertRaisesRegex(pilot.Blocked, 'confirm-source-match'):
            self.approve('assessment', '--label', 'a')

    def test_import_never_starts_aws_job(self):
        with patch.object(kw.Transform, 'calls', side_effect=AssertionError('No cloud')):
            self.assess()

    def test_new_assessment_requires_authorization_and_setup(self):
        with self.assertRaisesRegex(pilot.Blocked, 'authorize'):
            app.assessment(self.work, self.project, self.args('assess', '--label', 'new'))
        with self.assertRaisesRegex(pilot.Blocked, 'setup'):
            app.assessment(self.work, self.project, self.args('assess', '--label', 'new', '--allow-new-assessment'))

    def test_resume_unknown_label_never_creates(self):
        with self.assertRaisesRegex(pilot.Blocked, 'never creates'):
            app.assessment(self.work, self.project, self.args('resume', '--label', 'missing'))

    def test_sealed_assessment_replaces_stale_wait_reason(self):
        result = self.assess()
        directory = Path(self.project['assessments']['a']['directory'])
        run = app.read(directory/'run.json')
        self.assertEqual(run['status'],'AWAITING_ASSESSMENT_APPROVAL')
        self.assertIn('downloaded and sealed',run['reason'])

    def test_assessment_review_index_escapes_content_and_does_not_approve(self):
        self.assess()
        directory = Path(self.project['assessments']['a']['directory'])
        run = app.read(directory/'run.json'); run['objective'] = '<script>alert(1)</script>'
        app.write(directory/'run.json',run)
        result = app.assessment_report(self.work,self.project,argparse.Namespace(label='a'))
        page = Path(result['report']).read_text()
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>',page)
        self.assertIn('assessment/rules.json',page)
        self.assertIn('Awaiting human review',page)
        self.assertFalse(self.project['approvals'])

    def test_aws_facts_count_export_rows_and_preserve_descriptions_without_model(self):
        directory = self.root/'summary-fixture';(directory/'assessment').mkdir(parents=True)
        inputs = {'assets.csv':'Path,File type,Total lines\na.cbl,COB,10\nb.cpy,CPY,3\na.cbl,COB,10\n',
                  'issues.csv':'Severity\nERROR\nWARNING\nERROR\n',
                  'missing.csv':'Type\nMissing Program\nMissing Program\n'}
        for name, content in inputs.items(): (directory/'assessment'/name).write_text(content)
        (directory/'summary.json').write_text(json.dumps({'a.cbl':'AWS text <script>unchanged</script>', 'application':'AWS overview unchanged', 'metadata':{'file_count':1}}))
        run = {'assessment_index':[{'path':name} for name in inputs], 'assessment_originals':[
            {'file':'archive.zip','source':{'assetKey':'transform-output/fixture/results/analyze_code_results.zip'},
             'text_files':[{'member':member,'file':file} for member,file in
                 [('assets_export.csv','assets.csv'),('code_issues_export.csv','issues.csv'),('missing_export.csv','missing.csv')]]},
            {'file':'summary.json','source':{'assetKey':'transform-output/fixture/documentation/metadata/summary.json'}}]}
        with patch.object(app,'model_call',side_effect=AssertionError('No model invocation')):
            page = artifacts.render_assessment_facts(run,directory,lambda path,title:title)
        for text in ['3 file records','2 distinct nonempty source paths','3 issue occurrences',
                     '2 missing-dependency records','23','AWS text &lt;script&gt;unchanged&lt;/script&gt;',
                     'Verbatim AWS-generated descriptions','AWS overview unchanged','AWS-provided application overview']:
            self.assertIn(text,page)
        self.assertNotIn('<script>',page)
        (directory/'assessment/assets.csv').write_text('Unexpected,Schema\na,b\n')
        with self.assertRaisesRegex(app.Blocked,'schema changed'):
            artifacts.render_assessment_facts(run,directory,lambda path,title:title)

    def test_human_review_omits_logs_metadata_and_duplicate_document_formats(self):
        self.assess()
        directory = Path(self.project['assessments']['a']['directory'])
        run = app.read(directory/'run.json')
        run['assessment_originals'] = []
        cases = [('logs/process.log','private-log.txt',[]),
                 ('documentation/CoverPage.pdf','cover.pdf',[]),
                 ('documentation/metadata/summary.json','metadata.json',[]),
                 ('documentation/app/PROGRAM-cbl.json','duplicate.json',[]),
                 ('documentation/app/PROGRAM-cbl.pdf','program.pdf',[]),
                 ('results/analyze_code_results.zip','analysis.zip',[{'member':'missing_fixture.txt','file':'rules.json'}]),
                 ('1/business-documentation/output.zip','rules.zip',[])]
        for key, filename, texts in cases:
            path = directory/filename;path.write_text('{}' if filename == 'metadata.json' else 'fixture')
            run['assessment_originals'].append({'file':filename,'sha256':app.sha(path),
                'source':{'assetKey':'transform-output/fixture/'+key},'text_files':texts})
        app.write(directory/'run.json',run)
        report = app.assessment_report(self.work,self.project,argparse.Namespace(label='a'))
        page = Path(report['report']).read_text()
        for filename in ['private-log.txt','cover.pdf','metadata.json','duplicate.json']:
            self.assertNotIn('href="'+filename+'"',page)
        for text in ['program.pdf','Missing dependencies (CSV)','Download business-rule reports','Program documentation (1)']:
            self.assertIn(text,page)
        self.assertNotIn(app.sha(directory/'program.pdf'),page)
        self.assertEqual(app.read(directory/'run.json'),run)
        self.assertTrue((directory/'private-log.txt').exists())

    def test_original_export_tamper_blocks_assessment_approval(self):
        self.assess()
        directory = Path(self.project['assessments']['a']['directory'])
        original = directory / 'original.zip'
        original.write_bytes(b'original archive bytes')
        run = app.read(directory/'run.json')
        run['assessment_originals'] = [{'file':'original.zip','sha256':app.sha(original)}]
        app.write(directory/'run.json',run)
        app.verified_assessment(self.project,'a')
        original.write_bytes(b'changed archive bytes')
        with self.assertRaisesRegex(app.Blocked,'Original assessment artifact changed'):
            self.approve('assessment','--label','a','--confirm-source-match')
        self.assertFalse(self.project['approvals'])

    def test_immutable_assessment_tamper_blocks(self):
        self.baseline()
        directory = Path(self.project['assessments']['a']['directory'])
        (directory / 'assessment/rules.json').write_text('{}')
        with self.assertRaisesRegex(pilot.Blocked, 'changed'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'))

    def test_claude_reviewer_gets_fresh_context(self):
        self.validated()
        first = next(m for m in self.messages if m[1] == 'reviewer')
        self.assertEqual(first[0], 'claude')
        self.assertEqual(first[2]['history'], [])
        self.assertIn('candidate', first[2])

    def test_reviewer_cannot_skip_source_reads(self):
        self.baseline()
        replies = self.replies()
        with self.assertRaisesRegex(pilot.Blocked, 'independently'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(replies[:3] + [replies[-1]]))

    def test_executor_cannot_skip_assessment_reads(self):
        self.baseline()
        replies = self.replies()
        with self.assertRaisesRegex(pilot.Blocked, 'assessment findings'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime([replies[0], replies[2]]))

    def test_automatic_rework_preserves_both_candidates(self):
        self.baseline()
        app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(self.replies('REWORK') + self.replies()))
        self.assertTrue((self.work / 'preparation/candidate-1.json').exists())
        self.assertTrue((self.work / 'preparation/candidate-2.json').exists())
        self.assertEqual(self.project['stage'], 'AWAITING_VALIDATION_APPROVAL')

    def test_call_budget_not_reset_by_resume(self):
        self.baseline()
        self.project['settings']['max_model_calls'] = 1
        result = app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(self.replies()))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.project['pipeline']['calls'], 1)

    def test_missing_sme_decision_blocks_validation_approval(self):
        self.baseline()
        replies = self.replies()
        replies[2]['specification']['questions'] = [{'id':'Q1', 'question':'Oracle?', 'impact':'Unproven parity', 'owner':'SME', 'blocks':'Release'}]
        app.legacy_pipeline(self.work, self.project, self.args('validate'), self.runtime(replies))
        with self.assertRaisesRegex(pilot.Blocked, 'Q1'):
            self.approve('validation')
        app.decision(self.work, self.project, self.args('decide', '--question', 'Q1', '--by', 'SME', '--answer', 'Obtain legacy oracle before release', '--disposition', 'deferred'))
        self.approve('validation')

    def test_changed_spec_blocks_packaging(self):
        self.site(); self.approve('specifications')
        (self.work / self.project['specification']['path']).write_text('{}')
        with self.assertRaisesRegex(pilot.Blocked, 'Specification changed'):
            app.package(self.work, self.project)

    def test_changed_html_blocks_approval(self):
        self.site()
        (self.work / self.project['site']['directory'] / 'index.html').write_text('changed')
        with self.assertRaisesRegex(pilot.Blocked, 'site changed'):
            self.approve('specifications')

    def test_feedback_invalidates_downstream_approvals_and_keeps_artifacts(self):
        self.site(); self.approve('specifications')
        old_site = self.work / self.project['site']['directory'] / 'index.html'
        app.feedback(self.work, self.project, self.args('feedback', '--by', 'SME', '--note', 'Change proposed layout'))
        self.assertNotIn('site', self.project)
        self.assertNotIn('specifications', self.project['approvals'])
        self.assertTrue(old_site.exists())
        with self.assertRaises(KeyError):
            app.package(self.work, self.project)

    def test_html_escapes_injected_markup(self):
        screens = self.screens()
        screens['screens'][0]['title'] = '<script>alert(1)</script>'
        output = artifacts.render_site(self.spec(), screens, {})
        self.assertNotIn('<script>', output)
        self.assertIn('&lt;script&gt;', output)
        self.assertIn("default-src 'none'", output)

    def test_screen_unknown_rule_is_rejected(self):
        screens = self.screens(); screens['screens'][0]['rule_ids'] = ['invented']
        with self.assertRaisesRegex(pilot.Blocked, 'unknown rule'):
            artifacts.validate_screens(screens, self.spec())

    def test_full_upload_rule_selection_is_bounded_and_receipted(self):
        self.assess()
        directory=Path(self.project['assessments']['a']['directory'])
        manifest=directory/'source-upload-manifest.json'
        app.write(manifest,{'included':[{'path':'rule.cbl'}]})
        run=app.read(directory/'run.json')
        run.update(job_id='fixture-job',assessment_scope_policy={'mode':'ALL_UPLOADED','manifest_sha256':pilot.sha(manifest)})
        run['settings']['transform']['workspace_id']='fixture-workspace'
        app.write(directory/'run.json',run)
        task={'taskId':'fixture-task','uxComponentId':'MainframeBreInputComponent',
              'jobMetadata':{'workspaceId':'fixture-workspace','jobId':'fixture-job'},
              'status':'AWAITING_HUMAN_INPUT','_outputSchema':{
                  'required':['reportScope','detailedLevel','userSelectedFiles'],
                  'properties':{'reportScope':{'enum':['applicationLevel','fileLevel']}}}}
        offered={'name':'rule.cbl','path':'rule.cbl','folderPath':'','fileType':'COB','charset':'UTF-8'}
        calls=[]
        class Client:
            def calls(self,requests):
                calls.extend(requests)
                if requests[-1][0]=='get_job_status':return [{},{'tasks':{'hitlTasks':[task]}}]
                if requests[-1][0]=='get_resource':return [{},{'task':task,'agentArtifactContent':{'properties':{'breInput':[offered]}}}]
                return [{},{'status':'SUBMITTED'}]
        for _ in range(2):app.assessment_tasks(self.work,self.project,self.args('tasks','--label','a','--configure-documentation'),Client())
        submissions=[value for key,value in calls if key=='complete_task']
        self.assertEqual(len(submissions),1)
        content=json.loads(submissions[0]['content'])
        self.assertEqual(content['reportScope'],'fileLevel')
        self.assertTrue(content['detailedLevel'])
        self.assertNotIn('charset',content['userSelectedFiles'][0])
        offered['path']='outside.cbl'
        with self.assertRaisesRegex(pilot.Blocked,'outside the upload'):
            app.assessment_tasks(self.work,self.project,self.args('tasks','--label','a','--configure-documentation'),Client())

    def test_api_unknown_rule_and_duplicate_operation_are_rejected(self):
        screens=self.screens();screens['apis'][0]['rule_ids']=['invented']
        with self.assertRaisesRegex(pilot.Blocked,'Unknown API rule'):artifacts.validate_screens(screens,self.spec())
        screens=self.screens();screens['apis'].append(dict(screens['apis'][0]))
        with self.assertRaisesRegex(pilot.Blocked,'Duplicate API'):artifacts.validate_screens(screens,self.spec())

    def test_test_execution_claim_is_rejected(self):
        spec = self.spec(); spec['tests'][0]['status'] = 'PASS'
        with self.assertRaisesRegex(pilot.Blocked, 'NOT RUN'):
            artifacts.validate_spec(spec, self.source)

    def test_backlog_cycle_is_rejected(self):
        spec = self.spec(); spec['work_items'][0]['depends_on'] = ['W1']
        with self.assertRaisesRegex(pilot.Blocked, 'dependency order'):
            artifacts.validate_spec(spec, self.source)

    def test_compare_retains_raw_results_and_detects_semantic_text_change(self):
        self.assess('a')
        (self.findings / 'rules.json').write_text('{"rules":["accept zero amount"]}')
        self.assess('b')
        result = app.compare(self.work, self.project, self.args('compare', '--left', 'a', '--right', 'b'))
        report = app.read(Path(result['report']))
        self.assertEqual(result['changed_files'], 1)
        self.assertEqual(result['semantic_review'], 'PENDING')
        self.assertIn('reject zero', report['differences'][0]['diff'])
        app.verified_assessment(self.project, 'a')

    def test_compare_normalizes_json_key_order_only(self):
        (self.findings / 'rules.json').write_text('{"a":1,"b":2}')
        self.assess('a')
        (self.findings / 'rules.json').write_text('{"b":2,"a":1}')
        self.assess('b')
        self.assertEqual(app.compare(self.work, self.project, self.args('compare', '--left', 'a', '--right', 'b'))['changed_files'], 0)

    def test_uncertain_resource_creation_is_not_retried(self):
        calls = []
        def lost():
            calls.append(1)
            raise OSError('lost response')
        with self.assertRaises(OSError): app.mutation(self.work, self.project, 'workspace', lost)
        with self.assertRaisesRegex(pilot.Blocked, 'Uncertain'): app.mutation(self.work, self.project, 'workspace', lost)
        self.assertEqual(len(calls), 1)

    def test_receipt_recovers_without_recreating_resource(self):
        app.write(self.work / 'setup-workspace.json', {'workspaceId': 'fixture'})
        result = app.mutation(self.work, self.project, 'workspace', lambda: self.fail('Duplicate creation'))
        self.assertEqual(result['workspaceId'], 'fixture')

    def test_source_change_blocks_any_validation(self):
        self.baseline(); self.dirty = True
        with self.assertRaisesRegex(pilot.Blocked, 'not clean'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'))

    def test_lock_prevents_concurrent_mutation(self):
        with app.locked(self.work):
            with self.assertRaisesRegex(pilot.Blocked, 'Another command'):
                with app.locked(self.work): pass

    def test_claude_command_disables_tools_and_records_receipt(self):
        class Process:
            returncode = 0
            def communicate(self, *a, **kw): return json.dumps({'result': '{"kind":"blocked","reason":"fixture"}'}), ''
        with patch.object(models.shutil, 'which', return_value='/fixture/claude'), patch.object(models.subprocess, 'Popen', return_value=Process()) as process:
            models.call('claude', {'settings': kw.DEFAULTS, 'model_calls':1}, self.work, 'reviewer', '{}')
        command = process.call_args[0][0]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertNotIn('--bare', command)
        self.assertIn('--strict-mcp-config', command)
        self.assertIn('mcp__*', command)

    def test_completed_aws_job_stops_before_agents(self):
        self.assess()
        directory = Path(self.project['assessments']['a']['directory'])
        run = app.read(directory / 'run.json')
        run.update(assessment_ready=False, assessment_input=None, job_id='fixture-job')
        run['settings']['transform'] = {'workspace_id':'fixture-workspace','output_location':str(self.findings)}
        app.write(directory / 'run.json', run)
        class Client:
            def calls(self, requests):
                return [{}, {'job':{'jobId':'fixture-job','status':'COMPLETED'}}]
        result = app.assessment(self.work, self.project, self.args('resume','--label','a'), Client())
        self.assertEqual(result['status'], 'AWAITING_ASSESSMENT_APPROVAL')
        self.assertNotIn('pipeline', self.project)

    def test_source_archive_is_identical_despite_mtime_changes(self):
        self.project['setup']['connector_approved_by'] = 'Fixture admin'
        self.project['setup']['connector_id'] = 'fixture-connector'
        self.project['settings']['transform'] = {'workspace_id':'fixture-workspace','upload_prefix':'s3://fixture-private/source','orchestrator_agent':'fixture-mainframe'}
        class Client:
            def calls(self, requests):
                return [{'job':{'jobId':'fixture-job','status':'RUNNING'}} if key in {'create_job','get_job_status'} else {}
                        for key, value in requests]
        result = type('Result', (), {'returncode':0})()
        with patch.object(kw.subprocess, 'run', return_value=result):
            app.assessment(self.work, self.project, self.args('assess','--label','a','--allow-new-assessment'), Client())
            import os
            os.utime(self.source / 'rule.cbl', (1000, 1000))
            app.assessment(self.work, self.project, self.args('assess','--label','b','--allow-new-assessment'), Client())
        a = Path(self.project['assessments']['a']['directory']) / 'source.zip'
        b = Path(self.project['assessments']['b']['directory']) / 'source.zip'
        self.assertEqual(pilot.sha(a), pilot.sha(b))
        self.assertTrue((a.parent / 'source-upload-manifest.json').exists())

    def test_settings_do_not_share_mutable_transform_defaults(self):
        one, two = kw.settings(None), kw.settings(None)
        one['transform']['workspace_id'] = 'fixture'
        self.assertNotIn('workspace_id', two['transform'])

    def test_semantic_comparison_requires_both_findings_and_primary_source(self):
        self.assess('a'); self.assess('b')
        reply = {'kind':'candidate', 'comparison':{'summary':'Fixture', 'coverage_limitations':'One rule', 'differences':[]}}
        args = self.args('compare', '--left', 'a', '--right', 'b', '--semantic')
        with self.assertRaisesRegex(pilot.Blocked, 'both findings and source'):
            app.semantic_compare(self.work, self.project, args, self.runtime([reply]))
        actions = [{'kind':'read','area':area,'paths':['rule.cbl' if area == 'source' else 'rules.json']}
                   for area in ('left', 'right', 'source')] + [reply]
        result = app.semantic_compare(self.work, self.project, args, self.runtime(actions))
        self.assertEqual(result['semantic_review'], 'AWAITING_HUMAN_REVIEW')
        self.assertIn('coverage_limitations', app.read(Path(result['report'])))

    def test_review_tamper_blocks_validation_approval(self):
        self.validated()
        (self.work / self.project['specification']['review_path']).write_text('{}')
        with self.assertRaisesRegex(pilot.Blocked, 'Independent review changed'):
            self.approve('validation')

    def test_setup_creates_once_and_stops_for_connector_approval(self):
        self.project['aws'].update(bucket='fixture-private', region='us-west-2')
        self.project['settings']['transform']['upload_prefix'] = 's3://fixture-private/source'
        calls = []
        class Client:
            def calls(self, requests):
                calls.extend(requests)
                values = {'get_status':{}, 'create_workspace':{'workspaceId':'fixture-workspace'},
                          'create_connector':{'connectorId':'fixture-connector','verificationLink':'https://example.invalid/verify'},
                          'get_resource':{'accountConnection':{'awsAccountConnection':{'status':'ACTIVE','accountId':'fixture-account'}}}}
                return [values[key] for key, value in requests]
        def aws(project, *args):
            if args[:2] == ('sts','get-caller-identity'): return {'Account':'fixture-account'}
            if args[:2] == ('s3api','get-public-access-block'):
                return {'PublicAccessBlockConfiguration': {k:True for k in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')}}
            return {}
        args = self.args('setup','--create-bucket','--create-workspace','fixture','--create-connector','fixture')
        with patch.object(app, 'aws', side_effect=aws):
            first = app.setup(self.work, self.project, args, Client())
            second = app.setup(self.work, self.project, args, Client())
            approved = app.setup(self.work, self.project, self.args('setup','--connector-approved','Fixture admin'), Client())
        self.assertEqual(first['status'], 'WAITING_FOR_CONNECTOR_APPROVAL')
        self.assertEqual(second['status'], 'WAITING_FOR_CONNECTOR_APPROVAL')
        self.assertEqual(approved['status'], 'READY_TO_ASSESS')
        self.assertEqual(self.project['settings']['transform']['connector_id'], 'fixture-connector')
        self.assertEqual([key for key, value in calls].count('create_workspace'), 1)
        self.assertEqual([key for key, value in calls].count('create_connector'), 1)
        connector=next(value for key,value in calls if key=='create_connector')
        self.assertEqual(connector['connectorType'],'mainframe_modernization|s3|2')
        self.assertEqual(connector['configuration'],{'s3BucketArn':'arn:aws:s3:::fixture-private'})

    def test_operator_confirmation_cannot_override_aws_connector_readiness(self):
        self.project['aws'].update(bucket='fixture-private', region='us-east-1')
        self.project['settings']['transform']['workspace_id'] = 'fixture-workspace'
        self.project['setup']['connector_id'] = 'fixture-connector'
        for connection in [{'status':'PENDING','accountId':'fixture-account'},
                           {'status':'ACTIVE','accountId':'other-account'}]:
            with self.subTest(connection=connection):
                class Client:
                    def calls(self, requests):
                        return [{'accountConnection':{'awsAccountConnection':connection}} if key == 'get_resource' else {}
                                for key,_ in requests]
                public = {'PublicAccessBlockConfiguration': {k:True for k in
                          ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')}}
                with patch.object(app,'aws',side_effect=[{'Account':'fixture-account'}, {}, public]):
                    with self.assertRaises(pilot.Blocked):
                        app.setup(self.work,self.project,self.args('setup','--connector-approved','Fixture admin'),Client())
                self.assertNotIn('connector_approved_by',self.project['setup'])

    def test_setup_rejects_existing_public_bucket(self):
        self.project['aws'].update(bucket='fixture-private', region='us-east-1')
        class Client:
            def calls(self, requests): return [{}]
        with patch.object(app, 'aws', side_effect=[{'Account':'fixture'}, {}, {'PublicAccessBlockConfiguration':{}}]):
            with self.assertRaisesRegex(pilot.Blocked, 'block public access'):
                app.setup(self.work, self.project, self.args('setup'), Client())

    def test_cli_failure_persists_consumed_budget(self):
        self.baseline()
        def failing(*args): raise kw.RuntimeUnavailable('Fixture authentication failure')
        # Patch pipeline's supplied runtime rather than its bound default.
        original = app.legacy_pipeline
        def pipeline(work, project, args): return original(work, project, args, failing)
        with patch.object(app, 'pipeline', side_effect=pipeline), patch('builtins.print'):
            result = app.main(['--workspace',str(self.work),'validate'])
        saved = app.read(self.work / 'project.json')
        self.assertEqual(result, 2)
        self.assertEqual(saved['pipeline']['calls'], 1)
        self.assertEqual(saved['stage'], 'WAITING_FOR_RUNTIME')

    def test_same_claude_models_rejected(self):
        with self.assertRaisesRegex(pilot.Blocked, 'different Claude models'):
            app.validate_model_pair('claude', {'executor_model':'claude-sonnet-5-5', 'reviewer_model':'claude-sonnet-5-5'})
        with self.assertRaisesRegex(pilot.Blocked, 'full model IDs'):
            app.validate_model_pair('claude', {'executor_model':'sonnet', 'reviewer_model':'opus'})

    def test_claude_fallback_model_is_rejected(self):
        class Process:
            returncode = 0
            def communicate(self, *a, **kw):
                return json.dumps({'modelUsage':{'claude-sonnet-5-5':{}}, 'result':'{}'}), ''
        settings = dict(kw.DEFAULTS, reviewer_model='claude-opus-5-5')
        with patch.object(models.shutil, 'which', return_value='/fixture/claude'), patch.object(models.subprocess, 'Popen', return_value=Process()):
            with self.assertRaisesRegex(pilot.Blocked, 'model identity'):
                models.call('claude', {'settings':settings, 'model_calls':1}, self.work, 'reviewer', '{}')

    def test_codex_unexpected_tool_event_rejected(self):
        class Process:
            returncode = 0
            def communicate(self, *a, **kw):
                return json.dumps({'type':'item.completed','item':{'type':'command_execution'}}), ''
        with patch.object(models.shutil, 'which', return_value='/fixture/codex'), patch.object(models.subprocess, 'Popen', return_value=Process()):
            with self.assertRaisesRegex(pilot.Blocked, 'Unexpected tool'):
                models.call('codex', {'settings':kw.DEFAULTS, 'model_calls':1}, self.work, 'wireframes', '{}')


if __name__ == '__main__': unittest.main()
