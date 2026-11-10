"""Guided flow routing: no AWS or model calls."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
from reframe_flow import run
from reframe_terminal import Terminal
from rich.console import Console

class GuidedFlowTests(unittest.TestCase):
    def ui(self, project, answers):
        ui = Mock()
        ui.project.return_value = project
        ui.ask.side_effect = answers
        ui.invoke.return_value = 0
        return ui

    def test_skip_never_runs_verification_or_approved_package(self):
        ui = self.ui({'stage':'BLOCKED','approvals':{'assessment':True}},
                     ['continue','draft','guided-draft','Owner','Demo','claude'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        args = ui.invoke.call_args.args[0]
        self.assertEqual(args[0], 'preview')
        self.assertIn('--skip-validation', args)
        self.assertEqual(ui.invoke.call_count, 1)

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
        ui = self.ui(None, [])
        ui.invoke.return_value = 2
        run(ui)
        ui.invoke.assert_called_once_with(['init','--guided'])

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
                     ['continue','draft','guided-draft'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            saved = Path(root)/'previews'/'guided-draft'
            saved.mkdir(parents=True)
            (saved/'preview-state.json').write_text(json.dumps({'owner':'Owner','reason':'Accepted risk','provider':'claude'}))
            run(ui)
        args = ui.invoke.call_args.args[0]
        self.assertIn('Accepted risk', args)
        self.assertIn('claude', args)
        self.assertEqual(ui.ask.call_count, 3)

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
                     ['continue','skip','guided-draft','Owner','Accepted risk','claude'])
        ui.yes.return_value = True
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        args = ui.invoke.call_args.args[0]
        self.assertEqual(args[0], 'preview')
        self.assertIn('--skip-validation', args)

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
                     ['continue', 'skip', 'guided-draft', 'Owner', 'Accepted risk', 'claude'])
        ui.yes.return_value = True
        lines = self.said(ui)
        with tempfile.TemporaryDirectory() as root, patch('reframe_setup.workspace', return_value=Path(root)):
            run(ui)
        text = '\n'.join(lines)
        self.assertIn('Wireframes — HTML — ' + str(Path(root) / 'previews' / 'guided-draft' / 'handoff' / 'index.html'), text)
        self.assertIn('AI-DLC draft package — ZIP — ' + str(Path(root) / 'packages' / 'guided-draft' / 'ai-dlc-draft.zip'), text)
        self.assertIn('Handoff records — JSON and Markdown — ', text)

    def test_approved_package_names_the_zip_and_its_folder(self):
        ui = self.ui({'stage':'PACKAGED', 'package':{'path':'packages/package-abc.zip'}}, ['continue'])
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
