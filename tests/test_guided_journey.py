"""Scripted guided journeys. External AWS and model calls stay mocked."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
os.environ.setdefault('REFRAME_CONFIG_HOME', tempfile.mkdtemp(prefix='reframe-journey-config-'))

import kiro_workflow as kw
import reframe
import reframe_optional as optional
import reframe_projects as projects
import reframe_terminal
from pilot import Blocked
from reframe_flow import offer_known_repository, run


SCREENS = {
    'journeys': [{'id': 'pay', 'name': 'Pay', 'summary': 'Check the amount.', 'screen_ids': ['s1']}],
    'screens': [{'id': 's1', 'title': 'Amount', 'purpose': 'Check the amount.', 'rule_ids': [],
                 'fields': ['Amount'], 'actions': ['Save'], 'states': ['Normal'],
                 'uncertainties': 'None recorded', 'api_ids': []}],
    'apis': [],
}


def project_record(commit='a' * 40, skip=False):
    record = {
        'stage': 'PACKAGED', 'scope': 'Fixture scope', 'aws': {},
        'site': {'directory': 'previews/business-1'},
        'source': {'repository': 'https://example.test/demo.git', 'commit': commit},
        'source_paths': ['cpy/AMOUNT.cpy'],
        'decisions': {}, 'approvals': {'assessment': {'by': 'Ari'}},
        'settings': {'max_model_calls': 16, 'max_model_dollars': 1, 'model_dollars_spent': 0,
                     'model_rates': {'executor': 0.02}},
        'pipeline': {'calls': 4}, 'ui_calls': 1,
    }
    if skip:
        record['verification_skip'] = {'owner': 'Ari', 'reason': 'Accepted the sealed findings', 'assessment_sha256': 'b' * 64}
    else:
        record['approvals']['validation'] = {'by': 'Noor'}
    return record


class GuidedJourneyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.config = Path(tempfile.mkdtemp())
        os.environ['REFRAME_CONFIG_HOME'] = str(self.config)
        self.site = self.root / 'previews' / 'business-1'
        self.site.mkdir(parents=True)
        (self.site / 'screens.json').write_text(json.dumps(SCREENS))
        (self.site / 'index.html').write_text('existing')
        self.project = project_record()
        (self.root / 'project.json').write_text(json.dumps(self.project))

    def test_imports_resolve_to_this_worktree(self):
        root = Path(__file__).resolve().parents[1]
        for module in (reframe, reframe_terminal, optional, projects):
            self.assertTrue(str(Path(module.__file__).resolve()).startswith(str(root / 'scripts')))

    def test_menu_one_writes_one_canonical_reimagine_design(self):
        work = self.root
        (work / 'project.json').write_text(json.dumps(self.project))
        from reframe_setup import select_workspace
        select_workspace(work)
        transcript = []

        answers = ['1', 'continue', 'reimagine', 'v1', 'java', 'none', '0']

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def prompt(self, message, **kwargs):
                text = message if isinstance(message, str) else ''.join(
                    part[1] if isinstance(part, tuple) else str(part) for part in message)
                transcript.append(text.strip())
                answer = answers.pop(0)
                transcript.append(answer)
                return answer

        stdout = io.StringIO()
        stdout.isatty = lambda: True
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys, 'stdout', stdout), \
                patch('reframe_terminal.PromptSession', Session), \
                patch('reframe_models.call', side_effect=AssertionError('model call')), \
                patch('reframe_projects._live_aws', side_effect=AssertionError('aws call')):
            code = reframe.main([])
        self.assertEqual(code, 0)
        design = json.loads((work / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['mode'], 'reimagine')
        self.assertEqual(design['stack'], 'java')
        self.assertEqual(design['provenance']['source_commit'], 'a' * 40)
        self.assertEqual(design['provenance']['verification']['status'], 'INDEPENDENTLY_REVIEWED')
        proposal = design['proposals'][0]
        self.assertEqual(proposal['status'], 'proposed_unapproved')
        self.assertIn('s1', proposal['evidence'])
        self.assertIn('Amount', proposal['impacts'])
        self.assertTrue(proposal['rationale'])
        self.assertTrue(proposal['acceptance_criteria'])
        archive = work / 'packages' / 'v1' / 'reimagine' / 'ai-dlc-package.zip'
        with zipfile.ZipFile(archive) as zipped:
            packaged = json.loads(zipped.read('design.json'))
            manifest = json.loads(zipped.read('manifest.json'))
            inventory = json.loads(zipped.read('field-inventory.json'))
            aidlc = json.loads(zipped.read('ai-dlc.json'))
        self.assertEqual(packaged['stack'], 'java')
        self.assertEqual(packaged['proposals'][0]['evidence'], proposal['evidence'])
        self.assertEqual(manifest['package_status'], 'DRAFT')
        self.assertEqual(aidlc['stack'], 'java')
        self.assertIn('cpy/AMOUNT.cpy', json.dumps(inventory))
        self.assertIn('1', transcript)
        self.assertIn('reimagine', transcript)
        transcript_path = Path(__file__).resolve().parents[1] / 'guided-session-transcript.txt'
        transcript_path.write_text('\n'.join(transcript) + '\n\n' + stdout.getvalue())

    def test_modes_review_skip_and_context_share_one_design(self):
        for mode in ('functional-parity', 'reimagine'):
            for skip in (False, True):
                for present in (False, True):
                    with self.subTest(mode=mode, skip=skip, present=present):
                        root = Path(tempfile.mkdtemp())
                        site = root / 'previews' / 'business-1'
                        site.mkdir(parents=True)
                        (site / 'screens.json').write_text(json.dumps(SCREENS))
                        (site / 'index.html').write_text('existing')
                        project = project_record(skip=skip)
                        if present:
                            note = root / 'note.md'
                            note.write_text('Cutoff is 5.\n')
                            imported = optional.import_context(root, note, 'Ari', 'architect', 'current-state', ['s1'])
                            optional.decide_context(root, imported['id'], 'accepted', 'Ari', 'Use the note')
                        version = 'v-' + mode[:3] + ('-s' if skip else '-r') + ('-c' if present else '-n')
                        optional.design_export(root, project, version, mode, 'java')
                        design = json.loads((root / 'previews' / version / 'design.json').read_text())
                        expected = 'SKIPPED_BY_OWNER' if skip else 'INDEPENDENTLY_REVIEWED'
                        self.assertEqual(design['provenance']['verification']['status'], expected)
                        self.assertEqual(design['stack'], 'java')
                        self.assertEqual(design['context_status'], 'provided' if present else 'not_provided')
                        if present:
                            self.assertEqual(design['context_dispositions'][0]['disposition'], 'accepted')
                        if mode == 'reimagine':
                            self.assertEqual(design['proposals'][0]['status'], 'proposed_unapproved')
                            self.assertIn('Amount', design['proposals'][0]['impacts'])
                        else:
                            self.assertEqual(design['proposals'], [])
                            self.assertFalse((root / 'previews' / version / 'future-state').exists())
                        packaged = optional.package_export(root, project, version, mode)
                        self.assertEqual(packaged['status'], 'DRAFT_READY')
                        with zipfile.ZipFile(root / packaged['archive']) as zipped:
                            self.assertEqual(json.loads(zipped.read('design.json'))['mode'], mode)
                            self.assertEqual(json.loads(zipped.read('manifest.json'))['package_status'], 'DRAFT')

    def test_repository_recognition_refuses_bad_infrastructure_and_counts_jobs(self):
        saved = self.root
        (saved / 'project.json').write_text(json.dumps({
            'source': {'repository': 'https://example.test/demo.git', 'commit': 'a' * 40},
            'settings': {'transform': {'sandbox_role': 'role', 'workspace_id': 'workspace', 'upload_prefix': 's3://bucket/reframe'}},
            'aws': {'region': 'us-east-1', 'bucket': 'bucket', 'account': '111111111111', 'profile': 'ador'},
            'setup': {'connector_id': 'connector'},
        }))
        projects.remember_workspace(saved, 'https://example.test/demo.git')
        ui = self._ui(['https://example.test/demo.git', 'reuse-baseline'])
        with self.assertRaises(Blocked):
            projects.verify_saved_infrastructure(json.loads((saved / 'project.json').read_text()), aws_call=self._bad_aws, client=self._bad_client())
        offer_known_repository(ui)
        self.assertTrue(any('No new resources were created.' in line for line in ui.said))
        ui.invoke.assert_not_called()

        fresh = self._ui(['https://example.test/demo.git', 'assess-new-revision'])
        fresh.yes.side_effect = [True]
        with patch('reframe_projects.verify_saved_infrastructure', return_value={'status': 'VERIFIED', 'aws_jobs_created': 0}), \
                patch('reframe_setup.select_workspace'):
            offer_known_repository(fresh)
        self.assertEqual(fresh.invoke.call_args.args[0][:2], ['assess', '--wait'])
        self.assertTrue(any('AWS jobs created: 0' in line for line in fresh.said))

        reuse = self._ui(['https://example.test/demo.git', 'reuse-baseline'])
        with patch('reframe_projects.verify_saved_infrastructure', return_value={'status': 'VERIFIED', 'aws_jobs_created': 0}), \
                patch('reframe_setup.select_workspace'):
            offer_known_repository(reuse)
        reuse.invoke.assert_not_called()

    def test_infrastructure_checker_uses_a_global_role_lookup(self):
        calls = []

        def aws_call(args):
            calls.append(list(args))
            if 'get-caller-identity' in args:
                return {'Account': '111111111111'}
            if args[-2:] == ['--role-name', 'role'] or (len(args) >= 2 and args[-2] == '--role-name' and 'get-role-policy' not in args):
                return {'Role': {'Arn': 'arn:aws:iam::111111111111:role/role',
                                 'AssumeRolePolicyDocument': self._trust()}}
            if 'get-role-policy' in args:
                return {'PolicyDocument': self._policy()}
            if 'get-bucket-location' in args:
                return {'LocationConstraint': 'us-east-1'}
            return {}

        project = json.loads((self.root / 'project.json').read_text())
        project['settings'] = {'transform': {'sandbox_role': 'role', 'workspace_id': 'workspace', 'upload_prefix': 's3://bucket/reframe'}}
        project['aws'] = {'region': 'us-east-1', 'bucket': 'bucket', 'account': '111111111111', 'profile': 'ador'}
        project['setup'] = {'connector_id': 'connector'}
        result = projects.verify_saved_infrastructure(project, aws_call=aws_call, client=self._ready_client())
        self.assertEqual(result['aws_jobs_created'], 0)
        role_call = next(item for item in calls if 'get-role' in item and 'get-role-policy' not in item)
        self.assertNotIn('--region', role_call)
        self.assertIn('--profile', role_call)
        self.assertIn('ador', role_call)
        self.assertFalse(projects.sandbox_policy_allows({'Statement': [{'Sid': 'mentions bucket'}]}, 'bucket'))
        observed = projects.collect_infrastructure(project, aws_call, self._ready_client())
        observed['iam_lookup_global'] = False
        self.assertIn('The role was looked up in a region. IAM roles are global.', projects.infrastructure_problems(observed))

    def test_completed_version_is_immutable_and_incomplete_generation_resumes(self):
        optional.design_export(self.root, self.project, 'v1', 'functional-parity', 'java')
        mode = self.root / 'previews' / 'v1' / 'mode.json'
        html = self.root / 'previews' / 'v1' / 'current-state' / 'index.html'
        record = json.loads(mode.read_text())
        record['status'] = 'generating'
        mode.write_text(json.dumps(record))
        html.unlink()
        optional.design_export(self.root, self.project, 'v1', 'functional-parity', 'java')
        self.assertTrue(html.is_file())
        packaged = optional.package_export(self.root, self.project, 'v1', 'functional-parity')
        saved = (self.root / packaged['archive']).read_bytes()
        again = optional.package_export(self.root, self.project, 'v1', 'functional-parity')
        self.assertTrue(again.get('reused'))
        self.assertEqual((self.root / again['archive']).read_bytes(), saved)
        archive = self.root / packaged['archive']
        archive.write_bytes(saved + b'changed')
        with self.assertRaisesRegex(Blocked, 'integrity failure'):
            optional.package_export(self.root, self.project, 'v1', 'functional-parity')
        self.assertTrue((archive.read_bytes()).endswith(b'changed'))
        note = self.root / 'later.md'
        note.write_text('A later note.\n')
        optional.import_context(self.root, note, 'Ari', 'architect', 'clarification', [])
        with self.assertRaisesRegex(Blocked, 'immutable'):
            optional.design_export(self.root, self.project, 'v1', 'functional-parity', 'java')
        self.assertEqual(json.loads((self.root / 'previews' / 'v1' / 'design.json').read_text())['context_status'], 'not_provided')

    def test_new_project_has_no_call_ceiling_and_keeps_a_saved_cap(self):
        self.assertIsNone(kw.settings(None)['max_model_calls'])
        saved = self.root / 'saved-settings.json'
        saved.write_text(json.dumps({'max_model_calls': 16}))
        self.assertEqual(kw.settings(saved)['max_model_calls'], 16)
        estate = self.root / 'estate-settings.json'
        estate.write_text(json.dumps({'max_model_calls': 5000}))
        self.assertEqual(kw.settings(estate)['max_model_calls'], 5000)
        project = {'settings': {'max_model_calls': 256, 'max_model_dollars': 0.03, 'model_dollars_spent': 0,
                               'model_rates': {'executor': 0.02, 'wireframes': 0.02}},
                   'pipeline': {'calls': 256}, 'approvals': {}, 'events': [], 'providers': {'executor': 'codex'}}
        recorded = reframe.set_model_budget(self.root, project, SimpleNamespace(max_calls=263, by='Ari', note='Finish the remaining programs'))
        self.assertEqual(recorded['total_cap'], 263)
        self.assertEqual(recorded['calls_consumed'], 256)
        self.assertEqual(project['pipeline']['calls'], 256)
        dollars = reframe.set_model_budget(self.root, project, SimpleNamespace(max_dollars=50, by='Ari', note='Estate model budget'))
        self.assertEqual(dollars['dollar_cap'], 50)
        self.assertEqual(project['settings']['max_model_calls'], 263)
        cleared = reframe.set_model_budget(self.root, project, SimpleNamespace(clear_call_cap=True, by='Ari', note='Remove the saved cap'))
        self.assertIsNone(cleared['total_cap'])
        self.assertEqual(project['pipeline']['calls'], 256)
        self.assertIsNone(project['settings']['max_model_calls'])
        project['settings']['max_model_calls'] = 263
        project['settings']['max_model_dollars'] = 0.03
        project['settings']['model_dollars_spent'] = 0
        with patch('reframe_models.claude_uses_api_key', return_value=True):
            reframe.require_dollar_cap(project, 'executor', 'codex')
            self.assertEqual(project['settings']['model_dollars_spent'], 0.02)
            with self.assertRaisesRegex(Blocked, 'dollar cap'):
                reframe.require_dollar_cap(project, 'wireframes', 'codex')

    def test_open_conflicts_block_final_approval_and_a_draft_still_exports(self):
        note = self.root / 'conflict.md'
        note.write_text('Contradicts: R1\nThe cutoff is 9.\n')
        imported = optional.import_context(self.root, note, 'Ari', 'architect', 'current-state', ['s1'])
        optional.decide_context(self.root, imported['id'], 'accepted', 'Ari', 'Leave it open')
        self.assertTrue(optional.open_conflicts(self.root))
        with patch('reframe.require_stage_contract'), patch('reframe.verify_source'), patch('reframe.require_skip_acceptance'):
            with self.assertRaisesRegex(Blocked, 'Open context conflicts block the authorized implementation handoff'):
                reframe.package(self.root, self.project)
            with self.assertRaisesRegex(Blocked, 'Open context conflicts block specification approval'):
                reframe.approve(self.root, self.project, SimpleNamespace(phase='specifications', by='Ari', note='Approve'))
        optional.design_export(self.root, self.project, 'v3', 'functional-parity', 'java')
        packaged = optional.package_export(self.root, self.project, 'v3', 'functional-parity')
        self.assertEqual(packaged['status'], 'DRAFT_BLOCKED_FOR_APPROVAL')

    def test_skip_draft_without_a_review_site_exports_the_same_tail(self):
        root = Path(tempfile.mkdtemp())
        handoff = root / 'previews' / 'guided-draft' / 'handoff'
        handoff.mkdir(parents=True)
        (handoff / 'screens.json').write_text(json.dumps(SCREENS))
        project = project_record(skip=True)
        project.pop('site')
        project['skip_bundle'] = 'previews/guided-draft'
        optional.design_export(root, project, 'v1', 'functional-parity', 'cobol')
        design = json.loads((root / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['provenance']['verification']['kind'], 'recorded_skip')
        self.assertEqual(design['stack'], 'cobol')
        self.assertEqual((handoff / 'screens.json').read_text(), json.dumps(SCREENS))

    def test_missing_evidence_stays_an_explicit_reimagine_gap(self):
        root = Path(tempfile.mkdtemp())
        site = root / 'previews' / 'business-1'
        site.mkdir(parents=True)
        empty = {'journeys': [{'id': 'pay', 'name': 'Pay', 'screen_ids': []}], 'screens': [], 'apis': []}
        (site / 'screens.json').write_text(json.dumps(empty))
        (site / 'index.html').write_text('existing')
        optional.design_export(root, self.project, 'v1', 'reimagine', 'java')
        design = json.loads((root / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['proposals'], [])
        self.assertTrue(design['gaps'])
        packaged = optional.package_export(root, self.project, 'v1', 'reimagine')
        with zipfile.ZipFile(root / packaged['archive']) as zipped:
            manifest = json.loads(zipped.read('manifest.json'))
        self.assertEqual(manifest['package_status'], 'DRAFT')
        self.assertTrue(manifest['design_gaps'])

    def test_skip_bundle_list_is_selected_by_reference_and_keeps_records(self):
        root = Path(tempfile.mkdtemp())
        chosen = root / 'previews' / 'aaa' / 'handoff'
        other = root / 'previews' / 'zzz' / 'handoff'
        chosen.mkdir(parents=True)
        other.mkdir(parents=True)
        screen = {'id': 's1', 'title': 'Amount', 'purpose': 'Check the amount.', 'uncertainties': 'None recorded',
                  'fields': ['Amount'], 'actions': ['Save'], 'states': ['Normal'], 'rule_ids': ['AWS-1'], 'api_ids': ['pay']}
        (chosen / 'screens.json').write_text(json.dumps([screen]))
        (chosen / 'api-contracts.json').write_text(json.dumps([{
            'id': 'pay', 'method': 'POST', 'path': '/pay', 'purpose': 'Pay', 'authentication': 'session',
            'uncertainties': 'None recorded', 'request': {'amount': 'number'}, 'responses': {'200': {'ok': True}},
            'rule_ids': ['AWS-1']}]))
        (chosen / 'rules.json').write_text(json.dumps([{
            'id': 'AWS-1', 'statement': 'Amount must be positive.', 'source_path': 'cbl/PAY.cbl',
            'acceptance_criteria': ['Amount stays positive']}]))
        (chosen / 'implementation-backlog.json').write_text(json.dumps([{
            'id': 'W1', 'title': 'Implement the amount check', 'acceptance': 'Amount stays positive', 'depends_on': []}]))
        (chosen / 'decisions-needed.json').write_text(json.dumps([{'id': 'Q1', 'question': 'Which cutoff?'}]))
        (chosen / 'acceptance-catalog.json').write_text(json.dumps([{'rule_id': 'AWS-1', 'status': 'NOT RUN'}]))
        (chosen / 'specification.json').write_text(json.dumps({
            'scope': 'Pay', 'sections': {'ui': {'specification': 'Use the amount field', 'implementation_prompt': 'Keep the check'}}}))
        (chosen / 'provenance.json').write_text(json.dumps({'source_commit': 'b' * 40, 'verification': 'SKIPPED_BY_OWNER'}))
        (other / 'screens.json').write_text(json.dumps({'screens': [{'id': 's9', 'title': 'Wrong'}], 'journeys': [], 'apis': []}))
        assessment = root / 'runs' / 'old'
        assessment.mkdir(parents=True)
        (assessment / 'source-upload-manifest.json').write_text(json.dumps({'included': [{'path': 'cpy/AMOUNT.cpy'}]}))
        project = project_record(skip=True)
        project.pop('site')
        project['skip_bundle'] = 'previews/aaa'
        project['selected'] = 'run-1'
        project['assessments'] = {'run-1': {'directory': str(assessment)}}
        optional.design_export(root, project, 'v1', 'functional-parity', 'java')
        design = json.loads((root / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['screens'][0]['title'], 'Amount')
        self.assertEqual(design['rules'][0]['statement'], 'Amount must be positive.')
        self.assertEqual(design['backlog'][0]['title'], 'Implement the amount check')
        self.assertEqual(design['questions'][0]['id'], 'Q1')
        self.assertEqual(design['acceptance'][0]['rule_id'], 'AWS-1')
        self.assertEqual(design['sections']['ui']['specification'], 'Use the amount field')
        self.assertEqual(design['provenance']['source_commit'], 'b' * 40)
        self.assertIn('cbl/PAY.cbl', design['source_paths'])
        self.assertIn('cpy/AMOUNT.cpy', design['source_paths'])
        packaged = optional.package_export(root, project, 'v1', 'functional-parity')
        with zipfile.ZipFile(root / packaged['archive']) as zipped:
            names = set(zipped.namelist())
            rules = json.loads(zipped.read('rules.json'))
            backlog = json.loads(zipped.read('implementation-backlog.json'))
            openapi = json.loads(zipped.read('openapi.json'))
        self.assertIn('acceptance-catalog.json', names)
        self.assertEqual(rules[0]['id'], 'AWS-1')
        self.assertEqual(backlog[0]['id'], 'W1')
        self.assertNotIn('GAP-1', json.dumps(backlog))
        self.assertIn('/pay', json.dumps(openapi))

    def test_revision_pin_keeps_the_baseline_and_multiple_matches_are_chosen(self):
        import subprocess
        source = self.root / 'source'
        source.mkdir()
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Reframe', 'GIT_AUTHOR_EMAIL': 'reframe@example.test',
               'GIT_COMMITTER_NAME': 'Reframe', 'GIT_COMMITTER_EMAIL': 'reframe@example.test'}
        subprocess.check_call(['git', 'init'], cwd=source)
        subprocess.check_call(['git', 'remote', 'add', 'origin', 'https://example.test/demo.git'], cwd=source)
        (source / 'PAY.cbl').write_text('baseline\n')
        subprocess.check_call(['git', 'add', 'PAY.cbl'], cwd=source)
        subprocess.check_call(['git', 'commit', '-m', 'baseline'], cwd=source, env=env)
        baseline = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        (source / 'PAY.cbl').write_text('revision\n')
        subprocess.check_call(['git', 'commit', '-am', 'revision'], cwd=source, env=env)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        project = {
            'source': {'root': str(source), 'repository': 'https://example.test/demo.git', 'commit': baseline},
            'approvals': {'assessment': {'by': 'Ari'}}, 'assessments': {'run-1': {'directory': 'runs/old'}},
            'settings': {'transform': {'sandbox_role': 'role', 'workspace_id': 'workspace', 'upload_prefix': 's3://bucket/reframe'}},
            'aws': {'bucket': 'bucket', 'region': 'us-east-1', 'profile': 'ador', 'account': '111111111111'},
            'setup': {'connector_id': 'connector'},
        }
        (self.root / 'project.json').write_text(json.dumps(project))
        pinned = projects.pin_requested_revision(self.root, project, revision)
        reframe.persist(self.root, project)
        saved = json.loads((self.root / 'project.json').read_text())
        self.assertEqual(saved['source']['commit'], baseline)
        self.assertEqual(saved['approvals']['assessment']['by'], 'Ari')
        self.assertEqual(saved['assessments']['run-1']['directory'], 'runs/old')
        self.assertEqual(pinned['commit'], revision)
        self.assertEqual(subprocess.check_output(['git', '-C', pinned['snapshot'], 'rev-parse', 'HEAD'], text=True).strip(), revision)
        with self.assertRaises(Blocked):
            projects.pin_requested_revision(self.root, project, 'missing-ref')
        other = Path(tempfile.mkdtemp())
        (other / 'project.json').write_text(json.dumps({'source': {'repository': 'https://example.test/demo.git'}}))
        projects.remember_workspace(self.root, 'https://example.test/demo.git')
        projects.remember_workspace(other, 'https://example.test/demo.git')
        ui = self._ui(['https://example.test/demo.git', '2', 'reuse-baseline'])
        with patch('reframe_projects.verify_saved_infrastructure', return_value={'status': 'VERIFIED', 'aws_jobs_created': 0}), \
                patch('reframe_setup.select_workspace') as selected:
            offer_known_repository(ui)
        selected.assert_called_once_with(other)
        ui.invoke.assert_not_called()

    def _trust(self):
        return {'Statement': [{'Effect': 'Allow', 'Action': 'sts:AssumeRole',
                               'Principal': {'Service': 'transform.amazonaws.com'},
                               'Condition': {'StringEquals': {'aws:SourceAccount': '111111111111'}}}]}

    def _policy(self):
        arn = 'arn:aws:s3:::bucket'
        return {'Statement': [
            {'Effect': 'Allow', 'Action': ['s3:ListBucket', 's3:GetBucketLocation'], 'Resource': arn},
            {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'], 'Resource': arn + '/*'},
            {'Effect': 'Deny', 'Action': ['s3:GetObject', 's3:ListBucket'], 'NotResource': [arn, arn + '/*']},
        ]}

    def _ready_client(self):
        class Client:
            def calls(self, requests):
                return [{'accountConnection': {'awsAccountConnection': {
                    'status': 'ACTIVE', 'accountId': '111111111111',
                    'roleArn': 'arn:aws:iam::111111111111:role/role'}}} for _request in requests]
        return Client()

    def _bad_client(self):
        class Client:
            def calls(self, requests):
                return [{'accountConnection': {'awsAccountConnection': {'status': 'PENDING', 'accountId': '222222222222'}}}]
        return Client()

    def _bad_aws(self, args):
        if 'get-caller-identity' in args:
            return {'Account': '222222222222'}
        if 'get-role-policy' in args:
            return {'PolicyDocument': {'Statement': [{'Sid': 'bucket'}]}}
        if 'get-role' in args:
            return {'Role': {'Arn': 'arn:aws:iam::111111111111:role/role', 'AssumeRolePolicyDocument': {}}}
        if 'get-bucket-location' in args:
            return {'LocationConstraint': 'eu-west-1'}
        return {'error': 'denied'}

    def _bad_runner(self, args):
        if args[0] == 'sts':
            return {'Account': '222222222222'}
        if args[:2] == ['iam', 'get-role']:
            return {'Role': {'Arn': 'arn:aws:iam::111111111111:role/role', 'AssumeRolePolicyDocument': {}}}
        if args[:2] == ['iam', 'get-role-policy']:
            return {}
        if args[1] == 'get-bucket-location':
            return {'LocationConstraint': 'eu-west-1'}
        if args[0] == 's3api':
            return {'error': 'denied'}
        if args[1] == 'get-workspace':
            return {'region': 'eu-west-1', 'account': '111111111111'}
        return {'status': 'pending', 'role_name': 'other'}

    def _ui(self, answers):
        from unittest.mock import Mock
        ui = Mock()
        ui.ask.side_effect = answers
        ui.yes.return_value = False
        ui.invoke.return_value = 0
        ui.said = []
        ui.say.side_effect = ui.said.append
        return ui


if __name__ == '__main__':
    unittest.main()
