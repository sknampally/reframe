"""Terminal navigation tests; never invoke AWS or live models."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from rich.console import Console
from reframe_terminal import Terminal, clean
from pilot import Blocked

class TerminalTests(unittest.TestCase):
    def ui(self,demo=False,width=90):
        self.output=io.StringIO()
        # Height is set with width so a TERM=dumb console still uses the requested size.
        return Terminal(console=Console(file=self.output,width=width,height=24,color_system=None),demo=demo)
    def test_no_workspace_displays_welcome(self):
        ui=self.ui()
        with patch('reframe_setup.workspace',side_effect=Blocked('No project')):ui.dashboard()
        self.assertIn('Welcome',self.output.getvalue())
        self.assertIn('Workspace menu',self.output.getvalue())
    def test_preview_has_explicit_label_and_no_backend_calls(self):
        ui=self.ui(True)
        with patch.object(ui,'invoke',side_effect=AssertionError('No live invocation')):
            ui.dashboard()
            for action in ['/assess','/approve assessment','/package','/init','/workspace','/next']:ui.dispatch(action)
        self.assertIn('simulated data',self.output.getvalue())
        self.assertEqual(ui.demo_index,1)
    def test_preview_does_not_read_active_project(self):
        ui=self.ui(True)
        with patch('reframe_setup.workspace',side_effect=AssertionError('No real workspace read')):ui.dashboard()
    def test_result_privacy_masks_error_paths(self):
        ui=self.ui()
        ui.report({'status':'BLOCKED','reason':'secret bucket/path','next_step':'reframe init'})
        self.assertNotIn('secret bucket/path',self.output.getvalue())
        self.assertIn('reframe init',self.output.getvalue())
    def test_result_visibility_can_be_enabled(self):
        ui=self.ui();ui.private=False
        ui.report({'status':'BLOCKED','reason':'Example failure'})
        self.assertIn('Example failure',self.output.getvalue())
    def test_unknown_commands_never_execute(self):
        ui=self.ui()
        with patch.object(ui,'invoke',side_effect=AssertionError('No invocation')):ui.dispatch('rm -rf example')
        self.assertIn('Unknown command',self.output.getvalue())
    def test_menu_dispatch_keeps_existing_controller_gate(self):
        ui=self.ui()
        with patch.object(ui,'invoke') as invoke:ui.dispatch('2')
        invoke.assert_called_once_with(['assess','--wait'])
    def test_resume_stays_with_the_aws_job(self):
        ui=self.ui()
        with patch.object(ui,'invoke') as invoke:ui.dispatch('4')
        invoke.assert_called_once_with(['resume','--wait'])
    def test_flags_are_arguments_not_shell_text(self):
        ui=self.ui()
        with patch.object(ui,'invoke') as invoke:ui.dispatch('/feedback --by "Example owner" --note "$(echo injected)"')
        self.assertEqual(invoke.call_args.args[0][-1],'$(echo injected)')
    def test_exit_and_eof(self):
        ui=self.ui(True)
        self.assertFalse(ui.dispatch('/exit'))
        class Session:
            def prompt(self,*a,**kw):raise EOFError
        ui.session=Session()
        self.assertEqual(ui.run(),0)
    def test_console_respects_narrow_terminal(self):
        ui=self.ui(True,width=60);ui.dashboard()
        self.assertTrue(all(len(line)<=60 for line in self.output.getvalue().splitlines()))
    def test_markup_and_controls_are_not_executed(self):
        ui=self.ui();ui.say('[red]literal[/red]\x1b[2J')
        self.assertIn('[red]literal[/red]',self.output.getvalue())
        self.assertNotIn('\x1b',self.output.getvalue())
    def test_help_contains_review_and_approval_commands(self):
        ui=self.ui();ui.dispatch('/help')
        for term in ['approve','decide','review-site','privacy']:self.assertIn(term,self.output.getvalue())
    def test_guide_menu_does_not_dump_entire_file(self):
        ui=self.ui();ui.dispatch('9')
        self.assertIn('/guide setup',self.output.getvalue())
        self.assertLess(len(self.output.getvalue()),1500)
    def test_cancel_initial_setup_returns_to_console(self):
        ui=self.ui(True)
        class Session:
            def prompt(self,*a,**kw):raise EOFError
        ui.session=Session()
        with patch.object(ui,'dispatch',side_effect=KeyboardInterrupt):
            self.assertEqual(ui.run(initial='init'),0)
        self.assertIn('Setup cancelled',self.output.getvalue())

    def test_multiline_paste_is_one_rejected_input(self):
        ui=self.ui()
        with patch('reframe_terminal.PromptSession') as session:
            session.return_value.prompt.return_value='{\n"mcpServers":{}\n}'
            with self.assertRaisesRegex(Blocked,'single value'):ui.ask('Workspace ID')
        session.return_value.prompt.assert_called_once()

    def test_status_shows_the_running_step_without_redrawing_the_menu(self):
        ui=self.ui()
        project={'stage':'READY_TO_ASSESS','scope':'hidden scope','assessments':{'run-4':{}},'settings':{},'providers':{}}
        progress={'active_assessment':'run-4','saved_status':'WAITING_FOR_ASSESSMENT',
                  'reason':'private/path detail','steps':[{'name':'Extract business logic','status':'IN_PROGRESS'}]}
        with patch.object(ui,'project',return_value=project), \
             patch('reframe_setup.workspace',return_value=Path('/fixture')), \
             patch('reframe.saved_progress',return_value=progress), \
             patch('reframe.watcher_owns',return_value=True):
            ui.dispatch('3')
            hidden=self.output.getvalue()
            ui.dispatch('/privacy')
            revealed=self.output.getvalue()
        self.assertIn('Extract business logic',hidden)
        self.assertIn('IN_PROGRESS',hidden)
        self.assertIn('already staying',hidden)
        self.assertNotIn('private/path detail',hidden)
        self.assertNotIn('Workspace menu',hidden)
        self.assertIn('private/path detail',revealed)
        self.assertFalse(ui.private)

    def test_busy_watcher_names_the_current_step(self):
        import json, tempfile
        import reframe
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            run=root/'run'
            run.mkdir()
            (root/'project.json').write_text(json.dumps({'active_assessment':'run-4','assessments':{'run-4':{'directory':str(run)}}}))
            (run/'run.json').write_text(json.dumps({'status':'WAITING_FOR_ASSESSMENT','reason':'saved detail'}))
            (run/'assessment-status.json').write_text(json.dumps({'plan':{'steps':[
                {'stepName':'Analyze code','status':'SUCCEEDED'},
                {'stepName':'Extract business logic','status':'IN_PROGRESS'}]}}))
            progress=reframe.saved_progress(root)
            error=reframe.watcher_busy(root)
        self.assertEqual(progress['steps'][1]['status'],'IN_PROGRESS')
        self.assertIn('Extract business logic',error.hint)
        self.assertIn('Choose 3',error.hint)
        self.assertNotIn('saved detail',error.hint)

    def test_privacy_replays_the_blocked_reason_when_no_project_exists(self):
        ui=self.ui()
        ui.report({'status':'BLOCKED','reason':'The sandbox role could not be assumed for the deny check.','hint':'Choose 1 again.'})
        self.assertNotIn('could not be assumed',self.output.getvalue())
        with patch.object(ui,'project',return_value=None):
            ui.dispatch('/privacy')
        self.assertIn('could not be assumed',self.output.getvalue())
        self.assertFalse(ui.private)

    def test_safe_remediation_visible_with_privacy_enabled(self):
        ui=self.ui()
        ui.report({'status':'BLOCKED','reason':'private/path/diagnostic','hint':'Reinstall using a managed Python'})
        self.assertNotIn('private/path/diagnostic',self.output.getvalue())
        self.assertIn('Reinstall using a managed Python',self.output.getvalue())

    def test_reporter_avoids_json_dump(self):
        import reframe
        results=[]
        code=reframe.main(['--workspace','/nonexistent-fixture','status'],reporter=results.append)
        self.assertEqual(code,2)
        self.assertEqual(results[0]['status'],'BLOCKED')

if __name__=='__main__':unittest.main()
