"""Guided flow routing: no AWS or model calls."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
os.environ.setdefault('REFRAME_CONFIG_HOME', tempfile.mkdtemp(prefix='reframe-guided-config-'))
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
from reframe_flow import run
from reframe_terminal import Terminal
from rich.console import Console

class GuidedFlowTests(unittest.TestCase):
    def test_conflict_prompt_uses_keep_human_statement(self):
        from reframe_flow import finish_guided_design
        import reframe_optional as optional
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / 'project.json').write_text('{"stage":"PACKAGED","settings":{}}')
            note = folder / 'human.md'
            note.write_text('Contradicts: R1\nThe cutoff is 9.\n')
            imported = optional.import_context(folder, note, 'Ari', 'architect', 'current-state', ['s1'])
            optional.decide_context(folder, imported['id'], 'accepted', 'Ari', 'Recorded')
            ui = self.ui({'stage': 'PACKAGED', 'settings': {}, 'approvals': {}}, [
                'functional-parity', 'v1', 'unspecified', str(note), 'Ari', 'architect', 'current-state', 's1', 'rejected',
                'keep-human-statement', 'Ari', 'Use the human statement',
            ])
            ui.yes.return_value = True
            with patch('reframe_setup.workspace', return_value=folder):
                finish_guided_design(ui, ui.project())
        prompts = [call.args[0] for call in ui.ask.call_args_list]
        self.assertTrue(any('keep-human-statement' in prompt for prompt in prompts))
        self.assertFalse(any('keep-context' in prompt for prompt in prompts))
        resolved = [call.args[0] for call in ui.invoke.call_args_list if call.args[0][0] == 'context' and 'resolve' in call.args[0]]
        self.assertEqual(resolved[0][resolved[0].index('--resolution') + 1], 'keep-human-statement')
    def ui(self, project, answers):
        ui = Mock()
        ui.project.return_value = project
        ui.ask.side_effect = answers
        ui.invoke.return_value = 0
        return ui

    def test_skip_never_runs_verification_or_approved_package(self):
        ui = self.ui({'stage':'BLOCKED','approvals':{'assessment':True}},
                     ['continue','draft','guided-draft','Owner','Demo','claude',
                      'functional-parity','v1','unspecified','none'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        commands = [call.args[0][0] for call in ui.invoke.call_args_list]
        self.assertEqual(commands[0], 'preview')
        self.assertIn('--skip-validation', ui.invoke.call_args_list[0].args[0])
        self.assertIn('design-export', commands)
        self.assertIn('package-export', commands)
        self.assertNotIn('validate', commands)
        self.assertNotIn('package', commands)

    def test_review_declined_never_approves(self):
        ui = self.ui({'stage':'AWAITING_ASSESSMENT_APPROVAL'}, ['continue'])
        ui.yes.return_value = False
        run(ui)
        ui.invoke.assert_called_once_with(['assessment-report'])

    def test_running_assessment_resumes_without_new_job(self):
        ui = self.ui({'stage':'ASSESSING','active_assessment':'run-1'}, ['continue'])
        run(ui)
        ui.invoke.assert_called_once_with(['resume','--wait'])

    def test_failed_setup_stops(self):
        ui = self.ui(None, ['https://github.com/example/new.git'])
        ui.invoke.return_value = 2
        run(ui)
        ui.invoke.assert_called_once_with(['init', '--guided', '--source', 'https://github.com/example/new.git'])

    def test_verify_failure_never_falls_back_to_draft(self):
        ui = self.ui({'stage':'ASSESSMENT_APPROVED','approvals':{'assessment':True}}, ['continue','verify'])
        ui.yes.return_value = True
        ui.invoke.return_value = 2
        run(ui)
        ui.invoke.assert_called_once_with(['validate'])

    def test_validation_gate_does_not_restart_verification(self):
        ui = self.ui({'stage':'AWAITING_VALIDATION_APPROVAL','approvals':{'assessment':True}}, ['continue'])
        ui.yes.return_value = False
        run(ui)
        ui.invoke.assert_not_called()

    def test_demo_flow_cannot_invoke_backend(self):
        ui = Terminal(console=Console(file=io.StringIO()), demo=True)
        with patch.object(ui, 'invoke', side_effect=AssertionError('Live call')):
            ui.dispatch('/flow')
            ui.dispatch('1')

    def test_saved_draft_reuses_original_decision_and_provider(self):
        import json
        ui = self.ui({'stage':'ASSESSMENT_APPROVED','approvals':{'assessment':True}},
                     ['continue','draft','guided-draft','functional-parity','v1','unspecified','none'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            saved = Path(root)/'previews'/'guided-draft'
            saved.mkdir(parents=True)
            (saved/'preview-state.json').write_text(json.dumps({'owner':'Owner','reason':'Accepted risk','provider':'claude'}))
            run(ui)
        args = ui.invoke.call_args_list[0].args[0]
        self.assertIn('Accepted risk', args)
        self.assertIn('claude', args)
        self.assertEqual(ui.ask.call_count, 7)

    def test_skip_confirmation_declined_has_no_provider_call(self):
        ui = self.ui({'stage':'ASSESSMENT_APPROVED','approvals':{'assessment':True}},
                     ['continue','draft','guided-draft','Owner','Demo','claude'])
        ui.yes.return_value = False
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        ui.invoke.assert_not_called()

    def test_skip_word_generates_the_draft_package(self):
        ui = self.ui({'stage':'ASSESSMENT_APPROVED','approvals':{'assessment':True},
                      'providers':{'wireframes':'claude'}},
                     ['continue','skip','guided-draft','Owner','Accepted risk','claude',
                      'reimagine','v1','java','none'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        args = ui.invoke.call_args_list[0].args[0]
        self.assertEqual(args[0], 'preview')
        self.assertIn('--skip-validation', args)
        self.assertEqual(ui.invoke.call_args_list[-1].args[0][0], 'package-export')

    def test_short_review_budget_states_the_count_before_raising_it(self):
        first = {'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True}}
        blocked = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'pipeline':{
            'calls':0, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':12, 'remaining_calls':4}}
        ready = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}}
        ui = self.ui(first, ['continue', 'review', 'Owner', 'Cover the paired programs'])
        ui.project.side_effect = [first, first, blocked, ready]
        ui.invoke.side_effect = [2, 0, 0]
        ui.yes.side_effect = [True, True, False]
        lines = self.said(ui)
        run(ui)
        self.assertIn('at least 12 more model calls', '\n'.join(lines))
        self.assertEqual(ui.invoke.call_args_list[1].args[0],
                         ['model-budget', '--max-calls', '12', '--by', 'Owner', '--note', 'Cover the paired programs'])
        self.assertEqual(ui.invoke.call_args_list[2].args[0], ['validate'])

    def test_spent_calls_ask_to_raise_the_cap_above_what_was_used(self):
        first = {'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True}}
        blocked = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'pipeline':{
            'calls':3, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':142, 'remaining_calls':139}}
        ready = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}}
        ui = self.ui(first, ['continue', 'review', 'Satish', 'Need more to run'])
        ui.project.side_effect = [first, first, blocked, ready]
        ui.invoke.side_effect = [2, 0, 0]
        ui.yes.side_effect = [True, True, False]
        lines = self.said(ui)
        run(ui)
        text = '\n'.join(lines)
        self.assertIn('at least 142 more model calls', text)
        self.assertIn('3 model calls were already used', text)
        self.assertEqual(ui.yes.call_args_list[1].args[0], 'Raise the model-call cap to 145 and start the review?')
        self.assertEqual(ui.invoke.call_args_list[1].args[0],
                         ['model-budget', '--max-calls', '145', '--by', 'Satish', '--note', 'Need more to run'])
        self.assertEqual(ui.invoke.call_args_list[2].args[0], ['validate'])

    def test_exhausted_budget_restates_the_remaining_plan_before_asking(self):
        first = {'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True}}
        exhausted = {'stage':'BLOCKED', 'approvals':{'assessment':True}, 'pipeline':{
            'calls':148, 'reason':'Total model call budget exhausted'}}
        blocked = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'pipeline':{
            'calls':148, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':40, 'remaining_calls':0}}
        ready = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}}
        ui = self.ui(first, ['continue', 'review', 'Satish', 'Finish the remaining programs'])
        ui.project.side_effect = [first, first, exhausted, blocked, ready]
        ui.invoke.side_effect = [2, 2, 0, 0]
        ui.yes.side_effect = [True, True, False]
        lines = self.said(ui)
        run(ui)
        self.assertIn('at least 40 more model calls', '\n'.join(lines))
        self.assertEqual(ui.invoke.call_args_list[1].args[0], ['validate'])
        self.assertEqual(ui.yes.call_args_list[1].args[0], 'Raise the model-call cap to 188 and start the review?')
        self.assertEqual(ui.invoke.call_args_list[2].args[0],
                         ['model-budget', '--max-calls', '188', '--by', 'Satish', '--note', 'Finish the remaining programs'])

    def test_a_large_estate_can_raise_the_allowance_past_256(self):
        first = {'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True}, 'settings':{'max_model_calls':256}}
        blocked = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'settings':{'max_model_calls':256}, 'pipeline':{
            'calls':256, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':7, 'remaining_calls':0}}
        ready = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}}
        ui = self.ui(first, ['continue', 'review', 'Satish', 'Finish the remaining programs'])
        ui.project.side_effect = [first, first, blocked, ready]
        ui.invoke.side_effect = [2, 0, 0]
        ui.yes.side_effect = [True, True, False]
        run(ui)
        self.assertEqual(ui.yes.call_args_list[1].args[0], 'Raise the model-call cap to 263 and start the review?')
        self.assertEqual(ui.invoke.call_args_list[1].args[0],
                         ['model-budget', '--max-calls', '263', '--by', 'Satish', '--note', 'Finish the remaining programs'])

    def test_a_raise_that_is_spent_asks_for_the_next_cap(self):
        first = {'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True}}
        blocked = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'pipeline':{
            'calls':200, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':2, 'remaining_calls':0}}
        exhausted = {'stage':'BLOCKED', 'approvals':{'assessment':True}, 'settings':{'max_model_calls':202}, 'pipeline':{
            'calls':202, 'reason':'Total model call budget exhausted'}}
        blocked_again = {'stage':'PREPARATION_PLANNED', 'approvals':{'assessment':True}, 'settings':{'max_model_calls':202}, 'pipeline':{
            'calls':202, 'reason':'Insufficient call budget for remaining checkpoint plan',
            'minimum_additional_calls':2, 'remaining_calls':0}}
        ready = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}}
        ui = self.ui(first, ['continue', 'review', 'Satish', 'Finish synthesis', 'Satish', 'Finish synthesis'])
        ui.project.side_effect = [first, first, blocked, exhausted, blocked_again, ready]
        ui.invoke.side_effect = [2, 0, 2, 2, 0, 0]
        ui.yes.side_effect = [True, True, True, False]
        lines = self.said(ui)
        run(ui)
        self.assertEqual(ui.yes.call_args_list[2].args[0], 'Raise the model-call cap to 204 and start the review?')
        self.assertIn('Checking how many calls', '\n'.join(lines))

    def test_open_questions_are_deferred_before_validation_approval(self):
        waiting = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}, 'decisions':{}}
        approved = {'stage':'VALIDATION_APPROVED', 'approvals':{'assessment':True, 'validation':True}}
        ui = self.ui(waiting, ['continue', 'Satish', 'Deferred for package review'])
        ui.yes.side_effect = [True, True]
        ui.project.side_effect = [waiting, waiting, approved]
        ui.invoke.side_effect = [0, 0, 0, 2]
        with patch('reframe_flow.open_question_ids', return_value=['Q1', 'Q2']):
            run(ui)
        calls = [c.args[0] for c in ui.invoke.call_args_list]
        self.assertEqual(calls[0], ['decide', '--question', 'Q1', '--by', 'Satish',
                                    '--answer', 'Deferred for package review', '--disposition', 'deferred'])
        self.assertEqual(calls[1], ['decide', '--question', 'Q2', '--by', 'Satish',
                                    '--answer', 'Deferred for package review', '--disposition', 'deferred'])
        self.assertEqual(calls[2], ['approve', 'validation'])

    def test_declining_deferral_records_no_approval(self):
        waiting = {'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True}, 'decisions':{}}
        ui = self.ui(waiting, ['continue'])
        ui.yes.return_value = False
        with patch('reframe_flow.open_question_ids', return_value=['Q1']):
            run(ui)
        ui.invoke.assert_not_called()

    def test_review_word_runs_validation(self):
        ui = self.ui({'stage':'ASSESSMENT_APPROVED','approvals':{'assessment':True}}, ['continue','review'])
        ui.yes.return_value = True
        ui.invoke.return_value = 2
        run(ui)
        ui.invoke.assert_called_once_with(['validate'])

    def test_framework_update_is_recorded_before_approval(self):
        stale = {'stage':'AWAITING_ASSESSMENT_APPROVAL','framework_commit':'old','approvals':{}}
        ui = self.ui(stale, ['continue', 'Record the controller update'])
        ui.project.side_effect = [stale, stale]
        ui.yes.return_value = False
        with patch('reframe.git', return_value='new'):
            run(ui)
        self.assertEqual(ui.invoke.call_args_list[0].args[0],
                         ['upgrade-framework', '--note', 'Record the controller update'])
        self.assertEqual(ui.invoke.call_args_list[1].args[0], ['assessment-report'])

    def said(self, ui):
        lines = []
        ui.say.side_effect = lines.append
        return lines

    def test_assessment_review_names_the_html_and_sealed_findings(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root) / 'runs' / 'run-1'
            ui = self.ui({'stage':'AWAITING_ASSESSMENT_APPROVAL', 'active_assessment':'run-1',
                          'assessments':{'run-1':{'directory':str(run_dir)}}}, ['continue'])
            ui.yes.return_value = False
            lines = self.said(ui)
            run(ui)
        text = '\n'.join(lines)
        self.assertIn('Assessment review page — HTML — ' + str(run_dir / 'assessment-review.html'), text)
        self.assertIn('Sealed findings — ZIP, CSV, JSON, and PDF — ' + str(run_dir / 'assessment'), text)
        self.assertIn('Report index — HTML — ' + str(Path(root) / 'reports' / 'index.html'), text)

    def test_validation_review_names_the_html_and_json(self):
        ui = self.ui({'stage':'AWAITING_VALIDATION_APPROVAL', 'approvals':{'assessment':True},
                      'specification':{'path':'preparation/candidate-1.json', 'review_path':'preparation/review-1.json'}},
                     ['continue'])
        ui.yes.return_value = False
        lines = self.said(ui)
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        text = '\n'.join(lines)
        self.assertIn('SME and architect review — HTML — ' + str(Path(root) / 'preparation' / 'review.html'), text)
        self.assertIn('Executor candidate — JSON — ' + str(Path(root) / 'preparation' / 'candidate-1.json'), text)
        self.assertIn('Independent reviewer report — JSON — ' + str(Path(root) / 'preparation' / 'review-1.json'), text)
        ui.invoke.assert_not_called()

    def test_skip_names_the_html_wireframes_and_zip(self):
        ui = self.ui({'stage':'ASSESSMENT_APPROVED', 'approvals':{'assessment':True},
                      'providers':{'wireframes':'claude'}},
                     ['continue', 'skip', 'guided-draft', 'Owner', 'Accepted risk', 'claude',
                      'functional-parity', 'v1', 'unspecified', 'none'])
        ui.yes.return_value = True
        lines = self.said(ui)
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        text = '\n'.join(lines)
        self.assertIn('Wireframes — HTML — ' + str(Path(root) / 'previews' / 'guided-draft' / 'handoff' / 'index.html'), text)
        self.assertIn('AI-DLC draft package — ZIP — ' + str(Path(root) / 'packages' / 'guided-draft' / 'ai-dlc-draft.zip'), text)
        self.assertIn('Handoff records — JSON and Markdown — ', text)

    def test_approved_package_names_the_zip_and_its_folder(self):
        ui = self.ui({'stage':'PACKAGED', 'package':{'path':'packages/package-abc.zip'}},
                     ['continue', 'functional-parity', 'v1', 'java', 'none'])
        lines = self.said(ui)
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        text = '\n'.join(lines)
        self.assertIn('Approved AI-DLC package — ZIP — ' + str(Path(root) / 'packages' / 'package-abc.zip'), text)
        self.assertIn('Package files — HTML, JSON, and Markdown — ' + str(Path(root) / 'packages' / 'package-abc'), text)

    def test_initial_flow_blocker_stays_in_console(self):
        from pilot import Blocked
        ui = Terminal(console=Console(file=io.StringIO()), demo=True)
        ui.session = Mock()
        ui.session.prompt.side_effect = EOFError
        with patch.object(ui, 'dispatch', side_effect=Blocked('Example blocker')):
            self.assertEqual(ui.run(initial='flow'), 0)
