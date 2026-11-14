import copy
import json
import unittest
from pathlib import Path
import zipfile
from unittest.mock import patch

import test_reframe as fixtures
import reframe as app
import reframe_preview as preview
from pilot import Blocked, sha


class PreviewTests(unittest.TestCase):
    args=fixtures.PreparationTests.args
    assess=fixtures.PreparationTests.assess
    approve=fixtures.PreparationTests.approve
    spec=fixtures.PreparationTests.spec
    screens=fixtures.PreparationTests.screens

    def setUp(self):
        fixtures.PreparationTests.setUp(self)
        mock=patch.object(preview,'git',side_effect=app.git);mock.start();self.addCleanup(mock.stop)
        (self.findings/'rules.json').write_text(json.dumps({'program_name':'FIXTURE','all_rules':[
            {'Rule_Id':'R1','Rule_Name':'Reject zero','Rule_Description':'Reject zero amount','Acceptance_Criteria':['Zero rejected']}]}))
        self.assess();self.approve('assessment','--label','a','--confirm-source-match')
        self.project['pipeline']={'calls':64,'status':'BLOCKED'}
        self.project['stage']='BLOCKED';self.project['settings']['max_model_calls']=64
        app.persist(self.work,self.project)
        self.arguments=self.args('preview','--skip-validation','--by','Fixture owner','--note','Preview AWS findings')

    def runtime(self,provider,run,directory,role,message):
        context=json.loads(message)
        self.assertEqual(role,'wireframes')
        self.assertNotIn('candidate',context)
        rule_id=context['programs'][0]['example_rule']['id']
        value=self.screens()
        for screen in value['screens']: screen['rule_ids']=[rule_id]
        for api in value['apis']: api['rule_ids']=[rule_id]
        spec=self.spec()
        value.update(sections=spec['sections'],work_items=spec['work_items'],monorepo_layout=spec['monorepo_layout'],questions=[
            {'id':'Q1','question':'Confirm design','owner':'Architect','impact':'Unapproved design','blocks':'Implementation'}])
        return value

    def test_failed_preview_attempts_stay_bounded_and_a_valid_design_is_reused(self):
        self.project['settings']['max_model_dollars'] = 100
        calls = {'count': 0}

        def flaky(provider, run, directory, role, message):
            calls['count'] += 1
            if calls['count'] < 3:
                return {'sections': {}}
            return self.runtime(provider, run, directory, role, message)

        for _ in range(2):
            with self.assertRaises(Blocked):
                preview.run(self.work, self.project, self.arguments, app, flaky)
        result = preview.run(self.work, self.project, self.arguments, app, flaky)
        self.assertEqual(result['status'], 'DRAFT_READY')
        state = app.read(self.work / 'previews' / 'transform-direct' / 'preview-state.json')
        self.assertEqual(state['failed_attempts'], 2)
        self.assertEqual(state['calls'], 3)
        preview.run(self.work, self.project, self.arguments, app, flaky)
        reused = app.read(self.work / 'previews' / 'transform-direct' / 'preview-state.json')
        self.assertEqual(reused['calls'], 3)
        self.assertEqual(calls['count'], 3)

    def test_preview_skips_agents_preserves_gate_state_and_exports_draft(self):
        before=copy.deepcopy(self.project)
        result=preview.run(self.work,self.project,self.arguments,app,self.runtime)
        self.assertEqual(result['status'],'DRAFT_READY')
        self.assertNotEqual(self.project.get('stage'),'PACKAGED')
        recorded=self.project.pop('verification_skip')
        self.assertEqual(recorded['owner'],'Fixture owner')
        self.assertEqual(recorded['reason'],'Preview AWS findings')
        self.assertEqual(self.project.pop('skip_bundle'), 'previews/transform-direct')
        self.assertGreater(self.project['settings'].pop('model_dollars_spent'), 0)
        before['settings'].pop('model_dollars_spent', None)
        self.assertEqual(self.project,before)
        saved=app.read(self.work/'project.json')
        self.assertEqual(saved.pop('verification_skip')['owner'],'Fixture owner')
        self.assertEqual(saved.pop('skip_bundle'), 'previews/transform-direct')
        self.assertGreater(saved['settings'].pop('model_dollars_spent'), 0)
        self.assertEqual(saved,before)
        with zipfile.ZipFile(result['archive']) as z:
            manifest=json.loads(z.read('ai-dlc.json'))
            self.assertEqual(manifest['verification'],'SKIPPED_BY_OWNER')
            self.assertEqual(manifest['phase'],'DRAFT_ENGINEERING_PREPARATION')
            self.assertIn('SKIPPED',z.read('assumptions.json').decode())
            self.assertIn('SKILL.md',z.namelist())
            self.assertIn('reframe-notice.md',z.namelist())
            self.assertIn('infrastructure-prompt.md',z.namelist())
            self.assertFalse(any('provider' in n or 'project.json'==n for n in z.namelist()))
            self.assertEqual(json.loads(z.read('acceptance-catalog.json'))[0]['status'],'NOT RUN')
            sequence=json.loads(z.read('modernization-sequence.json'))
            self.assertEqual([step['id'] for step in sequence],['stabilize','expose','decompose','replace-one-slice','validate-in-parallel','retire-after-signoff'])
            self.assertTrue(all(step['status']=='NOT_STARTED' for step in sequence))
            self.assertEqual(json.loads(z.read('capability-map.json'))['capabilities'][0]['disposition'],'unassigned')
            plan=json.loads(z.read('parallel-validation.json'))
            self.assertEqual(plan['status'],'NOT RUN')
            self.assertEqual([case['status'] for case in plan['rules'][0]['cases']],['NOT RUN']*5)
            self.assertEqual(json.loads(z.read('governance.json'))['retirement_status'],'NOT_READY')
            self.assertIn('skill-risk.md',z.namelist())
            self.assertTrue(json.loads(z.read('extraction-reading.json'))['source_stays_pinned'])
            self.assertEqual(json.loads(z.read('rule-coverage.json'))['status'], 'COMPLETE')
            self.assertEqual(json.loads(z.read('capability-map.json'))['acceptance'], 'PROPOSAL')
            self.assertIn('COPY-expanded',z.read('skill-extraction.md').decode())
        second=preview.run(self.work,self.project,self.arguments,app,lambda *a:self.fail('Reuse generated design'))
        self.assertEqual(second['model_calls'],1)

    def test_explicit_skip_and_assessment_approval_required(self):
        self.arguments.skip_validation=False
        with self.assertRaisesRegex(Blocked,'explicitly'):preview.run(self.work,self.project,self.arguments,app,self.runtime)
        self.arguments.skip_validation=True;self.project['approvals']={}
        with self.assertRaisesRegex(Blocked,'approval'):preview.run(self.work,self.project,self.arguments,app,self.runtime)

    def test_rule_import_keeps_conflicting_same_id_records(self):
        directory,run=app.verified_assessment(self.project,self.project['selected'])
        # A second valid export with the same AWS ID but different content remains distinct.
        path=directory/'assessment'/'conflict.json'
        path.write_text(json.dumps({'program_name':'FIXTURE','all_rules':[
            {'Rule_Id':'R1','Rule_Name':'Conflict','Rule_Description':'Different assertion'}]}))
        run['assessment_index'].append({'path':path.name,'sha256':sha(path)})
        _,rules=preview.extract(directory,run,app)
        self.assertEqual(len(rules),2)
        self.assertNotEqual(rules[0]['id'],rules[1]['id'])

    def test_tampered_generated_design_is_rejected(self):
        preview.run(self.work,self.project,self.arguments,app,self.runtime)
        (self.work/'previews'/'transform-direct'/'proposed-design.json').write_text('{}')
        with self.assertRaisesRegex(Blocked,'design changed'):preview.run(self.work,self.project,self.arguments,app,self.runtime)

    def test_model_failure_keeps_separate_call_count_and_original_pipeline(self):
        def fail(*a):raise app.kw.RuntimeUnavailable('Fixture provider failure')
        with self.assertRaises(app.kw.RuntimeUnavailable):preview.run(self.work,self.project,self.arguments,app,fail)
        state=app.read(self.work/'previews'/'transform-direct'/'preview-state.json')
        self.assertEqual(state['calls'],1)
        self.assertEqual(self.project['pipeline']['calls'],64)

    def test_untrusted_screen_text_is_escaped(self):
        def runtime(*args):
            value=self.runtime(*args);value['screens'][0]['title']='<script>alert(1)</script>';return value
        result=preview.run(self.work,self.project,self.arguments,app,runtime)
        content=Path(result['html']).read_text()
        self.assertNotIn('<script>',content)
        self.assertIn('&lt;script&gt;',content)

    def test_jcl_rules_format_is_imported_with_its_original_pointer(self):
        directory,run=app.verified_assessment(self.project,self.project['selected'])
        path=directory/'assessment'/'job.json'
        path.write_text(json.dumps({'name':'JOB1','rules':[{'Rule_Id':'J1','Rule_Name':'Sort records','Rule_Description':'Sort input'}]}))
        run['assessment_index'].append({'path':path.name,'sha256':sha(path)})
        _,rules=preview.extract(directory,run,app)
        job=next(r for r in rules if r['program']=='JOB1')
        self.assertEqual(job['source_anchor'],'/rules/0')
        self.assertEqual(job['evidence_status'],'AWS_ASSUMED')

    def test_export_refresh_reuses_verified_design_without_model_call(self):
        preview.run(self.work,self.project,self.arguments,app,self.runtime)
        state_path=self.work/'previews'/'transform-direct'/'preview-state.json'
        state=app.read(state_path);state['framework_commit']='b'*40;app.write(state_path,state)
        self.arguments.refresh_export=True
        result=preview.run(self.work,self.project,self.arguments,app,lambda *a:self.fail('No generation during export refresh'))
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(app.read(state_path)['generation_framework_commit'],'b'*40)

    def test_named_screen_states_are_normalized_without_losing_text(self):
        value={'screens':[{'states':{'normal':'Ready','rejection':'Invalid','boundary':'Limit'}}]}
        result=preview.normalize_design(value)
        self.assertEqual(result['screens'][0]['states'],['Normal: Ready','Rejection: Invalid','Boundary: Limit'])
        self.assertIsInstance(value['screens'][0]['states'],dict)

    def test_saved_reply_requires_matching_provider_receipt(self):
        d=self.work/'response';d.mkdir()
        raw={'screens':[{'states':{'normal':'Ready'}}]}
        app.write(d/'design-reply-1.json',raw)
        app.write(d/'provider-1.json',{'provider':'claude','returncode':0,'stdout':json.dumps({'result':json.dumps(raw)})})
        state={'provider':'claude','calls':1}
        result=preview.verified_saved_reply(d,state,app)
        self.assertEqual(result['screens'][0]['states'],['Normal: Ready'])
        app.write(d/'design-reply-1.json',{})
        with self.assertRaisesRegex(Blocked,'differs'):preview.verified_saved_reply(d,state,app)
