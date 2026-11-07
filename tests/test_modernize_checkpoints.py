import argparse
import copy
import json
import unittest
from unittest.mock import patch

import test_modernize as fixtures
import modernize as app
import modernize_checkpoints as cp
from pilot import Blocked, sha


class CheckpointTests(unittest.TestCase):
    args = fixtures.PreparationTests.args
    assess = fixtures.PreparationTests.assess
    approve = fixtures.PreparationTests.approve
    baseline = fixtures.PreparationTests.baseline
    spec = fixtures.PreparationTests.spec

    def setUp(self):
        fixtures.PreparationTests.setUp(self)
        mock = patch.object(cp, 'git', side_effect=app.git)
        mock.start(); self.addCleanup(mock.stop)
        self.baseline()
        self.project['settings']['max_model_calls'] = 32
        self.seen = []

    def plan(self):
        return app.preparation_plan(self.work, self.project, argparse.Namespace(activate=True))

    def checkpoint(self, context):
        unit = context['unit_id']
        coverage, findings, questions = [], [], []
        for index, part in enumerate(context['evidence']):
            coverage.append({k:part[k] for k in ('area','path','start','end')} | {
                'status':'NEEDS_SME' if part.get('opaque') else 'REVIEWED', 'note':'Fixture inspected range'})
            if part.get('opaque') or part.get('empty'):
                if part.get('opaque'):
                    questions.append({'id':unit+'-Q'+str(index),'question':'Decode opaque file','owner':'SME',
                                      'impact':'Behavior unknown','blocks':'Implementation'})
                continue
            line = part['text'].splitlines()[0].split(': ',1)[1]
            kind = 'business_rule' if part['area'] == 'source' else 'assessment_claim'
            findings.append({'id':unit+'-F'+str(index),'kind':kind,'statement':'Reject zero' if kind == 'business_rule' else 'AWS claim',
                'status':'SOURCE_VERIFIED' if kind == 'business_rule' else 'INFERRED', 'citations':[
                    {'area':part['area'],'path':part['path'],'start':part['start'],'end':part['start'],'quote':line}]})
            if kind == 'assessment_claim':
                questions.append({'id':unit+'-Q'+str(index),'question':'Reconcile report','owner':'SME',
                                  'impact':'Unproven report claim','blocks':'Implementation'})
        return {'kind':'checkpoint','summary':'Fixture evidence checkpoint','coverage':coverage,'findings':findings,'questions':questions}

    def runtime(self, provider, run, directory, role, message):
        context = json.loads(message)
        self.seen.append((role, context))
        if 'unit_id' in context:
            if role == 'executor': return self.checkpoint(context)
            self.assertTrue(context['evidence'][0].get('text'))
            return {'kind':'review','decision':'PASS','report':'Independent fixture review','findings':[]}
        if role == 'reviewer':
            return {'kind':'review','decision':'PASS','report':'Integration fixture review','findings':[]}
        capsules = context['reviewed_evidence_units']
        rule = next(f for c in capsules for f in c['checkpoint']['findings'] if f['kind']=='business_rule')
        spec = self.spec()
        spec['rules'][0]['id'] = rule['id']
        for section in spec['sections'].values(): section['rule_ids'] = [rule['id']]
        for test in spec['tests']: test['rule_id'] = rule['id']
        spec['questions'] = [q for c in capsules for q in c['checkpoint']['questions']]
        return {'kind':'candidate','specification':spec}

    def test_default_workflow_reviews_units_then_integrates_and_preserves_human_gate(self):
        result = app.pipeline(self.work, self.project, self.args('validate'), self.runtime)
        self.assertEqual(result['status'],'AWAITING_VALIDATION_APPROVAL')
        self.assertEqual(result['calls'],6)
        self.assertTrue(all(u['status']=='PASS' for u in self.project['checkpoints']['units'].values()))
        self.assertEqual(set(self.project['approvals']), {'assessment'})
        self.assertIn('reviewed_evidence_units', self.seen[-2][1])
        self.assertTrue((self.work/self.project['preparation_report']['path']).is_file())

    def test_preview_does_not_activate_or_change_budget(self):
        result = app.preparation_plan(self.work, self.project, argparse.Namespace(activate=False))
        self.assertEqual(result['minimum_calls'],6)
        self.assertNotIn('checkpoints', self.project)
        self.assertNotIn('pipeline', self.project)
        self.assertEqual(self.project['settings']['max_model_calls'],32)

    def test_budget_preflight_spends_nothing(self):
        self.plan(); self.project['settings']['max_model_calls']=5
        result=app.pipeline(self.work,self.project,self.args('validate'),lambda *a:self.fail('No call without minimum budget'))
        self.assertEqual(result['minimum_additional_calls'],6)
        self.assertEqual(self.project['pipeline']['calls'],0)

    def test_migration_archives_legacy_state_and_retains_approval_and_calls(self):
        self.project['pipeline']={'calls':64,'candidate_sha256':'old','contexts':{'1:executor':[{'response':['old']} ]},'status':'BLOCKED'}
        self.project['stage']='BLOCKED'
        approval=copy.deepcopy(self.project['approvals'])
        self.plan()
        state=self.project['checkpoints']
        self.assertEqual(app.read(self.work/state['legacy_state'])['calls'],64)
        self.assertEqual(self.project['pipeline']['calls'],64)
        self.assertEqual(self.project['approvals'],approval)
        self.assertEqual(sha(self.work/state['legacy_state']),state['legacy_sha256'])

    def test_resume_reuses_passed_units_after_interrupted_provider(self):
        self.plan()
        def interrupt(provider, run, directory, role, message):
            if run['model_calls']==4: raise RuntimeError('fixture interruption')
            return self.runtime(provider,run,directory,role,message)
        with self.assertRaises(RuntimeError): app.pipeline(self.work,self.project,self.args('validate'),interrupt)
        self.assertEqual(self.project['pipeline']['calls'],4)
        first=copy.deepcopy(self.project['checkpoints']['units']['U0001'])
        self.project=app.read(self.work/'project.json')
        before=len(self.seen)
        result=app.pipeline(self.work,self.project,self.args('resume'),self.runtime)
        self.assertEqual(result['calls'],7)
        self.assertEqual(self.project['checkpoints']['units']['U0001'],first)
        self.assertFalse(any(c.get('unit_id')=='U0001' for _,c in self.seen[before:]))

    def test_tampered_passed_checkpoint_blocks_without_model_call(self):
        self.plan()
        def interrupt(provider, run, directory, role, message):
            if run['model_calls']==3: raise RuntimeError('stop')
            return self.runtime(provider,run,directory,role,message)
        with self.assertRaises(RuntimeError): app.pipeline(self.work,self.project,self.args('validate'),interrupt)
        record=self.project['checkpoints']['units']['U0001']
        (self.work/record['candidate_path']).write_text('{}')
        with self.assertRaisesRegex(Blocked,'handoff changed'):
            app.pipeline(self.work,self.project,self.args('resume'),lambda *a:self.fail('No call after tampering'))

    def test_candidate_quotes_must_match_supplied_evidence(self):
        self.plan()
        plan,_=cp.verified_capsules(self.work,self.project,app)
        unit=plan['units'][0]
        directory,_=app.verified_assessment(self.project,self.project['selected'])
        supplied=cp.evidence(unit,self.project,directory)
        candidate=self.checkpoint({'unit_id':unit['id'],'evidence':supplied})
        candidate['findings'][0]['citations'][0]['quote']='invented'
        with self.assertRaisesRegex(Blocked,'does not match'):cp.validate_checkpoint(candidate,unit,supplied)

    def test_omitted_segments_and_false_aws_certainty_rejected(self):
        self.plan();plan,_=cp.verified_capsules(self.work,self.project,app)
        directory,_=app.verified_assessment(self.project,self.project['selected'])
        unit=plan['units'][-1]; supplied=cp.evidence(unit,self.project,directory)
        candidate=self.checkpoint({'unit_id':unit['id'],'evidence':supplied})
        candidate['findings'][0]['status']='SOURCE_VERIFIED'
        with self.assertRaisesRegex(Blocked,'AWS claims'): cp.validate_checkpoint(candidate,unit,supplied)
        candidate['coverage']=[]
        with self.assertRaisesRegex(Blocked,'every supplied segment'): cp.validate_checkpoint(candidate,unit,supplied)

    def test_unit_rework_does_not_reopen_passed_units(self):
        self.plan()
        def runtime(provider,run,directory,role,message):
            context=json.loads(message)
            if context.get('unit_id')=='U0002' and role=='reviewer' and not context.get('previous_review'):
                return {'kind':'review','decision':'REWORK','report':'Correct uncertainty','findings':['Fix claim']}
            return self.runtime(provider,run,directory,role,message)
        result=app.pipeline(self.work,self.project,self.args('validate'),runtime)
        self.assertEqual(result['calls'],8)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['attempt'],1)
        self.assertEqual(self.project['checkpoints']['units']['U0002']['attempt'],2)

    def test_plan_preserves_all_lines_and_marks_binary_unresolved(self):
        path=self.root/'large.txt';path.write_text('\n'.join('line '+str(i) for i in range(1200)))
        parts=cp.segments('source','large.txt',path,sha(path))
        self.assertEqual([n for p in parts for n in range(p['start'],p['end']+1)],list(range(1,1201)))
        self.assertTrue(all(p['end']-p['start']<cp.MAX_LINES for p in parts))
        path.write_bytes(b'\x00binary')
        self.assertTrue(cp.segments('source','binary',path,sha(path))[0]['opaque'])

    def test_context_overflow_stops_before_spending_call(self):
        self.plan();self.project['settings']['max_context_bytes']=100
        with self.assertRaisesRegex(Blocked,'context limit'):
            app.pipeline(self.work,self.project,self.args('validate'),lambda *a:self.fail('No oversized provider call'))
        self.assertEqual(self.project['pipeline']['calls'],0)

    def test_invalid_schema_stops_after_three_calls(self):
        self.plan()
        result=app.pipeline(self.work,self.project,self.args('validate'),lambda *a:{'kind':'checkpoint','summary':'Missing coverage'})
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(self.project['pipeline']['calls'],3)
        with self.assertRaisesRegex(Blocked,'blocked evidence unit'):
            app.pipeline(self.work,self.project,self.args('resume'),lambda *a:self.fail('No automatic retry'))

    def test_synthesis_cannot_drop_checkpoint_rules_or_questions(self):
        self.plan()
        observed=[]
        def runtime(provider,run,directory,role,message):
            context=json.loads(message)
            if 'unit_id' not in context and role=='executor':
                observed.append(context)
                if len(observed)==1:
                    reply=self.runtime(provider,run,directory,role,message)
                    reply['specification']['questions']=[]
                    return reply
                self.assertIn('omitted checkpoint SME questions',context['candidate_correction']['error'])
            return self.runtime(provider,run,directory,role,message)
        # Semantic correction feedback is saved alongside schema feedback.
        result=app.pipeline(self.work,self.project,self.args('validate'),runtime)
        self.assertEqual(result['status'],'AWAITING_VALIDATION_APPROVAL')

    def test_checkpoint_tamper_blocks_post_synthesis_approval(self):
        app.pipeline(self.work,self.project,self.args('validate'),self.runtime)
        unit=self.project['checkpoints']['units']['U0001']
        (self.work/unit['review_path']).write_text('{}')
        with self.assertRaisesRegex(Blocked,'handoff changed'):app.spec_path(self.work,self.project)

    def test_checkpoint_synthesis_keeps_legacy_candidate_files(self):
        area=self.work/'preparation';area.mkdir()
        app.write(area/'candidate-1.json',{'legacy':'unchanged'})
        original=sha(area/'candidate-1.json')
        app.pipeline(self.work,self.project,self.args('validate'),self.runtime)
        self.assertEqual(sha(area/'candidate-1.json'),original)
        self.assertIn('checkpoint-candidate',self.project['specification']['path'])

    def test_json_projection_preserves_conflicts_and_identifies_only_exact_duplicates(self):
        value={'a':{'id':'R1','text':'same'}, 'b':{'id':'R1','text':'same'}, 'c':{'id':'R1','text':'different'}}
        records=[json.loads(line) for line in cp.json_projection(value)]
        self.assertIn({'pointer':'/b','same_value_as':'/a'},records)
        self.assertIn({'pointer':'/c/text','value':'different'},records)
        escaped=[json.loads(line) for line in cp.json_projection({'a/b~':None})]
        self.assertIn({'pointer':'/a~1b~0','value':None},escaped)

    def test_projection_quotes_and_original_source_lines_are_distinguished(self):
        raw=b'{"rule": {"text": "reject zero"}}'
        source,source_format=cp.evidence_lines(raw,'source')
        projected,assessment_format=cp.evidence_lines(raw,'assessment')
        self.assertEqual(source_format,'original-lines')
        self.assertEqual(assessment_format,'json-pointer-records-v1')
        self.assertEqual(json.loads(projected[-1])['pointer'],'/rule/text')
        self.assertEqual(source,[raw.decode()])

    def test_supplemental_source_is_kept_for_independent_reviewer(self):
        self.plan()
        requested=False
        def runtime(provider,run,directory,role,message):
            nonlocal requested
            context=json.loads(message)
            if context.get('unit_id')=='U0002':
                if role=='executor' and not requested:
                    requested=True
                    return {'kind':'read','area':'source','paths':['rule.cbl'],'start':1,'end':3}
                if role=='reviewer':
                    self.assertTrue(any(p['area']=='source' and 'IF AMOUNT' in p.get('text','') for p in context['evidence']))
            return self.runtime(provider,run,directory,role,message)
        # Stop at synthesis so this test isolates supplement handoff, not fixture synthesis rules.
        with patch.object(app,'legacy_pipeline',return_value={'status':'READY_FOR_SYNTHESIS'}):
            result=app.pipeline(self.work,self.project,self.args('validate'),runtime)
        self.assertEqual(result['status'],'READY_FOR_SYNTHESIS')
        self.assertTrue(requested)
        self.assertEqual(self.project['pipeline']['calls'],5)

    def test_feedback_reopens_only_blocked_unit_without_resetting_calls(self):
        self.plan()
        app.pipeline(self.work,self.project,self.args('validate'),lambda *a:{'kind':'blocked','reason':'Need SME clarification'})
        consumed=self.project['pipeline']['calls']
        result=app.feedback(self.work,self.project,self.args('feedback','--unit','U0001','--by','Fixture owner','--note','Keep the uncertainty explicit'))
        self.assertEqual(result['calls_consumed'],consumed)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['attempt'],2)
        self.assertEqual(self.project['stage'],'RUNNING_EXECUTOR')
        self.assertEqual(set(self.project['approvals']),{'assessment'})

    def test_pointer_projection_round_trip_preserves_container_types_and_references(self):
        original={'object':{'0':True,'nested':[]},'array':[{'0':True,'nested':[]},None,3.5], 'escaped/key~':'value'}
        values={}
        for line in cp.json_projection(original):
            row=json.loads(line)
            pointer=row['pointer']
            if 'same_value_as' in row: value=copy.deepcopy(values[row['same_value_as']])
            elif 'container' in row: value={} if row['container']=='object' else []
            else: value=row['value']
            values[pointer]=value
            if pointer:
                parent,token=pointer.rsplit('/',1)
                key=token.replace('~1','/').replace('~0','~')
                if isinstance(values[parent],list): values[parent].append(value)
                else: values[parent][key]=value
        self.assertEqual(values[''],original)

    def test_current_rule_files_exclude_unrelated_source(self):
        self.source_files = ['other.cbl', 'rule.cbl']
        (self.source / 'other.cbl').write_text('DISPLAY "OTHER".\n')
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            self.seen.append((role, context))
            if 'unit_id' not in context:
                return {'kind': 'blocked', 'reason': 'Stop before synthesis'}
            if role == 'executor':
                self.assertNotIn('candidate', context)
                return self.checkpoint(context)
            self.assertIn('candidate', context)
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Independent fixture review', 'findings': []}
        with patch.object(app, 'legacy_pipeline', return_value={'status': 'READY_FOR_SYNTHESIS'}):
            app.pipeline(self.work, self.project, self.args('validate'), runtime)
        listed = {}
        for role, context in self.seen:
            if 'unit_id' not in context:
                continue
            sources = sorted({part['path'] for part in context['evidence'] if part['area'] == 'source'})
            self.assertEqual(context['source_files'], sources)
            listed.setdefault(tuple(sources), set()).add(role)
            if sources == ['rule.cbl']:
                self.assertNotIn('other.cbl', json.dumps(context))
            if sources == ['other.cbl']:
                self.assertNotIn('rule.cbl', json.dumps(context))
        self.assertIn(('rule.cbl',), listed)
        self.assertIn(('other.cbl',), listed)
        self.assertEqual(listed[('rule.cbl',)], {'executor', 'reviewer'})
        self.assertEqual(listed[('other.cbl',)], {'executor', 'reviewer'})

    def test_reviewer_cannot_write_the_candidate(self):
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' not in context:
                return {'kind': 'blocked', 'reason': 'Stop before synthesis'}
            if role == 'executor':
                return self.checkpoint(context)
            path = self.work / self.project['checkpoints']['units'][context['unit_id']]['candidate_path']
            path.write_text(path.read_text() + '\n')
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Tried to rewrite the candidate', 'findings': []}
        with self.assertRaisesRegex(Blocked, 'Reviewer cannot write the candidate'):
            app.pipeline(self.work, self.project, self.args('validate'), runtime)
