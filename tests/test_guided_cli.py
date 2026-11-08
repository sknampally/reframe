"""User-facing command defaults and setup recovery; no cloud/model calls."""
import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import modernize as app
import modernize_setup as guide
from pilot import Blocked

class GuidedTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.work=Path(temp.name)
        mock=patch.object(guide,'active_file',return_value=self.work/'config/active.json')
        mock.start();self.addCleanup(mock.stop)
        self.project={'stage':'READY_TO_ASSESS','assessments':{},'source':{'root':'/fixture/source','repository':'https://github.com/example/source.git'},
            'providers':{'executor':'claude','wireframes':'codex'},'aws':{'profile':'fixture','region':'us-east-1'}}
    def args(self,*v):return app.parser().parse_args(v)
    def test_short_commands_parse(self):
        for argv in [('init',),('assess','--source','https://github.com/example/source.git'),('status',),('resume',),('approve','assessment'),
                     ('validate','--reviewer','claude-code'),('review-site','--provider','codex'),('approve','specifications'),('package',)]:
            self.args(*argv)
    def test_saved_workspace_is_private_and_resolved(self):
        guide.select_workspace(self.work)
        self.assertEqual(guide.workspace(),self.work.resolve())
        self.assertEqual(guide.active_file().stat().st_mode & 0o777,0o600)
    def test_missing_workspace_guides_to_init(self):
        with self.assertRaisesRegex(Blocked,'reframe init'):guide.workspace()
    def test_resume_never_creates_assessment(self):
        with self.assertRaisesRegex(Blocked,'No active'):guide.prepare_command(self.args('resume'),self.work,self.project)
        self.project['active_assessment']='run-1'
        args=self.args('resume');guide.prepare_command(args,self.work,self.project)
        self.assertEqual(args.label,'run-1')
    def test_new_run_number_and_active_pointer(self):
        args=self.args('assess','--allow-new-assessment')
        guide.prepare_command(args,self.work,self.project)
        self.assertEqual(args.label,'run-1')
        self.assertEqual(self.project['active_assessment'],'run-1')
        self.project['assessments']['run-1']={}
        args=self.args('assess','--allow-new-assessment');guide.prepare_command(args,self.work,self.project)
        self.assertEqual(args.label,'run-2')
    def test_changed_source_rejected_before_job(self):
        args=self.args('assess','--source','https://github.com/example/other')
        with self.assertRaisesRegex(Blocked,'Source differs'):guide.prepare_command(args,self.work,self.project)
    def test_approval_cannot_be_inferred_without_terminal(self):
        with patch.object(sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(Blocked,'requires --by'):guide.prepare_command(self.args('approve','assessment'),self.work,self.project)
    def test_interactive_approval_prompts_for_provenance(self):
        self.project['active_assessment']='run-1'
        with patch.object(sys.stdin,'isatty',return_value=True),patch('builtins.input',side_effect=['Fixture owner','Reviewed evidence','yes']):
            args=self.args('approve','assessment');guide.prepare_command(args,self.work,self.project)
        self.assertTrue(args.confirm_source_match)
        self.assertEqual(args.label,'run-1')
    def test_missing_tools_dont_mark_setup_ready(self):
        with patch.object(guide.shutil,'which',return_value=None):
            with self.assertRaisesRegex(Blocked,'Install missing'):guide.checks(self.project)
    def test_auth_diagnostics_not_disclosed(self):
        with patch.object(guide.shutil,'which',return_value='/fixture'),patch.object(guide,'command_ok',return_value=True),patch('sys.stdout',new_callable=io.StringIO) as output:
            guide.checks(self.project)
        self.assertNotIn('Account',output.getvalue())
    def test_guided_setup_resumes_existing_project_and_checks_environment(self):
        project=dict(self.project,setup={'connector_approved_by':'Fixture admin'})
        app.write(self.work/'project.json',project)
        with patch.object(sys.stdin,'isatty',return_value=True),patch('builtins.input',return_value=str(self.work)),patch.object(guide,'checks') as checks:
            result=guide.guided_init(self.args('init'))
        checks.assert_called_once()
        self.assertEqual(result['next_step'],'reframe assess')
    def test_declined_setup_creates_nothing(self):
        answers=['/fixture/source',str(self.work),str(self.work),'Fixture scope','fixture','us-east-1','claude','codex','no']
        with patch.object(sys.stdin,'isatty',return_value=True),patch('builtins.input',side_effect=answers),patch.object(guide,'ensure_aws_login'),patch.object(guide,'sandbox_outputs',side_effect=AssertionError('terraform')):
            result=guide.guided_init(self.args('init'))
        self.assertEqual(result['status'],'SETUP_INCOMPLETE')
        self.assertIn('Nothing was created', result['next_step'])
        self.assertFalse((self.work/'project.json').exists())

    def test_guided_start_finishes_sandbox_without_a_typed_bucket(self):
        project=dict(self.project,setup={},settings={'transform':{}},aws={'profile':'fixture','region':'us-east-1'})
        def initialize(args,work):
            self.assertEqual(args.bucket,'fixture-bucket')
            app.write(work/'project.json',project)
        def setup(work,project,args,client):
            self.assertFalse(args.create_bucket)
            self.assertEqual(args.role_arn,'fixture-role-arn')
            self.assertEqual(args.create_workspace,'reframe-sandbox')
            self.assertEqual(args.connector_approved,'Fixture admin')
            self.assertEqual(project['settings']['transform']['sandbox_role'],'fixture-sandbox-role')
            self.assertEqual(project['settings']['transform']['upload_prefix'],'s3://fixture-bucket/reframe')
            project['setup']['connector_approved_by']=args.connector_approved
            project['stage']='READY_TO_ASSESS'
            return {'status':'READY_TO_ASSESS'}
        answers=['/fixture/source',str(self.work),str(self.work),'Fixture scope','fixture','us-east-1','claude','codex','yes','fixture-production','Fixture admin']
        with patch.object(sys.stdin,'isatty',return_value=True),patch('builtins.input',side_effect=answers), \
             patch.object(app,'initialize',side_effect=initialize),patch.object(guide,'checks'),patch.object(guide,'ensure_aws_login'), \
             patch.object(guide,'sandbox_outputs',return_value=('fixture-bucket','s3://fixture-bucket/reframe','fixture-sandbox-role','fixture-role-arn')) as outputs, \
             patch.object(guide,'confirm_production_deny') as deny,patch.object(guide,'connect_transform',return_value=object()), \
             patch.object(app,'setup',side_effect=setup):
            result=guide.guided_init(self.args('init'))
        outputs.assert_called_once_with('fixture','us-east-1',True)
        deny.assert_called_once_with('fixture-production')
        self.assertEqual(result['status'],'READY_TO_ASSESS')
        self.assertEqual(result['next_step'],'reframe assess')

    def test_old_opentofu_stops_before_apply(self):
        bindir = self.work / 'bin'
        bindir.mkdir()
        binary = bindir / 'tofu'
        binary.write_text('')
        binary.chmod(0o755)
        def run(command, **kwargs):
            self.assertEqual(command[:2], [str(binary), 'version'])
            return argparse.Namespace(returncode=0, stdout='{"terraform_version":"0.11.13","platform":"darwin_amd64"}', stderr='')
        with patch.dict(guide.os.environ, {'PATH': str(bindir)}), patch('platform.system', return_value='Darwin'), patch('platform.machine', return_value='x86_64'), patch.object(guide, 'opentofu_install_directories', return_value=[]), patch.object(guide.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(Blocked, '1.5'):
                guide.sandbox_outputs('fixture', 'us-east-1', True)

    def test_setup_finds_the_native_tofu_without_a_path_change(self):
        cases = [
            ('Darwin', 'arm64', 'darwin_arm64', 'tofu'),
            ('Darwin', 'x86_64', 'darwin_amd64', 'tofu'),
            ('Linux', 'aarch64', 'linux_arm64', 'tofu'),
            ('Windows', 'AMD64', 'windows_amd64', 'tofu.exe'),
        ]
        for system, machine, reported, name in cases:
            with self.subTest(platform=reported):
                other = self.work / reported / 'other'
                native = self.work / reported / 'native'
                other.mkdir(parents=True)
                native.mkdir()
                wrong = other / name
                right = native / name
                wrong.write_text('')
                right.write_text('')
                wrong.chmod(0o755)
                right.chmod(0o755)
                def run(command, **kwargs):
                    self.assertEqual(command[1:3], ['version', '-json'])
                    platform_name = reported if command[0] == str(right) else 'other_arch'
                    return argparse.Namespace(returncode=0, stdout=json.dumps({'terraform_version': '1.12.5', 'platform': platform_name}), stderr='')
                with patch.dict(guide.os.environ, {'PATH': str(other)}), patch('platform.system', return_value=system), patch('platform.machine', return_value=machine), patch.object(guide, 'opentofu_install_directories', return_value=[str(native)]), patch.object(guide.subprocess, 'run', side_effect=run):
                    self.assertEqual(guide.opentofu_binary(), str(right))

    def test_denied_production_read_is_required(self):
        with patch.object(guide, 'opentofu_binary', return_value='tofu'), patch.object(guide.subprocess, 'run', return_value=argparse.Namespace(returncode=1, stdout='listed', stderr='')):
            with self.assertRaisesRegex(Blocked,'not denied') as caught:
                guide.confirm_production_deny('fixture-production')
        self.assertFalse(hasattr(caught.exception,'hint') and 'fixture-production' in caught.exception.hint)
        self.assertNotIn('listed', str(caught.exception))

    def test_resume_routes_interrupted_model_work_without_aws_calls(self):
        project=dict(self.project,active_assessment='run-1',pending_action={'command':'validate'})
        app.write(self.work/'project.json',project)
        with patch.object(app,'pipeline',return_value={'status':'AWAITING_VALIDATION_APPROVAL'}) as pipeline,patch.object(app,'assessment',side_effect=AssertionError('No AWS resume')),patch('builtins.print'):
            result=app.main(['--workspace',str(self.work),'resume'])
        self.assertEqual(result,0)
        pipeline.assert_called_once()
        self.assertNotIn('pending_action',app.read(self.work/'project.json'))

    def test_resume_advances_approved_phases_without_returning_to_aws(self):
        for stage, function in [('ASSESSMENT_APPROVED','pipeline'),('VALIDATION_APPROVED','review_site'),
                                ('SPECIFICATIONS_APPROVED','package')]:
            with self.subTest(stage=stage):
                app.write(self.work/'project.json',dict(self.project,active_assessment='run-1',stage=stage))
                with patch.object(app,function,return_value={'status':'AWAITING_REVIEW'}) as handler, \
                     patch.object(app,'assessment',side_effect=AssertionError('No AWS call after phase approval')):
                    self.assertEqual(app.main(['--workspace',str(self.work),'resume'],reporter=lambda result:None),0)
                handler.assert_called_once()

    def test_ctrl_c_during_first_init_prompt_has_no_traceback(self):
        output=[]
        with patch.object(guide,'guided_init',side_effect=KeyboardInterrupt):
            code=app.main(['init','--guided'],reporter=output.append)
        self.assertEqual(code,130)
        self.assertEqual(output[0]['status'],'CANCELLED')
        self.assertEqual(output[0]['next_step'],'reframe init')
        self.assertFalse((self.work/'project.json').exists())

    def test_ctrl_c_retains_pending_phase_and_consumed_budget(self):
        app.write(self.work/'project.json',self.project)
        def interrupted(work,project,args):
            project['pipeline']={'calls':1}
            raise KeyboardInterrupt
        output=[]
        with patch.object(app,'pipeline',side_effect=interrupted):
            code=app.main(['--workspace',str(self.work),'validate'],reporter=output.append)
        saved=app.read(self.work/'project.json')
        self.assertEqual(code,130)
        self.assertEqual(saved['pipeline']['calls'],1)
        self.assertEqual(saved['pending_action']['command'],'validate')
        self.assertEqual(output[0]['status'],'CANCELLED')

    def test_profile_prompt_has_no_default_and_reprompts_blank(self):
        with patch.object(guide, 'optional', side_effect=['', '  ', 'chosen-profile']) as prompt:
            self.assertEqual(guide.choose_aws_profile(), 'chosen-profile')
        self.assertEqual(prompt.call_count, 3)
        for call in prompt.call_args_list:
            self.assertEqual(call.args, ('AWS profile (required; no default)',))
            self.assertEqual(call.kwargs, {})

    def test_missing_profile_never_uses_default_credentials(self):
        with patch.object(guide.shutil, 'which', return_value='/fixture/aws'), patch.object(guide, 'command_ok') as check:
            with self.assertRaisesRegex(Blocked, 'explicit AWS profile'):
                guide.ensure_aws_login({'aws': {}})
        check.assert_not_called()

    def test_declined_configuration_shows_safe_recovery_instructions(self):
        with patch.object(guide.shutil, 'which', return_value='/fixture/aws'), patch.object(sys.stdin, 'isatty', return_value=True), patch.object(guide, 'command_ok', return_value=False), patch.object(guide, 'yes', return_value=False), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(Blocked):
                guide.ensure_aws_login(self.project)
        self.assertIn('AWS access was not verified', output.getvalue())
        self.assertIn('/privacy', output.getvalue())
        self.assertNotIn('fixture', output.getvalue())

    def test_valid_aws_session_does_not_launch_login(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(guide,'command_ok',return_value=True),patch.object(guide.subprocess,'run') as run:
            guide.ensure_aws_login(self.project)
        run.assert_not_called()

    def test_expired_sso_session_logs_in_and_rechecks_identity(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(sys.stdin,'isatty',return_value=True),patch.object(guide,'command_ok',side_effect=[False,True,True]) as verify,patch.object(guide,'yes',return_value=True),patch.object(guide.subprocess,'run',return_value=argparse.Namespace(returncode=0)) as run:
            guide.ensure_aws_login(self.project)
        command=run.call_args.args[0]
        self.assertEqual(command[-2:],['sso','login'])
        self.assertEqual(command[command.index('--profile')+1],'fixture')
        self.assertEqual(verify.call_args_list[-1].args[0][-2:],['sts','get-caller-identity'])

    def test_declined_login_stops_before_aws_login(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(sys.stdin,'isatty',return_value=True),patch.object(guide,'command_ok',side_effect=[False,True]),patch.object(guide,'yes',return_value=False),patch.object(guide.subprocess,'run') as run:
            with self.assertRaisesRegex(Blocked,'deferred'):guide.ensure_aws_login(self.project)
        run.assert_not_called()

    def test_login_success_without_identity_access_blocks(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(sys.stdin,'isatty',return_value=True),patch.object(guide,'command_ok',side_effect=[False,True,False]),patch.object(guide,'yes',return_value=True),patch.object(guide.subprocess,'run',return_value=argparse.Namespace(returncode=0)):
            with self.assertRaisesRegex(Blocked,'still unverified'):guide.ensure_aws_login(self.project)

    def test_scripted_login_never_prompts_or_opens_browser(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(sys.stdin,'isatty',return_value=False),patch.object(guide,'command_ok',return_value=False),patch.object(guide.subprocess,'run') as run:
            with self.assertRaisesRegex(Blocked,'authentication is required'):guide.ensure_aws_login(self.project)
        run.assert_not_called()

    def test_unconfigured_sso_requires_explicit_configuration_choice(self):
        with patch.object(guide.shutil,'which',return_value='/fixture/aws'),patch.object(sys.stdin,'isatty',return_value=True),patch.object(guide,'command_ok',side_effect=[False,False,False,True]),patch.object(guide,'yes',side_effect=[True,True]),patch.object(guide.subprocess,'run',return_value=argparse.Namespace(returncode=0)) as run:
            guide.ensure_aws_login(self.project)
        self.assertEqual(run.call_args_list[0].args[0][-2:],['configure','sso'])
        self.assertEqual(run.call_args_list[1].args[0][-2:],['sso','login'])

    def test_connected_transform_is_reused_without_reconfiguration(self):
        client=argparse.Namespace(calls=lambda requests: [{'connection':{'configured':True}}])
        with patch.object(guide,'ask',side_effect=AssertionError('No questions needed')):
            self.assertIs(guide.connect_transform(self.work,self.project,client),client)

    def test_transform_sso_configures_and_checks_connection_before_resources(self):
        from unittest.mock import Mock
        client=Mock()
        client.calls.side_effect=[[{'connection':{'configured':False}}],[{}],[{'connection':{'configured':True}}]]
        with patch.object(guide,'ask',side_effect=['sso','https://example.awsapps.com/start','us-east-1']),patch.object(guide,'sso_defaults',return_value=('','')),patch.object(guide,'optional',return_value=''),patch.object(guide,'yes',return_value=True):
            guide.connect_transform(self.work,self.project,client)
        arguments=client.calls.call_args_list[1].args[0][0][1]
        self.assertEqual(arguments,{'authMode':'sso','startUrl':'https://example.awsapps.com/start','idcRegion':'us-east-1'})
        self.assertEqual(client.calls.call_args_list[2].args[0],[('get_status',{})])

    def test_unverified_transform_connection_does_not_advance(self):
        from unittest.mock import Mock
        client=Mock()
        client.calls.side_effect=[[{'connection':{'configured':False}}],[{}],[{'connection':{'configured':False}}]]
        with patch.object(guide,'ask',side_effect=['sso','https://example.awsapps.com/start','us-east-1']),patch.object(guide,'sso_defaults',return_value=('','')),patch.object(guide,'optional',return_value=''),patch.object(guide,'yes',return_value=True):
            with self.assertRaisesRegex(Blocked,'not verified'):guide.connect_transform(self.work,self.project,client)

    def test_mcp_client_configuration_is_not_authentication_arguments(self):
        path=self.work/'client.json';path.write_text('{"mcpServers":{}}')
        with self.assertRaisesRegex(Blocked,'MCP client'):guide.connect_arguments(str(path))
        with self.assertRaisesRegex(Blocked,'path to an existing'):guide.connect_arguments('{')

    def test_multiline_or_json_optional_input_is_rejected(self):
        for value in ['{','["config"]','first\nsecond']:
            with patch('builtins.input',return_value=value):
                with self.assertRaisesRegex(Blocked,'not JSON'):guide.optional('Existing workspace ID')

    def test_unstarted_project_can_refresh_framework_only_after_confirmation(self):
        project=dict(self.project,framework_commit='old')
        project['source']=dict(project['source'],commit='source-revision')
        def git(root,*args):
            if args==('status','--porcelain'):return ''
            return 'new' if root==app.FRAMEWORK else 'source-revision'
        with patch.object(app,'git',side_effect=git),patch.object(guide,'yes',return_value=True):
            guide.refresh_unstarted_baseline(self.work,project)
        self.assertEqual(project['framework_commit'],'new')

    def test_existing_assessment_framework_revision_never_refreshed(self):
        project=dict(self.project,framework_commit='old',assessments={'run-1':{}})
        with patch.object(app,'git',return_value='new'),patch.object(guide,'yes',side_effect=AssertionError('No repinning')):
            with self.assertRaisesRegex(Blocked,'baseline exists'):guide.refresh_unstarted_baseline(self.work,project)
        self.assertEqual(project['framework_commit'],'old')

    def test_dependency_failure_provides_safe_install_hint(self):
        import kiro_workflow as flow
        with patch.dict(sys.modules,{'mcp':None}):
            with self.assertRaises(Blocked) as raised:flow.mcp_transport(self.work)
        self.assertIn('--managed-python',raised.exception.hint)
        saved=app.read(self.work/'mcp-error.json')
        self.assertIn('runtime_architecture',saved)
        self.assertEqual((self.work/'mcp-error.json').stat().st_mode & 0o777,0o600)

    def test_status_advertises_human_gate(self):
        self.project['stage']='AWAITING_ASSESSMENT_APPROVAL'
        self.assertIn('approve assessment',guide.next_step(self.project))

if __name__=='__main__':unittest.main()
