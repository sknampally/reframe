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
        return Terminal(console=Console(file=self.output,width=width,color_system=None),demo=demo)
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
        invoke.assert_called_once_with(['assess'])
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
