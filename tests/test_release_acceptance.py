"""Menu-1 acceptance journeys. AWS and model providers are mocked; setup, rendering, and packaging are not."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
os.environ.setdefault('REFRAME_CONFIG_HOME', tempfile.mkdtemp(prefix='reframe-acceptance-config-'))

import kiro_workflow as kw
import pilot
import reframe
import reframe_checkpoints as cp
import reframe_optional as optional
import reframe_preview
import test_reframe as fixtures
import test_reframe_checkpoints as checkpoint_fixtures
from reframe_artifacts import SECTIONS
from reframe_setup import select_workspace


def commit_source(source):
    """Commit one local source tree without changing the user's Git configuration."""
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Release Fixture', 'GIT_AUTHOR_EMAIL': 'release@example.test',
           'GIT_COMMITTER_NAME': 'Release Fixture', 'GIT_COMMITTER_EMAIL': 'release@example.test'}
    subprocess.check_call(['git', 'init'], cwd=source, stdout=subprocess.DEVNULL)
    subprocess.check_call(['git', 'add', '.'], cwd=source)
    subprocess.check_call(['git', 'commit', '-m', 'baseline'], cwd=source, env=env, stdout=subprocess.DEVNULL)
    subprocess.check_call(['git', 'remote', 'add', 'origin', 'https://example.test/demo.git'], cwd=source)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()


ORIGINAL_GIT = pilot.git


def framework_git(root, *args):
    """Hide this dirty worktree from the cleanliness gate. Source repositories stay real."""
    if Path(root).resolve() == Path(reframe.FRAMEWORK).resolve() and args and args[0] == 'status':
        return ''
    return ORIGINAL_GIT(root, *args)


class MenuJourney:
    def __init__(self, findings):
        self.root = Path(tempfile.mkdtemp())
        self.config = Path(tempfile.mkdtemp())
        os.environ['REFRAME_CONFIG_HOME'] = str(self.config)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'rule.cbl').write_text('IF AMOUNT = ZERO\n MOVE 1 TO ERROR-FLAG\nEND-IF\n')
        copybook = self.source / 'cpy'
        copybook.mkdir()
        (copybook / 'AMOUNT.cpy').write_text('05 AMOUNT PIC 9(7)V99.\n')
        batch = self.source / 'jcl'
        batch.mkdir()
        (batch / 'NIGHTLY.jcl').write_text('//NIGHTLY JOB (ACCT),\'NIGHTLY\'\n//STEP1 EXEC PGM=RULE\n')
        self.commit = commit_source(self.source)
        self.findings = self.root / 'findings'
        self.findings.mkdir()
        (self.findings / 'rules.json').write_text(json.dumps(findings))
        self.work = self.root / 'private'
        args = reframe.parser().parse_args(['--workspace', str(self.work), 'init', '--source', str(self.source),
                                            '--scope', 'Zero-amount journey'])
        reframe.initialize(args, self.work)
        self.project = reframe.read(self.work / 'project.json')
        self.project['settings']['max_model_dollars'] = 100
        reframe.persist(self.work, self.project)
        reframe.assessment(self.work, self.project, reframe.parser().parse_args(
            ['--workspace', str(self.work), 'assess', '--label', 'run-1', '--findings', str(self.findings)]))
        reframe.approve(self.work, self.project, reframe.parser().parse_args(
            ['--workspace', str(self.work), 'approve', 'assessment', '--label', 'run-1', '--by', 'Ari',
             '--note', 'Findings match the pinned source', '--confirm-source-match']))
        select_workspace(self.work)
        self.project = reframe.read(self.work / 'project.json')

    def reload(self):
        self.project = reframe.read(self.work / 'project.json')
        return self.project


