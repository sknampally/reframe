import argparse
import copy
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import test_reframe as fixtures
import reframe as app
import reframe_artifacts as artifacts
import reframe_checkpoints as cp
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
        reply = {'kind':'checkpoint','summary':'Fixture evidence checkpoint','coverage':coverage,'findings':findings,'questions':questions}
        defects = cp.review_defects(context.get('previous_review'))
        if defects:
            quote = next((item['quote'] for finding in findings for item in finding.get('citations', []) if item.get('quote')), '')
            reply['dispositions'] = [{'finding_id': defect['id'], 'disposition': 'disputed', 'evidence': quote}
                                     for defect in defects]
        return reply

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
        synthesis_review = next(ctx for role, ctx in reversed(self.seen) if role == 'reviewer' and 'unit_id' not in ctx)
        self.assertNotIn('reviewed_evidence_units', synthesis_review)
        self.assertIn('assembled specification', synthesis_review['checkpoint_instruction'])
        self.assertTrue((self.work/self.project['preparation_report']['path']).is_file())

    def test_synthesis_contradiction_gets_one_correction_after_the_round_limit(self):
        reviews = {'n': 0}
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if role == 'reviewer' and 'unit_id' not in context:
                reviews['n'] += 1
                if reviews['n'] < 3:
                    return {'kind':'review', 'decision':'REWORK', 'report':'The screen count contradicts the inventory',
                            'findings':[{'id':'RV', 'issue':'screen count'}]}
                self.assertEqual(context['previous_review']['report'], 'The screen count contradicts the inventory')
                return {'kind':'review', 'decision':'PASS', 'report':'The corrected specification matches the inventory', 'findings':[]}
            return self.runtime(provider, run, directory, role, message)
        first = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(first['status'], 'REWORK_LIMIT')
        self.assertIn('Synthesis review returned REWORK', first['reason'])
        self.assertIn('RV', first['reason'])
        from reframe_setup import next_step
        self.assertIn('reframe resume', next_step(self.project))
        self.assertNotIn('feedback --unit', next_step(self.project))
        self.assertEqual(reviews['n'], 2)
        self.project = app.read(self.work / 'project.json')
        second = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(second['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.assertEqual(self.project['pipeline'].get('limit_corrections'), 1)
        self.assertEqual(reviews['n'], 3)

    def test_a_second_synthesis_contradiction_stays_stopped(self):
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if role == 'reviewer' and 'unit_id' not in context:
                return {'kind':'review', 'decision':'REWORK', 'report':'The screen count still contradicts the inventory',
                        'findings':[{'id':'RV', 'issue':'screen count'}]}
            return self.runtime(provider, run, directory, role, message)
        self.assertEqual(app.pipeline(self.work, self.project, self.args('validate'), runtime)['status'], 'REWORK_LIMIT')
        self.project = app.read(self.work / 'project.json')
        self.assertEqual(app.pipeline(self.work, self.project, self.args('resume'), runtime)['status'], 'REWORK_LIMIT')
        self.project = app.read(self.work / 'project.json')
        self.assertEqual(app.pipeline(self.work, self.project, self.args('resume'), runtime)['status'], 'REWORK_LIMIT')
        self.project = app.read(self.work / 'project.json')
        with self.assertRaisesRegex(Blocked, 'Resolve missing evidence via feedback before retrying'):
            app.pipeline(self.work, self.project, self.args('resume'), runtime)

    def test_finding_program_name_joins_that_source_file(self):
        (self.source / 'APP').mkdir()
        (self.source / 'APP' / 'MEMBER.TXT').write_text('IDENTIFICATION DIVISION.\n')
        (self.source / 'APP' / 'COPY.TXT').write_text('01 ITEM.\n')
        self.source_files += ['APP/MEMBER.TXT', 'APP/COPY.TXT']
        folder = self.root / 'sealed' / 'assessment'
        folder.mkdir(parents=True)
        (folder / 'finding-1.json').write_text(json.dumps({
            'documentation': {'high_level_overview': {'program_information': {'name': 'MEMBER.TXT', 'type': 'cobol'}}}}))
        (folder / 'rules.json').write_text('{"rules":["reject zero amount"]}')
        (folder / 'extract.json').write_text(json.dumps({'rules': ['x' * 4000]}))
        index = [{'path': name, 'sha256': sha(folder / name)} for name in ('finding-1.json', 'rules.json', 'extract.json')]
        self.project['selected'] = 'a'
        self.project['assessments']['a']['sha256'] = 'fixture'
        catalog = [{'path': item['path'], 'label': 'APP/MEMBER-cbl.json' if item['path'] == 'extract.json' else item['path']}
                   for item in index]
        plan = cp.make_plan(self.project, folder.parent, {'assessment_index': index}, catalog)
        focuses = {unit['focus'] for unit in plan['units']}
        self.assertIn('APP/MEMBER.TXT', focuses)
        self.assertNotIn('APP/COPY.TXT', focuses)
        self.assertIn('APP/COPY.TXT', plan['unlinked_source'])
        self.assertIn('rule.cbl', plan['unlinked_source'])
        self.assertEqual(plan['unlinked_assessment'], ['extract.json', 'rules.json'])
        paired = next(unit for unit in plan['units'] if unit['focus'] == 'APP/MEMBER.TXT')
        self.assertEqual({part['path'] for part in paired['segments']}, {'APP/MEMBER.TXT', 'finding-1.json'})
        self.assertEqual(focuses, {'APP/MEMBER.TXT'})

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
        full = candidate['findings'][0]['citations'][0]['quote']
        candidate['findings'][0]['citations'][0]['quote'] = full[:12]
        cp.validate_checkpoint(candidate, unit, supplied)
        candidate['findings'][0]['citations'][0]['quote']='invented'
        with self.assertRaisesRegex(Blocked,'does not match'):cp.validate_checkpoint(candidate,unit,supplied)

    def test_synthesis_view_fits_seventy_units_in_the_context_limit(self):
        capsule = {'unit':'U0001', 'review_sha256':'a'*64, 'checkpoint':{
            'summary':'s'*400, 'coverage':[{'note':'n'*500}],
            'findings':[{'id':'U0001-F1', 'kind':'business_rule', 'statement':'x'*240, 'status':'SOURCE_VERIFIED',
                         'citations':[{'area':'source', 'path':'a.txt', 'start':1, 'end':1, 'quote':'q'*400}]}],
            'questions':[{'id':'U0001-Q1', 'question':'q'*160, 'impact':'i'*80, 'owner':'SME', 'blocks':'b'*80}]}}
        views = []
        for index in range(70):
            item = json.loads(json.dumps(capsule))
            item['unit'] = f'U{index+1:04d}'
            item['checkpoint']['findings'][0]['id'] = item['unit'] + '-F1'
            item['checkpoint']['questions'][0]['id'] = item['unit'] + '-Q1'
            view = cp.synthesis_view(item)
            self.assertNotIn('coverage', view['checkpoint'])
            self.assertNotIn('citations', view['checkpoint']['findings'][0])
            self.assertNotIn('"quote"', json.dumps(view))
            views.append(view)
        catalog = [{'path': f'finding-{i}.json', 'label': f'PROGRAM{i}.txt'} for i in range(360)]
        message = json.dumps({'assessment_files': catalog, 'reviewed_evidence_units': views})
        self.assertLess(len(message.encode()), 180000)

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
        path.write_bytes(b'       IDENTIFICATION DIVISION.\xb3\n')
        parts = cp.segments('source','member.txt',path,sha(path))
        self.assertFalse(parts[0].get('opaque'))
        self.assertIn('\u00b3', cp.evidence_lines(path.read_bytes(), 'source')[0][0])

    def test_non_utf8_source_is_replanned_without_another_call(self):
        member = self.source / 'MEMBER.TXT'
        member.write_bytes(b'       IDENTIFICATION DIVISION.\xb3\n')
        self.source_files.append('MEMBER.TXT')
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = json.loads(plan_path.read_text())
        plan['units'][0]['segments'][0] = {'area':'source', 'path':'MEMBER.TXT', 'sha256':sha(member),
                                           'start':0, 'end':0, 'opaque':True, 'bytes':member.stat().st_size}
        mutated = plan_path.with_name('plan-opaque.json')
        mutated.write_text(json.dumps(plan))
        self.project['checkpoints']['plan_path'] = str(mutated.relative_to(self.work))
        self.project['checkpoints']['plan_sha256'] = sha(mutated)
        self.project['checkpoints']['units'] = {'U0001': {'status':'BLOCKED', 'role':'executor', 'attempt':1}}
        self.project['pipeline']['calls'] = 3
        self.project['pipeline']['status'] = 'BLOCKED'
        self.project['stage'] = 'BLOCKED'
        self.project['settings']['max_model_calls'] = 3
        app.persist(self.work, self.project)
        result = app.pipeline(self.work, self.project, self.args('validate'), lambda *a: self.fail('No model call'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.project['pipeline']['calls'], 3)
        saved = app.read(self.work / self.project['checkpoints']['plan_path'])
        member_parts = [part for unit in saved['units'] for part in unit['segments'] if part['path'] == 'MEMBER.TXT']
        self.assertTrue(member_parts and not member_parts[0].get('opaque'))

    def test_context_overflow_stops_before_spending_call(self):
        self.plan();self.project['settings']['max_context_bytes']=100
        with self.assertRaisesRegex(Blocked,'context limit'):
            app.pipeline(self.work,self.project,self.args('validate'),lambda *a:self.fail('No oversized provider call'))
        self.assertEqual(self.project['pipeline']['calls'],0)

    def test_reviewer_passes_matching_claims_without_an_inventory_round(self):
        text = (app.FRAMEWORK / 'prompts/checkpoints/reviewer.md').read_text()
        self.assertIn('PASS when every written claim matches its citation', text)
        self.assertNotIn('may request specific omitted behavior', text)

    def test_rework_limit_still_reviews_the_next_program_and_retries_once(self):
        self.plan()
        seen = {'U0001': 0}
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if context.get('unit_id') == 'U0001' and role == 'reviewer':
                seen['U0001'] += 1
                if seen['U0001'] < 3:
                    return {'kind':'review', 'decision':'REWORK', 'report':'Add the omitted branch',
                            'findings':['Record the omitted branch']}
            return self.runtime(provider, run, directory, role, message)
        first = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(first['status'], 'REWORK_LIMIT')
        self.assertEqual(self.project['checkpoints']['units']['U0002']['status'], 'PASS')
        self.assertEqual(self.project['checkpoints']['units']['U0001']['status'], 'REWORK_LIMIT')
        second = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['status'], 'PASS')
        self.assertEqual(seen['U0001'], 3)
        self.assertEqual(second['status'], 'AWAITING_VALIDATION_APPROVAL')

    def test_failed_limit_review_gets_one_executor_correction(self):
        self.plan()
        seen = {'U0001': 0}
        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if context.get('unit_id') == 'U0001' and role == 'reviewer':
                seen['U0001'] += 1
                if seen['U0001'] < 4:
                    return {'kind':'review', 'decision':'REWORK', 'report':'The cited branch is contradicted',
                            'findings':[{'issue':'The cited branch is contradicted'}]}
            return self.runtime(provider, run, directory, role, message)
        self.assertEqual(app.pipeline(self.work, self.project, self.args('validate'), runtime)['status'], 'REWORK_LIMIT')
        self.assertEqual(seen['U0001'], 2)
        self.project['settings']['max_model_calls'] = self.project['pipeline']['calls']
        blocked = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(blocked['reason'], 'Insufficient call budget for remaining checkpoint plan')
        self.assertEqual(blocked['minimum_additional_calls'], 4)
        self.assertEqual(seen['U0001'], 2)
        self.project['settings']['max_model_calls'] = self.project['pipeline']['calls'] + 8
        third = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(third['status'], 'REWORK_LIMIT')
        self.assertEqual(seen['U0001'], 3)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['corrections_used'], 1)
        stalled = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(stalled['status'], 'REWORK_LIMIT')
        self.assertEqual(seen['U0001'], 3)
        self.assertIn('authorize-correction', stalled['next_step'])
        attempt = self.project['checkpoints']['units']['U0001']['attempt']
        consumed = self.project['pipeline']['calls']
        authorized = app.feedback(self.work, self.project, self.args(
            'feedback', '--unit', 'U0001', '--authorize-correction', '--by', 'Fixture owner', '--note', 'One more correction'))
        self.assertEqual(authorized['calls_consumed'], consumed)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['attempt'], attempt)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['correction_authorizations'], 1)
        done = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['status'], 'PASS')
        self.assertEqual(seen['U0001'], 4)
        self.assertEqual(done['status'], 'AWAITING_VALIDATION_APPROVAL')

    def test_synthesis_restores_rules_and_questions_the_model_omitted(self):
        question = {'id':'Q1', 'question':'What limit applies', 'impact':'Stops the case',
                    'owner':'SME', 'blocks':'The case cannot be approved'}
        plan = {'units':[{'id':'U0001', 'segments':[{'area':'source', 'path':'a.txt'}]}]}
        capsules = [{'unit':'U0001', 'checkpoint':{
            'findings':[{'id':'R1', 'kind':'business_rule', 'statement':'Reject a zero amount',
                         'status':'SOURCE_VERIFIED',
                         'citations':[{'area':'source', 'path':'a.txt', 'start':4, 'end':6, 'quote':'REJECT'}]}],
            'questions':[question]}}]
        spec = {'rules':[{'id':'local', 'statement':'Local rule', 'source_path':'a.txt',
                          'source_anchor':'lines 1-1', 'evidence_status':'SOURCE_VERIFIED'}],
                'questions':[{'id':'Q1', 'question':'changed'},
                             {'id':'QX', 'question':'Which split applies', 'impact':'Blocks the service boundary',
                              'owner':'Architect', 'blocks':'Service design'}],
                'monorepo_layout':[{'path':'apps/ui', 'role':'interface'}],
                'tests':[{'id':'local-T', 'rule_id':'local', 'category':'normal', 'status':'NOT RUN',
                         'given':'g', 'when':'w', 'then':'t', 'oracle':'o'}]}
        cp.complete_synthesis(spec, capsules, plan)
        self.assertEqual(spec['monorepo_layout'], ['{"path": "apps/ui", "role": "interface"}'])
        self.assertEqual({rule['id'] for rule in spec['rules']}, {'local', 'R1'})
        added = next(rule for rule in spec['rules'] if rule['id'] == 'R1')
        self.assertEqual(added['source_path'], 'a.txt')
        self.assertEqual(added['source_anchor'], 'lines 4-6')
        self.assertEqual(spec['questions'], [question, {
            'id':'QX', 'question':'Which split applies', 'impact':'Blocks the service boundary',
            'owner':'Architect', 'blocks':'Service design'}])
        self.assertEqual(spec['tests'][-1], {
            'id': 'R1-accept', 'rule_id': 'R1', 'category': 'boundary', 'status': 'NOT RUN',
            'given': 'The reviewed condition for R1', 'when': 'That condition is evaluated',
            'then': 'Reject a zero amount', 'oracle': 'Compare with a.txt lines 4-6'})

    def test_spent_cap_before_synthesis_asks_for_two_calls(self):
        self.plan()
        ready = app.pipeline(self.work, self.project, self.args('validate'), self.runtime)
        self.assertEqual(ready['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.project.pop('specification')
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['pipeline']['calls'] = self.project['settings']['max_model_calls']
        self.project['pipeline']['status'] = 'BLOCKED'
        self.project['pipeline']['reason'] = 'Total model call budget exhausted'
        self.project['stage'] = 'BLOCKED'
        blocked = app.pipeline(self.work, self.project, self.args('resume'), self.runtime)
        self.assertEqual(blocked['reason'], 'Insufficient call budget for remaining checkpoint plan')
        self.assertEqual(blocked['minimum_additional_calls'], 2)
        self.project['settings']['max_model_calls'] = self.project['pipeline']['calls'] + 2
        done = app.pipeline(self.work, self.project, self.args('resume'), self.runtime)
        self.assertEqual(done['status'], 'AWAITING_VALIDATION_APPROVAL')

    def test_three_invalid_synthesis_candidates_can_resume(self):
        self.plan()
        ready = app.pipeline(self.work, self.project, self.args('validate'), self.runtime)
        self.assertEqual(ready['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.project.pop('specification')
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['pipeline'].update(status='BLOCKED', invalid_candidates=3,
            reason='Three invalid synthesis candidates; inspect validation feedback')
        self.project['stage'] = 'BLOCKED'
        done = app.pipeline(self.work, self.project, self.args('resume'), self.runtime)
        self.assertEqual(done['status'], 'AWAITING_VALIDATION_APPROVAL')

    def test_invalid_schema_stops_after_three_calls(self):
        self.plan()
        result=app.pipeline(self.work,self.project,self.args('validate'),lambda *a:{'kind':'checkpoint','summary':'Missing coverage'})
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(self.project['pipeline']['calls'],3)
        calls = self.project['pipeline']['calls']
        paused = app.pipeline(self.work, self.project, self.args('resume'), lambda *a: self.fail('No automatic retry'))
        self.assertEqual(paused['status'], 'BLOCKED')
        self.assertEqual(paused['unit'], 'U0001')
        self.assertTrue(str(paused['reason']).startswith('U0001'))
        self.assertEqual(self.project['pipeline']['calls'], calls)

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

    def test_stale_targeted_claim_is_corrected_or_stopped_without_a_new_assessment(self):
        self.plan()
        original = 'A non-normal response sets the failure flag.'
        only = 'The card name field is chosen only for two named cases.'
        cosmetic = 'The   card name field is chosen only for two named cases.'
        fixed = 'The card name field is chosen for two named cases and the blank branch.'
        mode = {'statement': cosmetic}
        reviewers = {'n': 0}
        sent = []

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' not in context:
                return self.runtime(provider, run, directory, role, message)
            if context['unit_id'] != 'U0001':
                if role == 'executor':
                    return self.checkpoint(context)
                return {'kind': 'review', 'decision': 'PASS', 'report': 'U0002 passed', 'findings': []}
            if role == 'reviewer':
                reviewers['n'] += 1
                statement = next(item['statement'] for item in context['candidate']['findings'] if item['id'] == 'U0001-F7')
                if 'blank branch' in statement:
                    return {'kind': 'review', 'decision': 'PASS', 'report': 'U0001 corrected claim matches the cited branch', 'findings': []}
                finding = 'R-U0001-1' if 'failure flag' in statement else 'R-U0001-2'
                report = 'U0001 rewrite the failure claim' if finding.endswith('1') else 'U0001 cursor field is still limited to two cases'
                return {'kind': 'review', 'decision': 'REWORK', 'report': report, 'findings': [
                    {'id': finding, 'target': 'U0001-F7', 'problem': report, 'required_action': 'Restate the claim'}]}
            reply = self.checkpoint(context)
            reply.pop('dispositions', None)
            reply['findings'][0]['id'] = 'U0001-F7'
            defects = (context.get('correction_request') or {}).get('findings') or []
            ident = {item['id'] for item in defects}
            if 'R-U0001-2' in ident:
                sent.append(context)
                reply['findings'][0]['statement'] = mode['statement']
            elif ident:
                reply['findings'][0]['statement'] = only
            else:
                self.assertNotIn('candidate', context)
                reply['findings'][0]['statement'] = original
            if ident:
                quote = reply['findings'][0]['citations'][0]['quote']
                reply['dispositions'] = [{'finding_id': item, 'disposition': 'corrected', 'evidence': quote} for item in ident]
            return reply

        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            first = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(first['status'], 'REWORK_LIMIT')
        self.assertEqual(first['unit'], 'U0001')
        self.assertTrue(first['reason'].startswith('U0001'))
        self.assertIn('two cases', first['reason'])
        self.assertNotIn('U0002', first['unit'])
        passed = copy.deepcopy(self.project['checkpoints']['units']['U0002'])
        calls = self.project['pipeline']['calls']
        unchanged = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(unchanged['unit'], 'U0001')
        self.assertTrue(unchanged['reason'].startswith('U0001'))
        self.assertIn('Unchanged defective content', unchanged['reason'])
        self.assertIn('R-U0001-2', unchanged['reason'])
        self.assertEqual(reviewers['n'], 2)
        self.assertEqual(sent[-1]['unit_id'], 'U0001')
        self.assertEqual(sent[-1]['candidate']['findings'][0]['statement'], only)
        self.assertEqual(sent[-1]['correction_request']['findings'][0]['id'], 'R-U0001-2')
        self.assertEqual(self.project['checkpoints']['units']['U0002'], passed)
        self.assertEqual(self.project['pipeline']['calls'], calls + 1)
        paused = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(paused['status'], 'REWORK_LIMIT')
        self.assertEqual(self.project['pipeline']['calls'], calls + 1)
        self.assertIn('authorize-correction', paused['next_step'])
        self.assertNotIn('reframe assess', paused['next_step'])
        self.assertIn('No assessment job is created', paused['next_step'])
        attempt = self.project['checkpoints']['units']['U0001']['attempt']
        history = list(self.project['checkpoints']['units']['U0001']['correction_history'])
        app.feedback(self.work, self.project, self.args(
            'feedback', '--unit', 'U0001', '--authorize-correction', '--by', 'Fixture owner', '--note', 'Correct the named cases'))
        self.assertEqual(self.project['pipeline']['calls'], calls + 1)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['attempt'], attempt)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['correction_history'], history)
        self.assertEqual(self.project['checkpoints']['units']['U0002'], passed)
        mode['statement'] = fixed
        done = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(done['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.assertEqual(reviewers['n'], 3)
        saved = app.read(self.work / self.project['checkpoints']['units']['U0001']['candidate_path'])
        self.assertIn('blank branch', next(item['statement'] for item in saved['findings'] if item['id'] == 'U0001-F7'))
        self.assertEqual(self.project['checkpoints']['units']['U0002']['candidate_sha256'], passed['candidate_sha256'])

    def test_interrupted_correction_reviews_the_new_candidate_without_replaying_it(self):
        self.plan()
        state = {'raise': True}

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if context.get('unit_id') != 'U0001':
                if 'unit_id' not in context:
                    return {'kind': 'blocked', 'reason': 'Stop before synthesis'}
                if role == 'executor':
                    return self.checkpoint(context)
                return {'kind': 'review', 'decision': 'PASS', 'report': 'Independent fixture review', 'findings': []}
            if role == 'reviewer':
                statement = next(item['statement'] for item in context['candidate']['findings'] if item['id'] == 'U0001-F7')
                if 'blank branch' in statement and state['raise']:
                    state['raise'] = False
                    raise RuntimeError('review interrupted')
                if 'blank branch' in statement:
                    state['seen'] = statement
                    return {'kind': 'review', 'decision': 'PASS', 'report': 'U0001 corrected', 'findings': []}
                finding = 'R-U0001-1' if 'failure flag' in statement else 'R-U0001-2'
                return {'kind': 'review', 'decision': 'REWORK', 'report': 'U0001 still wrong', 'findings': [
                    {'id': finding, 'target': 'U0001-F7', 'problem': 'U0001 still wrong', 'required_action': 'Restate'}]}
            reply = self.checkpoint(context)
            reply.pop('dispositions', None)
            reply['findings'][0]['id'] = 'U0001-F7'
            ident = {item['id'] for item in (context.get('correction_request') or {}).get('findings') or []}
            if 'R-U0001-2' in ident:
                reply['findings'][0]['statement'] = 'The card name field is chosen for two named cases and the blank branch.'
            elif ident:
                reply['findings'][0]['statement'] = 'The card name field is chosen only for two named cases.'
            else:
                reply['findings'][0]['statement'] = 'A non-normal response sets the failure flag.'
            if ident:
                quote = reply['findings'][0]['citations'][0]['quote']
                reply['dispositions'] = [{'finding_id': item, 'disposition': 'corrected', 'evidence': quote} for item in ident]
            return reply

        app.pipeline(self.work, self.project, self.args('validate'), runtime)
        passed = copy.deepcopy(self.project['checkpoints']['units']['U0002'])
        with self.assertRaisesRegex(RuntimeError, 'review interrupted'):
            app.pipeline(self.work, self.project, self.args('resume'), runtime)
        saved = copy.deepcopy(self.project['checkpoints']['units']['U0001'])
        self.assertEqual(saved['role'], 'reviewer')
        self.project = app.read(self.work / 'project.json')
        calls = self.project['pipeline']['calls']
        with patch.object(app, 'legacy_pipeline', return_value={'status': 'READY_FOR_SYNTHESIS'}), \
             patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(state['seen'], 'The card name field is chosen for two named cases and the blank branch.')
        self.assertEqual(self.project['checkpoints']['units']['U0001']['candidate_sha256'], saved['candidate_sha256'])
        self.assertEqual(self.project['pipeline']['calls'], calls + 1)
        self.assertEqual(self.project['checkpoints']['units']['U0002'], passed)

    def test_each_paused_unit_keeps_its_own_report(self):
        self.plan()

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' not in context:
                return {'kind': 'blocked', 'reason': 'Stop before synthesis'}
            if role == 'executor':
                return self.checkpoint(context)
            phrase = 'cursor only cases' if context['unit_id'] == 'U0001' else 'screen inventory'
            return {'kind': 'review', 'decision': 'REWORK', 'report': phrase, 'findings': [
                {'id': 'R-' + context['unit_id'], 'problem': phrase, 'required_action': 'Fix the claim'}]}

        result = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(result['unit'], 'U0001, U0002')
        rows = {row['unit']: row['reason'] for row in result['unresolved']}
        self.assertIn('cursor only cases', rows['U0001'])
        self.assertNotIn('screen inventory', rows['U0001'])
        self.assertIn('screen inventory', rows['U0002'])
        self.assertNotIn('cursor only cases', rows['U0002'])

    def test_resume_command_recovers_a_paused_review_without_assessment(self):
        self.plan()
        self.project['stage'] = 'REWORK_LIMIT'
        self.project['pipeline']['status'] = 'REWORK_LIMIT'
        self.project['active_assessment'] = self.project['selected']
        app.persist(self.work, self.project)
        seen = {}

        def fake(work, project, args, runtime=app.model_call):
            seen['command'] = args.command
            return {'status': 'REWORK_LIMIT', 'unit': 'U0001', 'reason': 'U0001: paused',
                    'next_step': 'reframe feedback --authorize-correction'}

        with patch.object(app, 'pipeline', fake), patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            code = app.main(['--workspace', str(self.work), 'resume'])
        self.assertEqual(seen['command'], 'resume')
        self.assertEqual(code, 2)

    def test_saved_question_impact_location_must_change_and_reach_review(self):
        review = {'kind': 'review', 'decision': 'REWORK',
                  'report': 'U0001 question impact omits the blocked screen decision',
                  'findings': [{
                      'id': 'R-U0001-2', 'severity': 'defect',
                      'location': 'candidate.questions[U0001-Q2].impact',
                      'issue': 'Question impact omits the blocked screen decision.',
                      'required_change': 'State that the unresolved account name blocks the screen decision.',
                  }]}
        defect = cp.review_defects(review)[0]
        self.assertEqual(defect['location'], 'candidate.questions[U0001-Q2].impact')
        self.assertEqual(defect['required_change'], review['findings'][0]['required_change'])
        self.assertEqual(defect['required_action'], defect['required_change'])
        self.assertEqual(defect['problem'], review['findings'][0]['issue'])
        self.assertEqual(defect['severity'], 'defect')
        for location in ('candidate.questions[U0001-Q2].impact + ""',
                         '__import__("os").system("id")',
                         'candidate.questions[U0001-Q2].owner',
                         'candidate["questions"][0].impact'):
            with self.assertRaisesRegex(Blocked, 'Unsupported correction location'):
                cp.correction_location(location)
        saved = {'id': 'U0001-Q2', 'question': 'Who confirms the account name on this screen?',
                 'impact': 'The account name stays unresolved for this screen.',
                 'owner': 'SME', 'blocks': 'Screen decision'}
        self.plan()
        plan = app.read(self.work / self.project['checkpoints']['plan_path'])
        area = self.work / 'preparation'
        units = {}
        for unit in plan['units']:
            ident = unit['id']
            if ident == 'U0001':
                candidate = {'kind': 'checkpoint', 'summary': 'saved', 'findings': [], 'questions': [saved], 'coverage': []}
                candidate_path = area / 'saved-U0001-candidate.json'
                review_path = area / 'saved-U0001-review.json'
                app.write(candidate_path, candidate)
                app.write(review_path, review)
                units[ident] = {'status': 'REWORK_LIMIT', 'attempt': 2, 'role': 'reviewer', 'limit_rereviewed': True,
                                'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                                'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path),
                                'previous_review': review, 'reason': review['report']}
                continue
            candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [], 'questions': [], 'coverage': []}
            passed_review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, passed_review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'PLANNED'
        self.project['pipeline'].update(calls=4, status='REWORK_LIMIT', role='reviewer')
        self.project['stage'] = 'REWORK_LIMIT'
        app.persist(self.work, self.project)
        passed = {ident: copy.deepcopy(record) for ident, record in units.items() if ident != 'U0001'}
        phase = {'n': 0}
        reviewed = []

        def place(reply, impact, owner='SME'):
            reply['questions'] = [item for item in reply['questions'] if item['id'] != 'U0001-Q2']
            reply['questions'].append({**saved, 'impact': impact, 'owner': owner})

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' not in context:
                raise AssertionError('synthesis is outside this correction')
            if role == 'reviewer':
                question = next(item for item in context['candidate']['questions'] if item['id'] == 'U0001-Q2')
                reviewed.append(question)
                return {'kind': 'review', 'decision': 'PASS', 'report': 'U0001 question impact now states the blocked work', 'findings': []}
            request = context['correction_request']['findings'][0]
            self.assertEqual(request['id'], 'R-U0001-2')
            self.assertEqual(request['location'], 'candidate.questions[U0001-Q2].impact')
            self.assertEqual(request['required_change'], review['findings'][0]['required_change'])
            self.assertEqual(context['candidate']['questions'][0]['impact'], saved['impact'])
            reply = self.checkpoint(context)
            reply.pop('dispositions', None)
            phase['n'] += 1
            if phase['n'] == 1:
                place(reply, 'The account name stays unresolved, for this screen.')
            elif phase['n'] == 2:
                place(reply, 'The unresolved account name blocks the screen decision.', owner='Architect')
            else:
                place(reply, 'The unresolved account name blocks the screen decision.')
            reply['dispositions'] = [{'finding_id': 'R-U0001-2', 'disposition': 'corrected', 'evidence': 'screen decision'}]
            return reply

        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            paused = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(paused['status'], 'REWORK_LIMIT')
        self.assertIn('Unchanged defective content', paused['reason'])
        self.assertIn('R-U0001-2', paused['reason'])
        self.assertEqual(reviewed, [])
        self.assertEqual(self.project['pipeline']['calls'], 5)
        self.assertEqual({ident: self.project['checkpoints']['units'][ident] for ident in passed}, passed)
        app.feedback(self.work, self.project, self.args(
            'feedback', '--unit', 'U0001', '--authorize-correction', '--by', 'Fixture owner', '--note', 'Correct the question impact'))
        with patch.object(app, 'legacy_pipeline', return_value={'status': 'READY_FOR_SYNTHESIS'}), \
                patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            done = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(done['status'], 'READY_FOR_SYNTHESIS')
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0]['impact'], 'The unresolved account name blocks the screen decision.')
        self.assertEqual(reviewed[0]['owner'], 'SME')
        self.assertEqual(reviewed[0]['blocks'], 'Screen decision')
        self.assertEqual(reviewed[0]['question'], saved['question'])
        self.assertEqual(phase['n'], 3)
        self.assertEqual(self.project['checkpoints']['units']['U0001']['attempt'], 2)
        self.assertEqual({ident: self.project['checkpoints']['units'][ident] for ident in passed}, passed)

    def test_sanitized_saved_rework_keeps_passed_units_and_corrects_each_paused_unit(self):
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = app.read(plan_path)
        segment = plan['units'][0]['segments'][0]
        paused = {'U0001', 'U0041', 'U0053'}
        claims = {
            'U0001': 'The account field is chosen only for two named cases.',
            'U0041': 'The balance field is chosen only for two named cases.',
            'U0053': 'The card name field is chosen only for two named cases.',
        }
        plan['units'] = [{'id': f'U{number:04d}', 'focus': 'rule.cbl', 'segments': [segment]} for number in range(1, 113)]
        app.write(plan_path, plan)
        self.project['checkpoints']['plan_sha256'] = sha(plan_path)
        units = {}
        area = self.work / 'preparation'
        for unit in plan['units']:
            ident = unit['id']
            if ident in paused:
                candidate = {'kind': 'checkpoint', 'summary': 'sanitized candidate',
                             'findings': [{'id': ident + '-F7', 'statement': claims[ident]}]}
                review = {'kind': 'review', 'decision': 'REWORK',
                          'report': ident + ' claim is still limited to two cases',
                          'findings': [{'id': 'R-' + ident + '-2', 'target': ident + '-F7',
                                        'problem': ident + ' claim is still limited to two cases',
                                        'required_action': 'Restate the claim'}]}
                candidate_path = area / ('saved-' + ident + '-candidate.json')
                review_path = area / ('saved-' + ident + '-review.json')
                app.write(candidate_path, candidate)
                app.write(review_path, review)
                units[ident] = {'status': 'REWORK_LIMIT', 'attempt': 2, 'role': 'reviewer', 'limit_rereviewed': True,
                                'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                                'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path),
                                'previous_review': review, 'reason': review['report']}
            else:
                candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [], 'questions': [], 'coverage': []}
                review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
                candidate_path = area / ('saved-' + ident + '-candidate.json')
                review_path = area / ('saved-' + ident + '-review.json')
                app.write(candidate_path, candidate)
                app.write(review_path, review)
                units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                                'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                                'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'PLANNED'
        self.project['pipeline'].update(calls=287, status='REWORK_LIMIT', role='reviewer', round=1)
        self.project['stage'] = 'REWORK_LIMIT'
        self.project['settings']['max_model_calls'] = None
        label = self.project['selected']
        assessment_sha = self.project['assessments'][label]['sha256']
        approvals = copy.deepcopy(self.project['approvals'])
        source_commit = self.project['source']['commit']
        prompt_hash = self.project['pins']['prompt_hash']
        self.project['framework_commit'] = 'b' * 40
        self.project['pins']['framework_commit'] = 'b' * 40
        directory = Path(self.project['assessments'][label]['directory'])
        run = app.read(directory / 'run.json')
        run['framework_commit'] = 'b' * 40
        app.write(directory / 'run.json', run)
        app.persist(self.work, self.project)
        passed = {ident: copy.deepcopy(record) for ident, record in units.items() if record['status'] == 'PASS'}
        upgraded = app.upgrade_framework(self.work, self.project, argparse.Namespace(note='Record checkpoint correction recovery'))
        self.assertEqual(upgraded['status'], 'FRAMEWORK_UPDATED')
        self.assertEqual(self.project['pipeline']['calls'], 287)
        self.assertEqual(self.project['assessments'][label]['sha256'], assessment_sha)
        self.assertEqual(self.project['approvals'], approvals)
        self.assertEqual(self.project['source']['commit'], source_commit)
        self.assertEqual(self.project['pins']['prompt_hash'], prompt_hash)
        self.assertEqual(self.project['pins']['framework_commit'], 'a' * 40)
        self.assertEqual({ident: self.project['checkpoints']['units'][ident] for ident in passed}, passed)
        seen = []

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' not in context:
                raise AssertionError('synthesis is outside this recovery')
            ident = context['unit_id']
            if role == 'reviewer':
                statement = next(item['statement'] for item in context['candidate']['findings'] if item['id'] == ident + '-F7')
                seen.append((ident, 'reviewer', statement))
                return {'kind': 'review', 'decision': 'PASS', 'report': ident + ' corrected claim matches', 'findings': []}
            request = context['correction_request']['findings']
            self.assertEqual([item['id'] for item in request], ['R-' + ident + '-2'])
            self.assertEqual(context['candidate']['findings'][0]['statement'], claims[ident])
            reply = self.checkpoint(context)
            reply.pop('dispositions', None)
            reply['findings'][0]['id'] = ident + '-F7'
            reply['findings'][0]['statement'] = claims[ident].replace('only for two named cases', 'for two named cases and the blank branch')
            quote = reply['findings'][0]['citations'][0]['quote']
            reply['dispositions'] = [{'finding_id': 'R-' + ident + '-2', 'disposition': 'corrected', 'evidence': quote}]
            seen.append((ident, 'executor', reply['findings'][0]['statement']))
            return reply

        with patch.object(app, 'legacy_pipeline', return_value={'status': 'READY_FOR_SYNTHESIS'}), \
                patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            result = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(result['status'], 'READY_FOR_SYNTHESIS')
        self.assertEqual(self.project['pipeline']['calls'], 293)
        self.assertEqual(self.project['assessments'][label]['sha256'], assessment_sha)
        self.assertEqual(self.project['approvals'], approvals)
        self.assertEqual({ident: self.project['checkpoints']['units'][ident] for ident in passed}, passed)
        for ident in sorted(paused):
            record = self.project['checkpoints']['units'][ident]
            self.assertEqual(record['status'], 'PASS')
            self.assertEqual(record['attempt'], 2)
            self.assertEqual([row for row in seen if row[0] == ident], [
                (ident, 'executor', claims[ident].replace('only for two named cases', 'for two named cases and the blank branch')),
                (ident, 'reviewer', claims[ident].replace('only for two named cases', 'for two named cases and the blank branch'))])

    def test_assessment_only_findings_keep_citation_provenance(self):
        question = {'id': 'U0020-Q1', 'question': 'Who owns the cutoff?',
                    'impact': 'The batch decision stays blocked.', 'owner': 'SME', 'blocks': 'Batch approval'}
        plan = {'units': [
            {'id': 'U0020', 'segments': [{'area': 'assessment', 'path': 'batch.json'}]},
            {'id': 'U0086', 'segments': [{'area': 'source', 'path': 'rule.cbl'}, {'area': 'assessment', 'path': 'screen.json'}]},
            {'id': 'U0018', 'segments': [{'area': 'source', 'path': 'rule.cbl'}]},
            {'id': 'U0001', 'segments': [{'area': 'source', 'path': 'rule.cbl'}]},
        ]}
        capsules = [
            {'unit': 'U0020', 'checkpoint': {'findings': [{
                'id': 'U0020-F2', 'kind': 'business_rule', 'statement': 'The batch report names an inferred cutoff.',
                'status': 'INFERRED', 'citations': [{'area': 'assessment', 'path': 'batch.json', 'start': 33, 'end': 33}]}],
                'questions': [question]}},
            {'unit': 'U0086', 'checkpoint': {'findings': [{
                'id': 'U0086-F8', 'kind': 'business_rule', 'statement': 'The screen report names an inferred field.',
                'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'assessment', 'path': 'screen.json', 'start': 7, 'end': 7}]}],
                'questions': []}},
            {'unit': 'U0018', 'checkpoint': {'findings': [{
                'id': 'U0018-F8', 'kind': 'assessment_claim', 'statement': 'The transaction report restates a source range.',
                'status': 'INFERRED', 'citations': [{'area': 'source', 'path': 'rule.cbl', 'start': 1, 'end': 1}]}],
                'questions': []}},
            {'unit': 'U0001', 'checkpoint': {'findings': [{
                'id': 'U0001-F1', 'kind': 'business_rule', 'statement': 'Reject a zero amount.',
                'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'source', 'path': 'rule.cbl', 'start': 1, 'end': 1}]}],
                'questions': []}},
        ]
        spec = self.spec()
        spec['rules'].append({'id': 'U0020-F2', 'statement': 'invented source claim', 'source_path': 'rule.cbl',
                              'source_anchor': 'lines 1-1', 'evidence_status': 'SOURCE_VERIFIED'})
        spec['rules'].append({'id': 'U0001-F1', 'statement': 'Reject a zero amount.', 'source_path': 'other.cbl',
                              'source_anchor': 'lines 9-9', 'evidence_status': 'UNKNOWN'})
        cp.complete_synthesis(spec, capsules, plan)
        ids = {rule['id'] for rule in spec['rules']}
        self.assertIn('R1', ids)
        self.assertIn('U0001-F1', ids)
        self.assertNotIn('U0020-F2', ids)
        self.assertNotIn('U0086-F8', ids)
        self.assertNotIn('U0018-F8', ids)
        corrected = next(rule for rule in spec['rules'] if rule['id'] == 'U0001-F1')
        self.assertEqual(corrected['source_path'], 'rule.cbl')
        self.assertEqual(corrected['source_anchor'], 'lines 1-1')
        self.assertEqual(corrected['evidence_status'], 'SOURCE_VERIFIED')
        evidence = {record['id']: record for record in spec['assessment_evidence']}
        self.assertEqual(evidence['U0020-F2']['assessment_path'], 'batch.json')
        self.assertEqual(evidence['U0020-F2']['assessment_anchor'], 'lines 33-33')
        self.assertNotIn('source_path', evidence['U0020-F2'])
        self.assertEqual(evidence['U0020-F2']['evidence_status'], 'INFERRED')
        self.assertEqual(evidence['U0086-F8']['evidence_status'], 'INFERRED')
        self.assertNotIn('source_path', evidence['U0086-F8'])
        self.assertEqual(evidence['U0018-F8']['source_path'], 'rule.cbl')
        self.assertNotIn('assessment_path', evidence['U0018-F8'])
        self.assertNotIn('U0020-F2-accept', {case['id'] for case in spec['tests']})
        self.assertEqual(cp.assembly_gaps(capsules, plan), [])
        cp.validate_synthesis(spec, capsules, plan)
        artifacts.validate_spec(spec, self.source)
        missing = [{'unit': 'U0099', 'checkpoint': {'findings': [{
            'id': 'U0099-F1', 'kind': 'business_rule', 'statement': 'No citation was saved.',
            'status': 'INFERRED', 'citations': []}], 'questions': []}}]
        gaps = cp.assembly_gaps(missing, {'units': [{'id': 'U0099', 'segments': []}]})
        self.assertEqual(gaps, [{'id': 'U0099-F1', 'missing': ['source_path', 'assessment_path']}])
        self.assertIn('U0099-F1 missing source_path, assessment_path', cp.assembly_gap_reason(gaps))

    def test_incomplete_assembly_stops_before_a_model_call(self):
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = app.read(plan_path)
        area = self.work / 'preparation'
        units = {}
        for unit in plan['units']:
            ident = unit['id']
            if ident == 'U0001':
                candidate = {'kind': 'checkpoint', 'summary': 'saved', 'coverage': [], 'questions': [],
                             'findings': [{'id': 'U0001-F1', 'kind': 'business_rule', 'statement': 'A cutoff is claimed.',
                                           'status': 'INFERRED', 'citations': []}]}
            else:
                candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [], 'questions': [], 'coverage': []}
            review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['settings']['max_model_calls'] = None
        self.project['pipeline'].update(calls=296, status='BLOCKED', role='executor',
                                         reason='Three invalid synthesis candidates; inspect validation feedback')
        self.project['stage'] = 'BLOCKED'
        app.persist(self.work, self.project)
        passed = copy.deepcopy(units)
        approvals = copy.deepcopy(self.project['approvals'])
        label = self.project['selected']
        assessment_sha = self.project['assessments'][label]['sha256']

        def runtime(*_args):
            raise AssertionError('model call')

        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            result = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['calls'], 296)
        self.assertEqual(result['gaps'], [{'id': 'U0001-F1', 'missing': ['source_path', 'assessment_path']}])
        self.assertIn('U0001-F1 missing source_path, assessment_path', result['reason'])
        self.assertEqual(self.project['pipeline']['calls'], 296)
        self.assertEqual(self.project['checkpoints']['units'], passed)
        self.assertEqual(self.project['approvals'], approvals)
        self.assertEqual(self.project['assessments'][label]['sha256'], assessment_sha)

    def test_saved_valid_assembly_reaches_review_without_another_executor_call(self):
        self.plan()
        plan = app.read(self.work / self.project['checkpoints']['plan_path'])
        area = self.work / 'preparation'
        units = {}
        for unit in plan['units']:
            ident = unit['id']
            candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [], 'questions': [], 'coverage': []}
            review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['settings']['max_model_calls'] = None
        self.project['pipeline'].update(calls=296, status='BLOCKED', role='executor', round=1,
                                         reason='Three invalid synthesis candidates; inspect validation feedback')
        self.project['stage'] = 'BLOCKED'
        app.write(area / 'reply-296.json', {'kind': 'candidate', 'specification': self.spec()})
        app.persist(self.work, self.project)
        passed = copy.deepcopy(units)
        approvals = copy.deepcopy(self.project['approvals'])
        roles = []

        def runtime(provider, run, directory, role, message):
            roles.append(role)
            context = json.loads(message)
            self.assertIn('assessment_evidence', context['candidate'])
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Assembled candidate keeps assessment evidence apart from source rules', 'findings': []}

        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            result = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(result['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.assertEqual(roles, ['reviewer'])
        self.assertEqual(self.project['pipeline']['calls'], 297)
        self.assertEqual(self.project['checkpoints']['units'], passed)
        self.assertEqual(self.project['approvals'], approvals)
        saved = app.read(self.work / self.project['specification']['path'])
        self.assertIn('assessment_evidence', saved)
        self.assertTrue(saved['rules'])

    def test_failed_synthesis_feedback_uses_the_assembled_candidate(self):
        self.plan()
        seen = []
        held = {}

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if 'unit_id' in context or role != 'executor':
                return self.runtime(provider, run, directory, role, message)
            seen.append(context)
            reply = self.runtime(provider, run, directory, role, message)
            if len(seen) == 1:
                reply['specification']['scope'] = ' '
                return reply
            rejected = context['candidate_correction']['rejected_specification']
            held['rejected'] = rejected
            self.assertTrue(rejected['rules'])
            self.assertIn('assessment_evidence', rejected)
            self.assertTrue(any(record.get('kind') == 'assessment_claim' for record in rejected['assessment_evidence']))
            self.assertNotIn('reframe assess', context['candidate_correction']['instruction'])
            return reply

        result = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(result['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.assertGreaterEqual(len(seen), 2)
        evidence = {record['id']: record for record in held['rejected']['assessment_evidence']}
        self.assertTrue(evidence)
        for record in evidence.values():
            if not record.get('source_path'):
                self.assertNotEqual(record.get('evidence_status'), 'SOURCE_VERIFIED')
        report = (self.work / self.project['preparation_report']['path']).read_text()
        self.assertIn('Assessment evidence', report)
        self.assertIn('not source-backed rules', report)

    def test_saved_blocked_synthesis_reviews_the_assembled_candidate(self):
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = app.read(plan_path)
        segment = plan['units'][0]['segments'][0]
        source_path = segment['path']
        assessment_only = {'U0020', 'U0042', 'U0044', 'U0051'}
        plan['units'] = []
        for number in range(1, 113):
            ident = f'U{number:04d}'
            if ident in assessment_only:
                segments = [{'area': 'assessment', 'path': 'batch.json', 'start': 1, 'end': 1}]
            else:
                segments = [segment]
            plan['units'].append({'id': ident, 'focus': source_path, 'segments': segments})
        app.write(plan_path, plan)
        self.project['checkpoints']['plan_sha256'] = sha(plan_path)
        area = self.work / 'preparation'
        units = {}
        question = {'id': 'U0020-Q1', 'question': 'Who owns the cutoff?', 'impact': 'The batch decision stays blocked.',
                    'owner': 'SME', 'blocks': 'Batch approval'}
        for unit in plan['units']:
            ident = unit['id']
            findings, questions = [], []
            if ident in assessment_only:
                findings.append({'id': ident + '-F2', 'kind': 'business_rule', 'statement': 'The report names an inferred cutoff.',
                                 'status': 'INFERRED', 'citations': [{'area': 'assessment', 'path': 'batch.json', 'start': 4, 'end': 4}]})
                if ident == 'U0020':
                    questions.append(question)
            elif ident == 'U0086':
                findings.append({'id': 'U0086-F8', 'kind': 'business_rule', 'statement': 'The screen report names an inferred field.',
                                 'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'assessment', 'path': 'screen.json', 'start': 7, 'end': 7}]})
            elif ident == 'U0018':
                findings.append({'id': 'U0018-F8', 'kind': 'assessment_claim', 'statement': 'The report restates a source range.',
                                 'status': 'INFERRED', 'citations': [{'area': 'source', 'path': source_path, 'start': 1, 'end': 1}]})
            else:
                findings.append({'id': ident + '-F1', 'kind': 'business_rule', 'statement': 'Reject a zero amount.',
                                 'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'source', 'path': source_path, 'start': 1, 'end': 1}]})
            candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': findings, 'questions': questions, 'coverage': []}
            review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        self.assertEqual(len(units), 112)
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['settings']['max_model_calls'] = None
        self.project['pipeline'].update(calls=296, status='BLOCKED', role='executor', round=1, invalid_candidates=3,
                                         reason='Three invalid synthesis candidates; inspect validation feedback')
        self.project['stage'] = 'BLOCKED'
        app.write(area / 'reply-296.json', {'kind': 'candidate', 'specification': self.spec()})
        app.persist(self.work, self.project)
        passed = copy.deepcopy(units)
        approvals = copy.deepcopy(self.project['approvals'])
        label = self.project['selected']
        assessment_sha = self.project['assessments'][label]['sha256']
        seen = {}

        def runtime(provider, run, directory, role, message):
            self.assertEqual(role, 'reviewer')
            self.assertEqual(self.project['pipeline']['calls'], 297)
            context = json.loads(message)
            seen['candidate'] = context['candidate']
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Assembled candidate keeps assessment evidence apart from source rules', 'findings': []}

        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            result = app.pipeline(self.work, self.project, self.args('resume'), runtime)
        self.assertEqual(result['status'], 'AWAITING_VALIDATION_APPROVAL')
        self.assertEqual(self.project['pipeline']['calls'], 297)
        self.assertEqual(self.project['checkpoints']['units'], passed)
        self.assertEqual(self.project['approvals'], approvals)
        self.assertEqual(self.project['assessments'][label]['sha256'], assessment_sha)
        candidate = seen['candidate']
        saved = app.read(self.work / self.project['specification']['path'])
        self.assertEqual(saved, candidate)
        evidence = {record['id']: record for record in candidate['assessment_evidence']}
        self.assertEqual(evidence['U0020-F2']['assessment_path'], 'batch.json')
        self.assertNotIn('source_path', evidence['U0020-F2'])
        self.assertEqual(evidence['U0020-F2']['evidence_status'], 'INFERRED')
        self.assertEqual(evidence['U0086-F8']['evidence_status'], 'INFERRED')
        self.assertNotIn('source_path', evidence['U0086-F8'])
        self.assertEqual(evidence['U0018-F8']['source_path'], source_path)
        self.assertEqual(evidence['U0018-F8']['evidence_status'], 'INFERRED')
        coverage = {row['id']: row for row in candidate['coverage']}
        self.assertEqual(coverage['U0020-F2']['disposition'], 'evidence_only')
        self.assertEqual(coverage['U0020-F2']['assessment_path'], 'batch.json')
        self.assertEqual(coverage['U0002-F1']['disposition'], 'gap')
        self.assertIn('section', coverage['U0002-F1']['gaps'])
        self.assertIn('work_item', coverage['U0002-F1']['gaps'])
        self.assertNotIn('U0020-F2', {case['rule_id'] for case in candidate['tests']})
        self.assertFalse(any('U0020-F2' in (item.get('rule_ids') or []) for item in candidate['work_items']))
        report = (self.work / self.project['preparation_report']['path']).read_text()
        self.assertIn('batch.json', report)
        self.assertIn('evidence_only', report)
        self.assertIn('SOURCE_VERIFIED appears only when a source citation supports it', report)

    def test_assessment_evidence_survives_review_html_traceability_and_package(self):
        seen = {}

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if role == 'reviewer' and 'unit_id' not in context:
                seen['candidate'] = context['candidate']
            return self.runtime(provider, run, directory, role, message)

        result = app.pipeline(self.work, self.project, self.args('validate'), runtime)
        self.assertEqual(result['status'], 'AWAITING_VALIDATION_APPROVAL')
        saved = app.read(self.work / self.project['specification']['path'])
        self.assertEqual(saved['assessment_evidence'], seen['candidate']['assessment_evidence'])
        self.assertEqual(saved['coverage'], seen['candidate']['coverage'])
        self.assertTrue(saved['assessment_evidence'])
        for record in saved['assessment_evidence']:
            self.assertTrue(record.get('assessment_path') or record.get('source_path'))
            row = next(item for item in saved['coverage'] if item['id'] == record['id'])
            self.assertEqual(row['disposition'], 'evidence_only')
            self.assertEqual(row.get('assessment_path') or '', record.get('assessment_path') or '')
            self.assertEqual(row.get('source_path') or '', record.get('source_path') or '')
            if not record.get('source_path'):
                self.assertNotEqual(record['evidence_status'], 'SOURCE_VERIFIED')
        report = (self.work / self.project['preparation_report']['path']).read_text()
        self.assertIn(saved['assessment_evidence'][0]['id'], report)
        for question in saved['questions']:
            app.decision(self.work, self.project, self.args(
                'decide', '--question', question['id'], '--by', 'SME', '--answer', 'Fixture answer', '--disposition', 'resolved'))
        app.approve(self.work, self.project, self.args('approve', 'validation', '--by', 'Fixture owner', '--note', 'Fixture review'))
        screens = fixtures.PreparationTests.screens(self)
        rule_id = saved['rules'][0]['id']
        for screen in screens['screens']:
            screen['rule_ids'] = [rule_id]
        for api in screens['apis']:
            api['rule_ids'] = [rule_id]
        app.review_site(self.work, self.project, self.args('review-site'), lambda *args: screens)
        app.approve(self.work, self.project, self.args('approve', 'specifications', '--by', 'Fixture owner', '--note', 'Fixture review'))
        packaged = app.package(self.work, self.project)
        with zipfile.ZipFile(packaged['archive']) as archive:
            spec = json.loads(archive.read('specification.json'))
            trace = json.loads(archive.read('traceability.json'))
            evidence = json.loads(archive.read('assessment-evidence.json'))
            html = archive.read('sme-architect-review.html').decode()
        self.assertEqual(evidence, saved['assessment_evidence'])
        self.assertEqual(spec['assessment_evidence'], saved['assessment_evidence'])
        self.assertEqual(trace['assessment_evidence'], saved['assessment_evidence'])
        self.assertEqual(trace['coverage'], saved['coverage'])
        self.assertIn('evidence_only', html)
        self.assertIn(saved['assessment_evidence'][0].get('assessment_path') or saved['assessment_evidence'][0]['source_path'], html)

    def test_explicit_mappings_are_kept_and_prose_does_not_fill_gaps(self):
        question = {'id': 'U0075-Q1', 'question': 'Which customer key length is authoritative?',
                    'impact': 'The record layout stays unresolved.', 'owner': 'SME', 'blocks': 'Customer data layout'}
        data = 'One file uses a 9 byte customer key and another uses 10 bytes.'
        spec = self.spec()
        spec['rules'].append({'id': 'R-POST-01', 'statement': 'Post the named account.', 'source_path': 'rule.cbl',
                              'source_anchor': 'lines 1-1', 'evidence_status': 'SOURCE_VERIFIED'})
        spec['sections']['data']['specification'] = data
        spec['sections']['delivery']['specification'] = 'Delivery prose assigns R-POST-01 to W1.'
        spec['sections']['delivery']['rule_ids'] = ['R1']
        spec['work_items'][0]['acceptance'] = 'W1 implements R-POST-01'
        spec['work_items'][0]['rule_ids'] = ['R1', 'U0020-F2']
        spec['questions'] = [question]
        plan = {'units': [{'id': 'U0001', 'segments': [{'area': 'source', 'path': 'rule.cbl'}]},
                          {'id': 'U0020', 'segments': [{'area': 'assessment', 'path': 'batch.json'}]}]}
        capsules = [
            {'unit': 'U0001', 'checkpoint': {'findings': [{
                'id': 'U0001-F1', 'kind': 'business_rule', 'statement': 'Reject a zero amount.',
                'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'source', 'path': 'rule.cbl', 'start': 1, 'end': 1}]}],
                'questions': []}},
            {'unit': 'U0020', 'checkpoint': {'findings': [{
                'id': 'U0020-F2', 'kind': 'business_rule', 'statement': 'The report names an inferred cutoff.',
                'status': 'INFERRED', 'citations': [{'area': 'assessment', 'path': 'batch.json', 'start': 4, 'end': 4}]}],
                'questions': []}},
        ]
        cp.complete_synthesis(spec, capsules, plan)
        self.assertEqual(spec['sections']['data']['specification'], data)
        self.assertEqual(spec['questions'], [question])
        self.assertEqual(spec['work_items'][0]['rule_ids'], ['R1'])
        self.assertEqual(spec['sections']['delivery']['rule_ids'], ['R1'])
        self.assertNotIn('R-POST-01', spec['work_items'][0]['rule_ids'])
        self.assertEqual(spec['coverage'], cp.coverage_dispositions(spec))
        rows = {row['id']: row for row in spec['coverage']}
        self.assertEqual(rows['R1']['work_items'], ['W1'])
        self.assertNotIn('work_item', rows['R1']['gaps'])
        self.assertIn('work_item', rows['R-POST-01']['gaps'])
        self.assertIn('section', rows['R-POST-01']['gaps'])
        self.assertEqual(rows['U0020-F2']['disposition'], 'evidence_only')
        self.assertNotIn('U0020-F2', spec['work_items'][0]['rule_ids'])

    def test_saved_call_299_review_corrects_synthesis_and_status_shows_the_reason(self):
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = app.read(plan_path)
        segment = plan['units'][0]['segments'][0]
        plan['units'] = [{'id': f'U{number:04d}', 'focus': segment['path'], 'segments': [segment]} for number in range(1, 113)]
        app.write(plan_path, plan)
        self.project['checkpoints']['plan_sha256'] = sha(plan_path)
        area = self.work / 'preparation'
        units = {}
        for unit in plan['units']:
            ident = unit['id']
            candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [{
                'id': ident + '-F1', 'kind': 'business_rule', 'statement': 'Reject a zero amount.',
                'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'source', 'path': segment['path'], 'start': 1, 'end': 1}]}],
                'questions': [], 'coverage': []}
            review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        question = {'id': 'U0075-Q1', 'question': 'Which customer key length is authoritative?',
                    'impact': 'The record layout stays unresolved.', 'owner': 'SME', 'blocks': 'Customer data layout'}
        data = 'One file uses a 9 byte customer key and another uses 10 bytes.'
        spec = self.spec()
        spec['rules'].append({'id': 'R-POST-01', 'statement': 'Post the named account.', 'source_path': segment['path'],
                              'source_anchor': 'lines 1-1', 'evidence_status': 'SOURCE_VERIFIED'})
        spec['sections']['data']['specification'] = data
        spec['sections']['delivery']['specification'] = 'Delivery prose assigns R-POST-01 to W1.'
        spec['sections']['delivery']['rule_ids'] = ['R1']
        spec['work_items'][0]['acceptance'] = 'W1 implements R-POST-01'
        spec['work_items'][0]['rule_ids'] = ['R1']
        spec['questions'] = [question]
        reply_298 = {'kind': 'candidate', 'specification': spec}
        reply_299 = {'kind': 'review', 'decision': 'REWORK',
                     'report': 'Coverage still disagrees with the delivery prose, and the key length conflict stays open.',
                     'findings': [
                         {'id': 'RF1-R', 'area': 'coverage', 'severity': 'major',
                          'statement': 'Work item prose names a rule that rule_ids omits.',
                          'required_action': 'Put the mapping in rule_ids or keep the gap and remove the prose claim.'},
                         {'id': 'RF5', 'area': 'data', 'severity': 'minor',
                          'statement': 'The data section must keep the key-length conflict open.',
                          'required_action': 'Do not choose an authoritative layout.'}]}
        app.write(area / 'reply-298.json', reply_298)
        app.write(area / 'reply-299.json', reply_299)
        app.write(area / 'checkpoint-review-2.json', reply_299)
        app.write(area / 'checkpoint-candidate-2.json', spec)
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['settings']['max_model_calls'] = None
        self.project['active_assessment'] = self.project['selected']
        self.project['pipeline'].update(calls=299, status='REWORK_LIMIT', role='reviewer', round=2,
                                         candidate_sha256=sha(area / 'checkpoint-candidate-2.json'),
                                         previous_review={'kind': 'review', 'decision': 'REWORK', 'report': 'older review',
                                                          'findings': [{'id': 'RF1', 'statement': 'older finding', 'required_action': 'older'}]})
        self.project['pipeline'].pop('reason', None)
        self.project['stage'] = 'REWORK_LIMIT'
        app.persist(self.work, self.project)
        passed = copy.deepcopy(units)
        approvals = copy.deepcopy(self.project['approvals'])
        seen = {}

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if role == 'executor':
                seen['correction'] = context['candidate_correction']
                return self.runtime(provider, run, directory, role, message)
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Corrected candidate keeps the open key question', 'findings': []}

        reports = []
        with patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            status = app.main(['--workspace', str(self.work), 'status'], reporter=reports.append)
        self.assertEqual(status, 2)
        self.assertIn('RF1-R', reports[0]['reason'])
        self.assertIn('RF5', reports[0]['reason'])
        self.assertNotIn('RF1.', reports[0]['reason'])
        self.assertIn('reframe resume', reports[0]['next_step'])
        self.assertNotIn('feedback --unit', reports[0]['next_step'])
        self.assertIsNone(app.read(self.work / 'project.json')['pipeline'].get('reason'))
        with patch.object(app, 'model_call', runtime), patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            code = app.main(['--workspace', str(self.work), 'resume'], reporter=reports.append)
        self.assertEqual(code, 0)
        correction = seen['correction']
        self.assertEqual([item['id'] for item in correction['review_findings']], ['RF1-R', 'RF5'])
        self.assertIn('rule_ids', correction['instruction'])
        self.assertIn('authoritative layout', correction['instruction'])
        corrected = correction['rejected_specification']
        self.assertIn(question, corrected['questions'])
        self.assertEqual(corrected['sections']['data']['specification'], data)
        self.assertEqual(corrected['work_items'][0]['rule_ids'], ['R1'])
        self.assertNotIn('R-POST-01', corrected['work_items'][0]['rule_ids'])
        self.assertEqual(corrected['coverage'], cp.coverage_dispositions(corrected))
        rows = {row['id']: row for row in corrected['coverage']}
        self.assertIn('work_item', rows['R-POST-01']['gaps'])
        saved = app.read(self.work / 'project.json')
        self.assertEqual(saved['checkpoints']['units'], passed)
        self.assertEqual(saved['approvals'], approvals)
        self.assertEqual(saved['pipeline']['calls'], 301)

    def test_unrelated_blocked_resume_does_not_start_synthesis(self):
        self.plan()
        self.project['stage'] = 'BLOCKED'
        self.project['pipeline'] = {'calls': 4, 'status': 'BLOCKED', 'role': 'executor', 'round': 1,
                                     'reason': 'Evidence missing', 'contexts': {}, 'reads': {}}
        self.project['active_assessment'] = self.project['selected']
        app.persist(self.work, self.project)

        def runtime(*_args):
            raise AssertionError('model call')

        reports = []
        with patch.object(app, 'model_call', runtime), patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            code = app.main(['--workspace', str(self.work), 'resume'], reporter=reports.append)
        self.assertEqual(code, 2)
        self.assertEqual(reports[0]['status'], 'BLOCKED')
        self.assertNotIn('targeted synthesis correction', reports[0]['next_step'])
        self.assertEqual(app.read(self.work / 'project.json')['pipeline']['calls'], 4)
        self.assertEqual(app.read(self.work / 'project.json')['stage'], 'BLOCKED')

    def test_authorized_prompt_migration_repairs_a_partial_upgrade_and_resumes_synthesis(self):
        self.plan()
        plan_path = self.work / self.project['checkpoints']['plan_path']
        plan = app.read(plan_path)
        segment = plan['units'][0]['segments'][0]
        plan['units'] = [{'id': f'U{number:04d}', 'focus': segment['path'], 'segments': [segment]} for number in range(1, 113)]
        app.write(plan_path, plan)
        self.project['checkpoints']['plan_sha256'] = sha(plan_path)
        area = self.work / 'preparation'
        units = {}
        for unit in plan['units']:
            ident = unit['id']
            candidate = {'kind': 'checkpoint', 'summary': 'passed', 'findings': [{
                'id': ident + '-F1', 'kind': 'business_rule', 'statement': 'Reject a zero amount.',
                'status': 'SOURCE_VERIFIED', 'citations': [{'area': 'source', 'path': segment['path'], 'start': 1, 'end': 1}]}],
                'questions': [], 'coverage': []}
            review = {'kind': 'review', 'decision': 'PASS', 'report': 'passed', 'findings': []}
            candidate_path = area / ('saved-' + ident + '-candidate.json')
            review_path = area / ('saved-' + ident + '-review.json')
            app.write(candidate_path, candidate)
            app.write(review_path, review)
            units[ident] = {'status': 'PASS', 'attempt': 1, 'role': 'reviewer',
                            'candidate_path': str(candidate_path.relative_to(self.work)), 'candidate_sha256': sha(candidate_path),
                            'review_path': str(review_path.relative_to(self.work)), 'review_sha256': sha(review_path)}
        question = {'id': 'U0075-Q1', 'question': 'Which customer key length is authoritative?',
                    'impact': 'The record layout stays unresolved.', 'owner': 'SME', 'blocks': 'Customer data layout'}
        spec = self.spec()
        spec['questions'] = [question]
        reply_298 = {'kind': 'candidate', 'specification': spec}
        reply_299 = {'kind': 'review', 'decision': 'REWORK', 'report': 'Coverage mappings are still prose.',
                     'findings': [{'id': 'RF1-R', 'statement': 'Put mappings in rule_ids.', 'required_action': 'Use rule_ids or keep the gap.'}]}
        app.write(area / 'reply-298.json', reply_298)
        app.write(area / 'reply-299.json', reply_299)
        app.write(area / 'checkpoint-review-2.json', reply_299)
        app.write(area / 'checkpoint-candidate-2.json', spec)
        label = self.project['selected']
        directory = Path(self.project['assessments'][label]['directory'])
        run = app.read(directory / 'run.json')
        run['framework_commit'] = 'a' * 40
        app.write(directory / 'run.json', run)
        self.project['checkpoints']['units'] = units
        self.project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
        self.project['settings']['max_model_calls'] = None
        self.project['active_assessment'] = label
        self.project['framework_commit'] = 'a' * 40
        self.project.setdefault('events', []).append({
            'event': 'framework_upgraded', 'from': 'b' * 40, 'to': 'a' * 40,
            'reason': 'Record revision 47b05a9 without the prompt hash'})
        self.project['pipeline'].update(calls=299, status='REWORK_LIMIT', role='reviewer', round=2,
                                         candidate_sha256=sha(area / 'checkpoint-candidate-2.json'))
        self.project['stage'] = 'REWORK_LIMIT'
        old_hash = 'd' * 64
        new_hash = 'c' * 64
        models = (self.project['pins']['executor_model'], self.project['pins']['reviewer_model'])
        self.project['pins'].update(framework_commit='a' * 40, prompt_hash=old_hash,
                                     aws_job_id='job-kept', assessment_label=label)
        app.persist(self.work, self.project)
        before = (self.work / 'project.json').read_bytes()
        run_before = (directory / 'run.json').read_bytes()
        passed = copy.deepcopy(units)
        approvals = copy.deepcopy(self.project['approvals'])
        source_commit = self.project['source']['commit']
        assessment_sha = self.project['assessments'][label]['sha256']
        reports = []

        def refuse_model(*_args):
            raise AssertionError('model call')

        with patch.object(app, 'preparation_protocol_hash', return_value=new_hash), \
                patch.object(app, 'model_call', refuse_model), \
                patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            status = app.main(['--workspace', str(self.work), 'status'], reporter=reports.append)
            self.assertEqual(status, 2)
            self.assertIn('--adopt-prompts', reports[-1]['next_step'])
            refused_resume = app.main(['--workspace', str(self.work), 'resume'], reporter=reports.append)
            self.assertEqual(refused_resume, 2)
            self.assertIn('prompt_hash', reports[-1]['reason'])
            self.assertIn('--adopt-prompts', reports[-1]['hint'])
            self.assertEqual((self.work / 'project.json').read_bytes(), before)
            refused_upgrade = app.main(['--workspace', str(self.work), 'upgrade-framework', '--note', 'Adopt synthesis prompts'],
                                       reporter=reports.append)
            self.assertEqual(refused_upgrade, 2)
            self.assertIn('was not recorded', reports[-1]['reason'])
            self.assertIn('--adopt-prompts', reports[-1]['hint'])
            self.assertEqual((self.work / 'project.json').read_bytes(), before)
            self.assertEqual((directory / 'run.json').read_bytes(), run_before)
            adopted = app.main(['--workspace', str(self.work), 'upgrade-framework', '--note', 'Adopt synthesis prompts', '--adopt-prompts'],
                               reporter=reports.append)
        self.assertEqual(adopted, 0)
        self.assertEqual(reports[-1]['prompt_migration'], 'adopted')
        self.assertEqual(reports[-1]['phase'], 'synthesis')
        saved = app.read(self.work / 'project.json')
        self.assertEqual(saved['pins']['prompt_hash'], new_hash)
        self.assertEqual(saved['pins']['framework_commit'], 'a' * 40)
        self.assertEqual((saved['pins']['executor_model'], saved['pins']['reviewer_model']), models)
        self.assertEqual(saved['pins']['aws_job_id'], 'job-kept')
        self.assertEqual(saved['pipeline']['calls'], 299)
        self.assertEqual(saved['checkpoints']['units'], passed)
        self.assertEqual(saved['approvals'], approvals)
        self.assertEqual(saved['source']['commit'], source_commit)
        self.assertEqual(saved['assessments'][label]['sha256'], assessment_sha)
        self.assertEqual((directory / 'run.json').read_bytes(), run_before)
        migration = saved['events'][-1]
        self.assertEqual(migration['event'], 'prompt_migrated')
        self.assertEqual(migration['phase'], 'synthesis')
        self.assertEqual(migration['rationale'], 'Adopt synthesis prompts')
        self.assertEqual(migration['from_prompt_hash'], old_hash)
        self.assertEqual(migration['to_prompt_hash'], new_hash)
        self.assertEqual(migration['from_framework_commit'], 'b' * 40)
        self.assertEqual(migration['to_framework_commit'], 'a' * 40)
        self.assertEqual(migration['calls_completed'], 299)
        self.assertEqual(migration['passed_units'], 112)
        history = saved['prompt_history'][-1]
        self.assertEqual(history['prompt_hash'], old_hash)
        self.assertEqual(history['through_call'], 299)
        self.assertEqual(history['phase'], 'synthesis')
        self.assertEqual(set(history['passed_units']), set(passed))
        saved['settings']['reviewer_model'] = 'other-model'
        app.write(self.work / 'project.json', saved)
        with patch.object(app, 'preparation_protocol_hash', return_value=new_hash), \
                patch.object(app, 'model_call', refuse_model), \
                patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            blocked_model = app.main(['--workspace', str(self.work), 'resume'], reporter=reports.append)
        self.assertEqual(blocked_model, 2)
        self.assertIn('reviewer_model', reports[-1]['reason'])
        self.assertNotIn('prompt_hash', reports[-1]['reason'])
        self.assertEqual(app.read(self.work / 'project.json')['pipeline']['calls'], 299)
        saved = app.read(self.work / 'project.json')
        saved['settings']['reviewer_model'] = models[1]
        app.write(self.work / 'project.json', saved)
        seen = {}

        def runtime(provider, run, directory, role, message):
            context = json.loads(message)
            if role == 'executor':
                seen['findings'] = [item['id'] for item in context['candidate_correction']['review_findings']]
                return self.runtime(provider, run, directory, role, message)
            return {'kind': 'review', 'decision': 'PASS', 'report': 'Corrected candidate keeps the saved units', 'findings': []}

        with patch.object(app, 'preparation_protocol_hash', return_value=new_hash), \
                patch.object(app, 'model_call', runtime), \
                patch.object(app, 'assessment', side_effect=AssertionError('assessment job')):
            resumed = app.main(['--workspace', str(self.work), 'resume'], reporter=reports.append)
        self.assertEqual(resumed, 0)
        self.assertEqual(seen['findings'], ['RF1-R'])
        recovered = app.read(self.work / 'project.json')
        self.assertEqual(recovered['pipeline']['calls'], 301)
        self.assertEqual(recovered['checkpoints']['units'], passed)
        self.assertEqual(recovered['approvals'], approvals)
        self.assertEqual(recovered['assessments'][label]['sha256'], assessment_sha)
        self.assertEqual(recovered['pins']['aws_job_id'], 'job-kept')
        self.assertEqual(recovered['prompt_history'][-1]['prompt_hash'], old_hash)
