import argparse
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_reframe as fixtures
import reframe as app
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