def drive(answers, model, aws):
    """Start the no-argument console, choose menu actions, and keep the prompt transcript."""
    transcript = []
    remaining = list(answers)

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def prompt(self, message, **kwargs):
            text = message if isinstance(message, str) else ''.join(
                part[1] if isinstance(part, tuple) else str(part) for part in message)
            transcript.append(text.strip())
            if not remaining:
                raise AssertionError('No scripted answer for: ' + text + '\n' + '\n'.join(transcript))
            answer = remaining.pop(0)
            transcript.append(answer)
            if answer == '<default>':
                return kwargs.get('default') or ''
            return answer

    stdout = io.StringIO()
    stdout.isatty = lambda: True
    real_pipeline, real_review, real_semantic = reframe.pipeline, reframe.review_site, reframe.semantic_compare

    def pipeline(work, project, args, runtime=None):
        return real_pipeline(work, project, args, model)

    def review_site(work, project, args, runtime=None):
        return real_review(work, project, args, model)

    def semantic_compare(work, project, args, runtime=None):
        return real_semantic(work, project, args, model)

    with ExitStack() as stack:
        stack.enter_context(patch.object(sys.stdin, 'isatty', return_value=True))
        stack.enter_context(patch.object(sys, 'stdout', stdout))
        stack.enter_context(patch('reframe_terminal.PromptSession', Session))
        stack.enter_context(patch.object(reframe, 'model_call', model))
        stack.enter_context(patch.object(reframe, 'pipeline', pipeline))
        stack.enter_context(patch.object(reframe, 'review_site', review_site))
        stack.enter_context(patch.object(reframe, 'semantic_compare', semantic_compare))
        stack.enter_context(patch.object(reframe, 'aws', aws))
        if os.environ.get('REFRAME_REAL_FRAMEWORK') != '1':
            for module in (pilot, reframe, kw, cp, reframe_preview):
                stack.enter_context(patch.object(module, 'git', framework_git))
        code = reframe.main([])
    return code, transcript, stdout.getvalue()


def preview_design(message):
    context = json.loads(message)
    rule_id = context['programs'][0]['example_rule']['id']
    value = fixtures.PreparationTests.screens(None)
    for screen in value['screens']:
        screen['rule_ids'] = [rule_id]
    for api in value['apis']:
        api['rule_ids'] = [rule_id]
    spec = fixtures.PreparationTests.spec(None)
    value.update(sections=spec['sections'], work_items=spec['work_items'], monorepo_layout=spec['monorepo_layout'],
                 questions=[{'id': 'Q1', 'question': 'Confirm the amount rule', 'owner': 'Architect',
                             'impact': 'Unapproved design', 'blocks': 'Implementation'}])
    return value


def review_model(helper, state):
    def model(provider, run, directory, role, message):
        state['calls'].append(role)
        if state.get('pause'):
            state['pause'] = False
            raise kw.RuntimeUnavailable('provider paused')
        context = json.loads(message)
        if 'programs' in context:
            return preview_design(message)
        if role == 'wireframes':
            spec = json.loads(message)['specification']
            screens = fixtures.PreparationTests.screens(None)
            rule = spec['rules'][0]['id']
            for screen in screens['screens']:
                screen['rule_ids'] = [rule]
            for api in screens['apis']:
                api['rule_ids'] = [rule]
            return screens
        return helper.runtime(provider, run, directory, role, message)
    return model


def refuse_aws(*args, **kwargs):
    raise AssertionError('live AWS call')


class ReleaseAcceptanceTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('REFRAME_REAL_FRAMEWORK') == '1':
            return
        for module in (pilot, reframe, kw, cp, reframe_preview):
            active = patch.object(module, 'git', framework_git)
            active.start()
            self.addCleanup(active.stop)

    def test_skip_journey_preserves_the_bundle_and_rejects_a_changed_version(self):
        findings = {'program_name': 'PAY', 'all_rules': [{
            'Rule_Id': 'R1', 'Rule_Name': 'Reject zero', 'Rule_Description': 'Reject a zero amount',
            'Acceptance_Criteria': ['Zero is rejected']}]}
        journey = MenuJourney(findings)
        self.assertIsNone(journey.project['settings']['max_model_calls'])
        state = {'calls': [], 'pause': False}
        helper = checkpoint_fixtures.CheckpointTests('test_default_workflow_reviews_units_then_integrates_and_preserves_human_gate')
        helper.seen = []
        code, transcript, console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'Ari', 'Accepted the sealed findings', 'codex', 'y',
             'functional-parity', 'v1', 'java', 'none', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, console)
        self.assertEqual(state['calls'], ['wireframes'])
        project = journey.reload()
        self.assertEqual(project['verification_skip']['owner'], 'Ari')
        self.assertNotIn('validation', project['approvals'])
        design = json.loads((journey.work / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['mode'], 'functional-parity')
        self.assertEqual(design['stack'], 'java')
        self.assertEqual(design['provenance']['source_commit'], journey.commit)
        self.assertEqual(design['provenance']['assessment_sha256'], project['assessments']['run-1']['sha256'])
        self.assertEqual(design['provenance']['verification']['status'], 'SKIPPED_BY_OWNER')
        self.assertTrue(design['rules'])
        self.assertEqual(design['rules'][0]['aws_rule_id'], 'R1')
        self.assertTrue(design['questions'])
        self.assertTrue(design['backlog'])
        self.assertIn('sections', design['specification'])
        self.assertTrue(set(SECTIONS).issubset(set(design['specification']['sections'])))
        page = (journey.work / 'previews' / 'v1' / 'current-state' / 'index.html').read_text()
        self.assertIn('Amount', page)
        self.assertIn('not_provided', page)
        self.assertFalse((journey.work / 'previews' / 'v1' / 'future-state').exists())
        with zipfile.ZipFile(journey.work / 'packages' / 'v1' / 'functional-parity' / 'ai-dlc-package.zip') as archive:
            packaged = json.loads(archive.read('design.json'))
            self.assertEqual(packaged['rules'][0]['aws_rule_id'], 'R1')
            self.assertTrue(json.loads(archive.read('implementation-backlog.json')))
            self.assertTrue(json.loads(archive.read('decisions-needed.json')))
            self.assertIn('Reject a zero amount', archive.read('rules.json').decode())
            self.assertEqual(json.loads(archive.read('manifest.json'))['source_commit'], journey.commit)
            inventory = json.loads(archive.read('field-inventory.json'))
            batch = json.loads(archive.read('batch-inventory.json'))
            self.assertEqual(inventory, packaged['inventories']['fields'])
            self.assertFalse(inventory['fields_parsed'])
            self.assertEqual(inventory['copybooks'], [{'path': 'cpy/AMOUNT.cpy', 'fields': []}])
            self.assertEqual(batch['jobs'], [{'path': 'jcl/NIGHTLY.jcl', 'status': 'NOT_STARTED'}])
            self.assertNotIn('PIC', json.dumps(inventory))
            self.assertIn(packaged['rules'][0]['source_path'], archive.read('rules.json').decode())
        mode_path = journey.work / 'previews' / 'v1' / 'mode.json'
        saved_mode = json.loads(mode_path.read_text())
        saved_mode['status'] = 'generating'
        mode_path.write_text(json.dumps(saved_mode))
        resumed = optional.design_export(journey.work, journey.reload(), 'v1', 'functional-parity', 'java')
        self.assertTrue(resumed['resumed'])
        from rich.console import Console
        from reframe_terminal import Terminal
        visible = io.StringIO()
        Terminal(console=Console(file=visible, force_terminal=False, width=120)).report(
            {'status': 'EXPORTED', 'resumed': True, 'current_state': 'previews/v1/current-state'})
        self.assertIn('Incomplete generation was resumed', visible.getvalue())
        self.assertIn('Review the completed version before packaging.', visible.getvalue())
        self.assertNotIn('previews/v1/current-state', visible.getvalue())
        note = journey.root / 'cutoff.md'
        note.write_text('Cutoff is 5.\n')
        code, changed, changed_console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'y', 'functional-parity', 'v1', 'java', str(note),
             'Ari', 'architect', 'current-state', 's1', 'accepted', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0)
        self.assertIn('Completed version is immutable', changed_console)
        self.assertIn('Export the next version', changed_console)
        self.assertNotIn(str(note), changed_console)
        self.assertEqual(json.loads((journey.work / 'previews' / 'v1' / 'design.json').read_text())['context_status'], 'not_provided')
        code, revised, revised_console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'y', 'functional-parity', 'v2', 'java', 'none', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, '\n'.join(revised) + revised_console)
        revised_page = (journey.work / 'previews' / 'v2' / 'current-state' / 'index.html').read_text()
        self.assertIn('Cutoff is 5.', revised_page)
        self.assertNotIn('Cutoff is 5.', page)
        api = journey.work / 'previews' / 'v2' / 'current-state' / 'api.html'
        original = api.read_text()
        api.write_text('corrupted')
        code, integrity, integrity_console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'y', 'functional-parity', 'v2', 'java', 'none', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0)
        self.assertIn('integrity failure', integrity_console)
        self.assertIn('Restore the completed files', integrity_console)
        self.assertNotIn('current-state/api.html', integrity_console)
        self.assertEqual(api.read_text(), 'corrupted')
        self.assertEqual(json.loads((journey.work / 'previews' / 'v2' / 'mode.json').read_text())['status'], 'complete')
        self.assertIn('Amount', original)
        code, corrupt, corrupt_console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'y', 'functional-parity', 'v1', 'cobol', 'none', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0)
        self.assertIn('Completed version is immutable', corrupt_console)
        self.assertNotIn('integrity failure', corrupt_console)
        self.assertEqual(json.loads((journey.work / 'previews' / 'v1' / 'design.json').read_text())['stack'], 'java')
        calls_before = list(state['calls'])
        code, recognized, recognized_console = drive(
            ['1', 'recognize', 'reuse-baseline', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, '\n'.join(recognized) + recognized_console)
        self.assertIn('No new resources were created.', '\n'.join(recognized) + recognized_console)
        self.assertEqual(state['calls'], calls_before)
        self.assertEqual(set(journey.reload()['assessments']), {'run-1'})
        subprocess.check_call(['git', 'commit', '--allow-empty', '-m', 'revision'], cwd=journey.source,
                              env={**os.environ, 'GIT_AUTHOR_NAME': 'Release Fixture', 'GIT_AUTHOR_EMAIL': 'release@example.test',
                                   'GIT_COMMITTER_NAME': 'Release Fixture', 'GIT_COMMITTER_EMAIL': 'release@example.test'},
                              stdout=subprocess.DEVNULL)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=journey.source, text=True).strip()
        with patch('reframe_projects.verify_saved_infrastructure', return_value={'status': 'VERIFIED', 'aws_jobs_created': 0}):
            code, pinned, pinned_console = drive(
                ['1', 'recognize', 'assess-new-revision', revision, 'n', 'stop', '0'],
                review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, '\n'.join(pinned) + pinned_console)
        saved = journey.reload()
        self.assertEqual(saved['pending_revision']['commit'], revision)
        self.assertEqual(saved['source']['commit'], journey.commit)
        self.assertEqual(set(saved['assessments']), {'run-1'})
        self.assertEqual(state['calls'], calls_before)
        text = '\n'.join(transcript + ['', '--- context revision ---', ''] + changed
                         + ['', '--- corruption ---', ''] + integrity
                         + ['', '--- recognize ---', ''] + recognized
                         + ['', '--- revision pin ---', ''] + pinned)
        path = Path(__file__).resolve().parents[1] / 'acceptance-skip-transcript.txt'
        path.write_text(text + '\n\n' + console + '\n' + changed_console + '\n' + integrity_console + '\n' + pinned_console)

    def test_review_journey_resumes_and_keeps_an_unapproved_reimagine_change(self):
        journey = MenuJourney({'rules': ['reject zero amount']})
        journey.project['settings']['max_model_calls'] = 256
        journey.project['pipeline'] = {'calls': 256}
        journey.project['stage'] = 'ASSESSMENT_APPROVED'
        reframe.persist(journey.work, journey.project)
        state = {'calls': [], 'pause': True}
        helper = checkpoint_fixtures.CheckpointTests('test_default_workflow_reviews_units_then_integrates_and_preserves_human_gate')
        helper.seen = []
        code, first, console = drive(
            ['1', 'continue', 'review', 'y', 'y', 'Ari', 'Finish the remaining programs', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, console + '\n' + '\n'.join(first))
        paused = journey.reload()
        self.assertEqual(paused['stage'], 'WAITING_FOR_RUNTIME')
        self.assertGreater(paused['pipeline']['calls'], 256)
        self.assertGreater(paused['settings']['max_model_calls'], 256)
        spent = paused['pipeline']['calls']
        code, second, console = drive(
            ['1', 'continue', 'y', 'Ari', 'Finish the remaining programs',
             'y', 'Ari', 'Deferred for package review', 'y', 'Ari', 'Reviewed the evidence',
             'y', 'Ari', 'Approve the recorded screens', 'reimagine', 'v1', 'java', 'none', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, console + '\n' + '\n'.join(second))
        self.assertIn('Resuming the saved review', '\n'.join(second) + console)
        project = journey.reload()
        self.assertGreaterEqual(project['pipeline']['calls'], spent)
        self.assertEqual(project['stage'], 'PACKAGED', console + '\n' + '\n'.join(second))
        self.assertIn('validation', project['approvals'])
        self.assertNotIn('assess', [role for role in state['calls']])
        design = json.loads((journey.work / 'previews' / 'v1' / 'design.json').read_text())
        self.assertEqual(design['mode'], 'reimagine')
        self.assertEqual(design['stack'], 'java')
        self.assertEqual(design['provenance']['verification']['status'], 'INDEPENDENTLY_REVIEWED')
        self.assertEqual(design['provenance']['source_commit'], journey.commit)
        proposal = design['proposals'][0]
        self.assertEqual(proposal['status'], 'proposed_unapproved')
        self.assertNotEqual(proposal.get('authorization'), 'implementation_requirement')
        self.assertTrue(proposal['change'].startswith('Before:'))
        self.assertIn('After:', proposal['change'])
        self.assertNotIn('Keep journey', proposal['change'])
        future = (journey.work / 'previews' / 'v1' / 'future-state' / 'index.html').read_text()
        current = (journey.work / 'previews' / 'v1' / 'current-state' / 'index.html').read_text()
        self.assertIn('Amount', current)
        self.assertIn('proposed_unapproved', future)
        self.assertIn('Confirm Submit', future)
        with zipfile.ZipFile(journey.work / 'packages' / 'v1' / 'reimagine' / 'ai-dlc-package.zip') as archive:
            self.assertIn('proposed_unapproved', archive.read('deltas.json').decode())
            self.assertNotIn('implementation_requirement', archive.read('deltas.json').decode())
            self.assertEqual(json.loads(archive.read('manifest.json'))['package_status'], 'DRAFT')
        conflict = journey.root / 'conflict.md'
        conflict.write_text('Contradicts: R1\nThe cutoff is 9.\n')
        code, blocked, _console = drive(
            ['1', 'continue', 'reimagine', 'v2', 'java', str(conflict), 'Ari', 'architect', 'current-state', 's1',
             'accepted', 'y', 'needs-more-evidence', 'Ari', 'Need the source line', '0'],
            review_model(helper, state), refuse_aws)
        self.assertEqual(code, 0, '\n'.join(blocked))
        packaged = reframe.read(journey.work / 'project.json')
        self.assertTrue((journey.work / 'packages' / 'v2' / 'reimagine' / 'ai-dlc-package.zip').is_file())
        with zipfile.ZipFile(journey.work / 'packages' / 'v2' / 'reimagine' / 'ai-dlc-package.zip') as archive:
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['final_handoff'], 'BLOCKED')
        approval = reframe.main(['approve', 'specifications', '--by', 'Ari', '--note', 'Try the final gate'],
                                reporter=lambda value: None)
        self.assertEqual(approval, 2)
        self.assertEqual(packaged['stage'], 'PACKAGED')
        text = '\n'.join(first + ['', '--- resume ---', ''] + second + ['', '--- conflict ---', ''] + blocked)
        path = Path(__file__).resolve().parents[1] / 'acceptance-review-transcript.txt'
        path.write_text(text + '\n\n' + console + '\n')

    def test_a_fresh_project_is_uncapped_and_a_saved_256_is_not_a_ceiling(self):
        self.assertIsNone(kw.settings(None)['max_model_calls'])
        journey = MenuJourney({'rules': ['reject zero amount']})
        journey.project['settings']['max_model_calls'] = 256
        journey.project['pipeline'] = {'calls': 256}
        journey.project['stage'] = 'ASSESSMENT_APPROVED'
        reframe.persist(journey.work, journey.project)
        with patch.object(reframe, 'git', framework_git), patch.object(kw, 'git', framework_git), \
                patch.object(cp, 'git', framework_git):
            blocked = reframe.pipeline(journey.work, journey.project, reframe.parser().parse_args(
                ['--workspace', str(journey.work), 'validate']),
                runtime=lambda *args: self.fail('exhausted cap must not call a model'))
        self.assertEqual(blocked['calls'], 256)
        self.assertEqual(journey.reload()['pipeline']['calls'], 256)
        raised = reframe.set_model_budget(journey.work, journey.project, type('B', (), {
            'max_calls': 300, 'max_dollars': None, 'clear_call_cap': False, 'by': 'Ari', 'note': 'The estate continues'})())
        self.assertEqual(raised['total_cap'], 300)
        self.assertEqual(journey.reload()['pipeline']['calls'], 256)
        with self.assertRaises(reframe.Blocked):
            reframe.set_model_budget(journey.work, journey.project, type('B', (), {
                'max_calls': 256, 'max_dollars': None, 'clear_call_cap': False, 'by': 'Ari', 'note': 'Does not exceed consumption'})())

    def test_menu_shows_an_existing_conflict_when_context_is_none(self):
        findings = {'program_name': 'PAY', 'all_rules': [{
            'Rule_Id': 'R1', 'Rule_Name': 'Reject zero', 'Rule_Description': 'Reject a zero amount',
            'Acceptance_Criteria': ['Zero is rejected']}]}
        journey = MenuJourney(findings)
        note = journey.root / 'conflict.md'
        note.write_text('Contradicts: R1\nThe cutoff is 9.\n')
        optional.import_context(journey.work, note, 'Ari', 'architect', 'current-state', ['R1'])
        helper = checkpoint_fixtures.CheckpointTests()
        state = {'calls': [], 'pause': False}
        code, prompts, console = drive(
            ['1', 'continue', 'skip', 'guided-draft', 'Ari', 'Accepted the sealed findings', 'claude', 'y',
             'functional-parity', 'v1', 'java', 'none', 'n', 'y', '0'],
            review_model(helper, state), refuse_aws)
        text = '\n'.join(prompts) + console
        self.assertEqual(code, 0, text)
        self.assertIn('none', prompts)
        self.assertIn('unresolved context conflicts', text)
        self.assertIn('Defer the open conflict', text)
        self.assertIn('The conflict stays open', text)
        with zipfile.ZipFile(journey.work / 'packages' / 'v1' / 'functional-parity' / 'ai-dlc-package.zip') as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['final_handoff'], 'BLOCKED')
        path = Path(__file__).resolve().parents[1] / 'acceptance-conflict-transcript.txt'
        path.write_text(text + '\n')

    def test_fresh_start_from_an_empty_config(self):
        if os.environ.get('REFRAME_FRESH_SNAPSHOT') != '1':
            self.skipTest('The clean snapshot runner executes this journey')
        root = Path(tempfile.mkdtemp())
        source = root / 'source'
        source.mkdir()
        (source / 'rule.cbl').write_text('IF AMOUNT = ZERO\n MOVE 1 TO ERROR-FLAG\nEND-IF\n')
        (source / 'cpy').mkdir()
        (source / 'cpy' / 'AMOUNT.cpy').write_text('05 AMOUNT PIC 9(7)V99.\n')
        (source / 'jcl').mkdir()
        (source / 'jcl' / 'NIGHTLY.jcl').write_text('//NIGHTLY JOB (ACCT),\'NIGHTLY\'\n//STEP1 EXEC PGM=RULE\n')
        commit_source(source)
        parent = root / 'engineer'
        parent.mkdir()
        os.environ['REFRAME_CONFIG_HOME'] = tempfile.mkdtemp(prefix='reframe-fresh-config-')
        self.assertFalse((Path(os.environ['REFRAME_CONFIG_HOME']) / 'projects.json').exists())
        role = 'arn:aws:iam::111111111111:role/fixture-role'
        user = '11111111-1111-1111-1111-111111111111'

        class FakeTransform:
            def __init__(self, config, directory):
                pass

            def calls(self, requests):
                results = []
                for name, _payload in requests:
                    if name == 'get_status':
                        results.append({'connection': {'configured': True}})
                    elif name == 'create_workspace':
                        results.append({'workspaceId': 'workspace'})
                    elif name == 'create_connector':
                        results.append({'connectorId': 'connector'})
                    elif name == 'accept_connector':
                        results.append({'status': 'ACTIVE'})
                    elif name == 'get_resource':
                        results.append({'accountConnection': {'awsAccountConnection': {
                            'status': 'ACTIVE', 'accountId': '111111111111', 'roleArn': role}}})
                    elif name == 'list_resources':
                        results.append({'users': [{'email': 'Ari', 'userId': user}]})
                    else:
                        results.append({})
                return results

        def aws(project, *args):
            joined = ' '.join(args)
            if 'get-caller-identity' in joined:
                return {'Account': '111111111111', 'Arn': 'arn:aws:sts::111111111111:assumed-role/fixture-role/Ari'}
            if 'get-public-access-block' in joined:
                return {'PublicAccessBlockConfiguration': {
                    'BlockPublicAcls': True, 'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True, 'RestrictPublicBuckets': True}}
            return {}

        def seal(run, directory, client):
            folder = directory / 'assessment'
            folder.mkdir(exist_ok=True)
            payload = {'program_name': 'PAY', 'all_rules': [{
                'Rule_Id': 'R1', 'Rule_Name': 'Reject zero', 'Rule_Description': 'Reject a zero amount',
                'Acceptance_Criteria': ['Zero is rejected']}]}
            target = folder / 'rules.json'
            target.write_text(json.dumps(payload))
            run['assessment_index'] = [{'path': 'rules.json', 'sha256': sha256(target.read_bytes()).hexdigest()}]
            run['assessment_ready'] = True

        import reframe_setup as setup
        helper = checkpoint_fixtures.CheckpointTests()
        state = {'calls': [], 'pause': False}
        answers = [
            '1', str(source), 'y', str(parent), '<default>', 'Zero-amount journey', 'fixture-profile', 'us-east-1',
            'claude', 'claude', 'y', 'Ari', '250000000', 'y', 'y', 'Ari', 'Findings match the pinned source', 'y',
            'skip', 'guided-draft', 'Ari', 'Accepted the sealed findings', 'claude', 'y',
            'functional-parity', 'v1', 'java', 'none', '0']
        with patch.object(setup, 'ensure_aws_login', lambda project: None), \
                patch.object(setup, 'checks', lambda project, work=None: None), \
                patch.object(setup, 'sandbox_outputs', lambda *args, **kwargs: ('fixture-bucket', 'reframe/', 'fixture-role', role)), \
                patch.object(setup, 'confirm_production_deny', lambda *args, **kwargs: None), \
                patch.object(setup, 'connect_transform', lambda *args, **kwargs: FakeTransform(None, None)), \
                patch.object(kw, 'Transform', FakeTransform), \
                patch.object(kw, 'assessment_step', seal), \
                patch('reframe_models.claude_uses_api_key', return_value=False):
            code, prompts, console = drive(answers, review_model(helper, state), aws)
        text = '\n'.join(prompts) + '\n' + console
        self.assertEqual(code, 0, text)
        self.assertIn('Repository URL to recognize', text)
        self.assertIn('Create a new project?', text)
        self.assertIn('Have you reviewed the assessment outputs', text)
        self.assertIn('Draft functional-parity package written for v1', text)
        config = Path(os.environ['REFRAME_CONFIG_HOME'])
        work = Path(json.loads((config / 'active.json').read_text())['workspace'])
        project = reframe.read(work / 'project.json')
        self.assertIn('run-1', project['assessments'])
        self.assertTrue(project['approvals']['assessment'])
        archive_path = work / 'packages' / 'v1' / 'functional-parity' / 'ai-dlc-package.zip'
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(archive_path) as archive:
            inventory = json.loads(archive.read('field-inventory.json'))
            batch = json.loads(archive.read('batch-inventory.json'))
        self.assertEqual(inventory['copybooks'], [{'path': 'cpy/AMOUNT.cpy', 'fields': []}])
        self.assertEqual(batch['jobs'], [{'path': 'jcl/NIGHTLY.jcl', 'status': 'NOT_STARTED'}])
        self.assertFalse(inventory['fields_parsed'])
        path = Path(__file__).resolve().parents[1] / 'acceptance-fresh-transcript.txt'
        path.write_text(text + '\n')

    def test_clean_snapshot_passes_and_a_dirty_tree_is_refused(self):
        snapshot = Path(tempfile.mkdtemp(prefix='reframe-clean-snapshot-'))
        source = Path(__file__).resolve().parents[1]

        def ignored(directory, names):
            return [name for name in names if name in {'.git', '.venv', '__pycache__', '.cursor'} or name.endswith('.pyc')]

        shutil.copytree(source, snapshot, dirs_exist_ok=True, ignore=ignored)
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Reframe Test', 'GIT_AUTHOR_EMAIL': 'reframe-test@example.com',
               'GIT_COMMITTER_NAME': 'Reframe Test', 'GIT_COMMITTER_EMAIL': 'reframe-test@example.com',
               'REFRAME_FRESH_SNAPSHOT': '1', 'REFRAME_REAL_FRAMEWORK': '1', 'PYTHONPATH': 'tests',
               'REFRAME_CONFIG_HOME': tempfile.mkdtemp(prefix='reframe-snapshot-config-')}
        subprocess.check_call(['git', 'init'], cwd=snapshot, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'add', '-A'], cwd=snapshot, stdout=subprocess.DEVNULL)
        subprocess.check_call(['git', 'commit', '-m', 'offline clean snapshot'], cwd=snapshot, env=env, stdout=subprocess.DEVNULL)
        child = subprocess.run(
            [sys.executable, '-m', 'unittest', 'test_release_acceptance.ReleaseAcceptanceTests.test_fresh_start_from_an_empty_config'],
            cwd=snapshot, env=env, capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stdout + '\n' + child.stderr)
        transcript = snapshot / 'acceptance-fresh-transcript.txt'
        self.assertTrue(transcript.is_file())
        shutil.copyfile(transcript, source / 'acceptance-fresh-transcript.txt')
        (snapshot / 'scripts' / 'reframe.py').write_text((snapshot / 'scripts' / 'reframe.py').read_text() + '\n# dirty\n')
        probe = subprocess.run([sys.executable, '-c', """
import os, subprocess, tempfile
from pathlib import Path
import kiro_workflow as kw
from pilot import Blocked
source = Path(tempfile.mkdtemp())
(source / 'rule.cbl').write_text('IF AMOUNT = ZERO\\n')
subprocess.check_call(['git', 'init'], cwd=source, stdout=subprocess.DEVNULL)
subprocess.check_call(['git', 'add', '-A'], cwd=source, stdout=subprocess.DEVNULL)
subprocess.check_call(['git', 'commit', '-m', 'source'], cwd=source, stdout=subprocess.DEVNULL)
try:
    kw.initialize(type('A', (), {'source': str(source), 'workspace': tempfile.mkdtemp(), 'settings': None,
        'objective': 'probe', 'assessment': None, 'source_record': None})())
except Blocked as error:
    if 'Framework checkout is not clean' not in str(error):
        raise SystemExit('unexpected: ' + str(error))
    print('dirty-framework-refused')
else:
    raise SystemExit('cleanliness check allowed a dirty framework')
"""], cwd=snapshot, env={**env, 'PYTHONPATH': str(snapshot / 'scripts')}, capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stdout + '\n' + probe.stderr)
        self.assertIn('dirty-framework-refused', probe.stdout)
