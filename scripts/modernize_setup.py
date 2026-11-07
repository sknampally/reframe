"""Interactive onboarding and private active-workspace selection."""
import json
import configparser
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path
from pilot import Blocked, require


def active_file():
    return Path.home() / '.config' / 'mainframe-modernize' / 'active.json'


def select_workspace(work):
    from kiro_workflow import private_save
    path = active_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_save(path, {'workspace': str(work)})


def workspace():
    path = active_file()
    require(path.is_file(), 'Run reframe init first, or select an existing --workspace')
    return Path(json.loads(path.read_text())['workspace']).expanduser().resolve()


def ask(label, default=None):
    value = input(label + (f' [{default}]' if default else '') + ': ').strip()
    result = value or default
    require(result, 'A value is required: ' + label)
    return result


def yes(label, default=False):
    answer = input(label + (' [Y/n]: ' if default else ' [y/N]: ')).strip().lower()
    require(answer in {'', 'y', 'yes', 'n', 'no'}, 'Answer yes or no')
    return answer in {'y', 'yes'} or (not answer and default)


def optional(label, default=''):
    value = input(label + (f' [{default}]' if default else '') + ': ').strip() or default
    require('\n' not in value and '\r' not in value and not value.startswith(('{','[')),
            'Enter one value, not JSON or multiple lines. No setup operation was requested.')
    return value


def connect_arguments(path):
    candidate = Path(path).expanduser()
    require(candidate.is_file(), 'Enter a path to an existing private configure-arguments file, not pasted JSON.')
    value = json.loads(candidate.read_text())
    require(isinstance(value, dict) and 'mcpServers' not in value and value.get('authMode') in {'sso','cookie','reset'},
            'This is not Transform authentication arguments. MCP client mcpServers configuration is not accepted here.')
    return value


def sso_defaults(project):
    config = configparser.RawConfigParser()
    config.read(Path(os.environ.get('AWS_CONFIG_FILE', str(Path.home()/'.aws/config'))))
    profile = (project['aws'].get('profile') or '').strip()
    require(profile, 'An explicit AWS profile is required; enter it during setup')
    section = 'default' if profile == 'default' else 'profile ' + profile
    session = config.get(section,'sso_session',fallback='')
    section = 'sso-session ' + session if session else section
    return config.get(section,'sso_start_url',fallback=''), config.get(section,'sso_region',fallback='')


def connect_transform(work, project, client=None):
    import kiro_workflow as flow
    client = client or flow.Transform(project['settings']['transform'], work)
    print('Checking the AWS Transform application connection...')
    status = client.calls([('get_status', {})])[0]
    connection = status.get('connection', {})
    if connection.get('configured') is True and not connection.get('error'):
        print('AWS Transform connection verified.')
        return client
    require(not connection.get('error'), 'Transform session verification failed. Retry later; no resource setup has started.')
    print('AWS credentials work, but the Transform application is not connected yet.')
    method = ask('Connection method: sso (browser) or file (advanced)', 'sso')
    require(method in {'sso','file'}, 'Choose sso or file')
    if method == 'file':
        path = optional('Path to private authentication-arguments file (do not paste JSON)')
        arguments = connect_arguments(path)
    else:
        from urllib.parse import urlparse
        start, region = sso_defaults(project)
        start = ask('IAM Identity Center start URL', start or None)
        parsed = urlparse(start)
        require(parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password,
                'Enter your HTTPS Identity Center start URL, not JSON or credentials')
        region = ask('Identity Center region (may differ from workload region)', region or None)
        require(re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region), 'Enter an AWS region name')
        arguments = {'authMode':'sso','startUrl':start,'idcRegion':region}
        profile = optional('Transform application profile name (Enter selects automatically when unambiguous)')
        if profile: arguments['profileName'] = profile
    if arguments.get('authMode') == 'sso':
        require(yes('Open the AWS Transform SSO browser sign-in now?', default=True), 'Transform connection deferred; rerun /init')
    client.calls([('configure', arguments)])
    status = client.calls([('get_status', {})])[0]
    connection = status.get('connection', {})
    require(connection.get('configured') is True and not connection.get('error'),
            'Transform connection is not verified. Resolve application/profile access and rerun /init.')
    print('AWS Transform connection verified after sign-in.')
    return client


def command_ok(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def installation_hint(tool):
    urls = {
        'git': 'https://git-scm.com/downloads',
        'aws': 'https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html',
        'uv': 'https://docs.astral.sh/uv/getting-started/installation/',
        'uvx': 'https://docs.astral.sh/uv/getting-started/installation/',
        'claude': 'https://code.claude.com/docs/en/setup',
        'codex': 'https://learn.chatgpt.com/docs/codex/cli',
        'kiro-cli': 'https://kiro.dev/docs/cli/',
    }
    return tool + ': ' + urls.get(tool, 'Install your configured launcher through its approved vendor distribution')


def halt(message, hint):
    error = Blocked(message)
    error.hint = hint
    raise error


def require_terraform():
    require(shutil.which('terraform'), 'Install Terraform 1.5 or newer, then choose guided start again.')
    result = subprocess.run(['terraform', 'version', '-json'], capture_output=True, text=True)
    version = ''
    if result.returncode == 0 and result.stdout.strip().startswith('{'):
        version = json.loads(result.stdout).get('terraform_version', '')
    parts = []
    for piece in version.split('.'):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    require(len(parts) >= 2 and parts[:2] >= [1, 5],
            'Install Terraform 1.5 or newer, then choose guided start again. An older terraform is first on PATH.')


def sandbox_outputs(profile, region, apply):
    """Create or read the sandbox. The role ARN stays in memory for connector activation."""
    import modernize as app
    require_terraform()
    require(re.fullmatch(r'[A-Za-z0-9_.=,@-]+', profile or ''), 'Enter the AWS profile name only.')
    require(re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region or ''), 'Enter an AWS region name.')
    directory = app.FRAMEWORK / 'infrastructure'
    require((directory / 'main.tf').is_file(), 'The sandbox stack is not in this framework checkout.')
    env = os.environ.copy()
    env.update(AWS_PROFILE=profile, AWS_DEFAULT_REGION=region, TF_IN_AUTOMATION='1', TF_INPUT='0')
    def run(args, capture=False):
        print('Running terraform ' + args[0] + '...')
        result = subprocess.run(['terraform', '-chdir=' + str(directory), *args], env=env, text=True, capture_output=capture)
        if result.returncode != 0:
            halt('Terraform ' + args[0] + ' did not finish. Setup stopped before an assessment.',
                 'Check Terraform 1.5 or newer and the selected AWS profile, then choose 1 again. /privacy shows the local detail.')
        return result
    if apply:
        run(['init', '-input=false'])
        run(['apply', '-input=false', '-auto-approve', '-var', 'aws_region=' + region, '-var', 'aws_profile=' + profile])
    result = subprocess.run(['terraform', '-chdir=' + str(directory), 'output', '-json'], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        require(not apply, 'Terraform outputs were not available. Setup stopped before an assessment.')
        return sandbox_outputs(profile, region, True)
    outputs = json.loads(result.stdout)
    prefix = outputs['upload_prefix']['value']
    role = outputs['sandbox_role']['value']
    role_arn = outputs['sandbox_role_arn']['value']
    require(isinstance(prefix, str) and prefix.startswith('s3://') and prefix.endswith('/reframe'),
            'The sandbox upload prefix is not the reframe prefix.')
    bucket = prefix.removeprefix('s3://').removesuffix('/reframe')
    require(bucket and '/' not in bucket and isinstance(role, str) and role and isinstance(role_arn, str) and role_arn,
            'Terraform outputs were incomplete. Setup stopped before an assessment.')
    return bucket, prefix, role, role_arn


def confirm_production_deny(bucket):
    import modernize as app
    require(re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket or ''),
            'Enter the production bucket name only. Setup stopped before an assessment.')
    script = app.FRAMEWORK / 'infrastructure' / 'deny-check.sh'
    print('Checking that the sandbox role cannot read the production bucket...')
    result = subprocess.run([str(script), bucket], capture_output=True, text=True)
    if result.returncode != 0:
        halt('The sandbox role was not denied for that production bucket. Setup stopped before a Transform workspace was created.',
             'Name a production bucket this role must not read, then choose 1 again. /privacy shows the local detail.')
    print('Production read was denied.')


def choose_aws_profile():
    """A new setup must never select an AWS account implicitly."""
    print('Enter your AWS profile name explicitly (no default). To list profiles, run aws configure list-profiles in another terminal.')
    while True:
        value = optional('AWS profile (required; no default)').strip()
        if value:
            return value
        print('AWS profile is required. Enter a configured profile name, or Ctrl+C to cancel.')


def ensure_aws_login(project):
    try:
        return _ensure_aws_login(project)
    except Blocked:
        print('AWS access was not verified; no assessment was started by this check. Select the intended profile and complete its login. Use /flow to retry new setup, or /init for saved setup. /privacy reveals the detailed local error.')
        raise


def _ensure_aws_login(project):
    """Check credentials, optionally complete SSO, then prove access with STS."""
    require(shutil.which('aws'), 'Install AWS CLI v2 before continuing: ' + installation_hint('aws'))
    profile = (project['aws'].get('profile') or '').strip()
    require(profile, 'An explicit AWS profile is required; enter it during setup')
    aws = ['aws', '--profile', profile, '--no-cli-pager']
    if project['aws'].get('region'): aws += ['--region', project['aws']['region']]
    verify = aws + ['--cli-connect-timeout', '5', '--cli-read-timeout', '10', 'sts', 'get-caller-identity']
    print('Checking AWS access for the selected profile...')
    if command_ok(verify):
        print('AWS profile access verified.')
        return
    require(sys.stdin.isatty(), 'AWS authentication is required. Sign in to the saved profile, then rerun reframe init.')
    configured_sso = any(command_ok(aws + ['configure', 'get', key]) for key in ('sso_session', 'sso_start_url'))
    if not configured_sso:
        print('This profile has no detected SSO configuration. Existing non-SSO profiles may need their approved credential refresh process.')
        require(yes('Configure this profile for IAM Identity Center (AWS SSO)?'),
                'AWS profile needs authentication. Use your approved login process, then rerun reframe init.')
        result = subprocess.run(aws + ['configure', 'sso'], timeout=600)
        require(result.returncode == 0, 'AWS SSO profile configuration did not finish. Rerun reframe init to retry.')
    print('AWS will handle authentication in its own browser/terminal flow; Reframe does not collect credentials.')
    require(yes('Open AWS SSO login for the selected profile now?', default=True),
            'AWS login deferred. Rerun reframe init when ready.')
    try:
        result = subprocess.run(aws + ['sso', 'login'], timeout=600)
    except subprocess.TimeoutExpired as error:
        raise Blocked('AWS login timed out. Rerun reframe init to retry.') from error
    require(result.returncode == 0, 'AWS SSO login did not finish successfully. Rerun reframe init to retry.')
    require(command_ok(verify), 'AWS login returned, but profile access is still unverified. Check account/role access before continuing.')
    print('AWS profile access verified after sign-in.')


def checks(project, work=None):
    """No model calls; authentication diagnostics are not printed or saved."""
    tools = ['git', 'aws', 'claude', 'codex' if project['providers']['wireframes'] == 'codex' else 'claude']
    tools.append(project.get('settings', {}).get('transform', {}).get('command', ['uvx'])[0])
    if project['providers']['executor'] == 'kiro': tools.append('kiro-cli')
    elif project['providers']['executor'] == 'codex': tools.append('codex')
    missing = sorted({tool for tool in tools if not shutil.which(tool)})
    require(not missing, 'Install missing tools, reopen your terminal, then rerun reframe init: ' + ', '.join(missing) + '\n' + '\n'.join(installation_hint(tool) for tool in missing) +
            '\nFull workstation guide: docs/engineer-setup.md in the framework checkout')
    from kiro_workflow import mcp_transport
    mcp_transport(work)
    require(command_ok(['claude', 'auth', 'status']), 'Run claude auth login, then rerun reframe init. See docs/engineer-setup.md for installation and authentication.')
    ensure_aws_login(project)
    if project['providers']['executor'] == 'kiro':
        import os
        require(os.environ.get('KIRO_API_KEY'), 'Configure Kiro headless authentication, then rerun reframe init')
    if 'codex' in tools:
        require(command_ok(['codex', 'login', 'status']), 'Run codex login, then rerun reframe init')
    print('Required CLIs and saved AWS/Claude authentication checks passed.')


def guided_init(args):
    import modernize as app
    require(sys.stdin.isatty(), 'Interactive setup needs a terminal; supply --workspace, --source and --scope for scripted init')
    print('Reframe setup. A new project creates the sandbox, checks the production deny, and connects Transform.')
    print('No assessment starts until you confirm an upload. Workstation guide: ' + str(app.FRAMEWORK / 'docs/engineer-setup.md'))
    from modernize_workspace import default_parent, output_directory
    default = args.workspace
    parent = getattr(args, 'output_parent', None)
    if not default and not parent and not args.source and active_file().exists(): default = str(workspace())
    if not default:
        args.source = args.source or ask('Local Git checkout or GitHub repository URL', 'https://github.com/aws-samples/aws-mainframe-modernization-carddemo.git')
        parent = parent or ask('Engineer workspace directory (assessment folder is created inside it)', str(default_parent(args.source, app.FRAMEWORK)))
        default = str(output_directory(parent, args.source))
        print('Assessment output folder: ' + default)
    work = Path(ask('Assessment output directory', default)).expanduser().resolve()
    require(not work.is_relative_to(app.FRAMEWORK), 'Choose a private workspace outside the framework checkout')
    args.workspace = str(work)
    sandbox_arn = None
    if (work / 'project.json').exists():
        print('Resuming setup for the saved source and configuration; existing evidence will be preserved.')
    else:
        args.source = args.source or ask('Local Git checkout or GitHub repository URL', 'https://github.com/aws-samples/aws-mainframe-modernization-carddemo.git')
        if args.source.startswith('https://'):
            value = optional('Git commit/ref to pin (Enter uses current default branch)')
            args.ref = args.ref or value or None
        args.scope = args.scope or ask('Business journey / assessment scope', 'Assess the entire supplied repository')
        print('Choose an AWS profile. Setup can guide SSO configuration/login when access needs refreshing.')
        args.aws_profile = (args.aws_profile or '').strip() or choose_aws_profile()
        args.region = args.region or ask('AWS region', 'us-east-1')
        ensure_aws_login({'aws': {'profile': args.aws_profile, 'region': args.region}})
        args.executor = ask('Executor runtime: claude, kiro or codex', args.executor)
        require(args.executor in {'claude', 'kiro', 'codex'}, 'Unsupported executor runtime')
        args.ui_provider = ask('HTML content provider: claude or codex', args.ui_provider)
        require(args.ui_provider in {'claude', 'codex'}, 'Unsupported HTML provider')
        print('Claude defaults: Sonnet 5.5 executor / Opus 5.5 reviewer. Kiro is optional.')
        print('Reframe will save this source pin, create the private sandbox, and store its role. You do not name the bucket.')
        if not yes('Continue and finish setup?', default=True):
            print('Setup paused before anything was created. Choose 1 again to continue.')
            return {'status': 'SETUP_INCOMPLETE', 'next_step': 'Choose 1 again. Nothing was created.'}
        bucket, prefix, role, sandbox_arn = sandbox_outputs(args.aws_profile, args.region, True)
        confirm_production_deny(ask('Production bucket the sandbox role must not read'))
        args.bucket = bucket
        app.initialize(args, work)
        project = app.read(work / 'project.json')
        project['settings'].setdefault('transform', {})
        project['settings']['transform']['upload_prefix'] = prefix
        project['settings']['transform']['sandbox_role'] = role
        project['aws']['bucket'] = bucket
        project.setdefault('setup', {})['production_deny_checked'] = True
        app.persist(work, project)
    select_workspace(work)
    # From here all writes and remote setup share the regular project lock.
    with app.locked(work) as project:
        refresh_unstarted_baseline(work, project)
        checks(project, work)
        for key, variable in (('profile', 'AWS_PROFILE'), ('region', 'AWS_DEFAULT_REGION')):
            if project['aws'].get(key): os.environ[variable] = project['aws'][key]
        if project.get('assessments') or project['setup'].get('connector_approved_by'):
            print('Existing assessment setup retained.')
            return {'workspace': str(work), 'status': project['stage'], 'next_step': next_step(project)}
        print('Connecting Transform and activating the sandbox connector. No assessment job starts.')
        transform = project.setdefault('settings', {}).setdefault('transform', {})
        ready = bool(transform.get('upload_prefix') and transform.get('sandbox_role') and project.get('setup', {}).get('production_deny_checked'))
        if not sandbox_arn:
            bucket, prefix, role, sandbox_arn = sandbox_outputs(project['aws']['profile'], project['aws']['region'], not ready)
            if not project.get('setup', {}).get('production_deny_checked'):
                confirm_production_deny(ask('Production bucket the sandbox role must not read'))
                project.setdefault('setup', {})['production_deny_checked'] = True
            transform['upload_prefix'] = prefix
            transform['sandbox_role'] = role
            project['aws']['bucket'] = bucket
            app.persist(work, project)
        client = connect_transform(work, project)
        wid = transform.get('workspace_id') or None
        cid = project['setup'].get('connector_id') or None
        setup = app.parser().parse_args(['setup'])
        setup.connect = None
        setup.create_bucket = False
        setup.attach_workspace = wid or None
        setup.attach_connector = cid or None
        setup.create_workspace = None if wid else 'reframe-sandbox'
        setup.create_connector = None if cid else 'reframe-source'
        setup.role_arn = sandbox_arn
        setup.connector_approved = ask('Your name, recorded on the connector confirmation')
        try:
            result = app.setup(work, project, setup, client)
            return {'workspace': str(work), 'status': result['status'], 'next_step': next_step(project)}
        finally:
            app.persist(work, project)


def refresh_unstarted_baseline(work, project):
    import modernize as app
    previous = project.get('framework_commit')
    if not previous: return
    current = app.git(app.FRAMEWORK, 'rev-parse', 'HEAD')
    if previous == current:
        return
    require(not project.get('assessments') and not project.get('approvals') and not project.get('pipeline'),
            'An assessment/evidence baseline exists. Use its pinned framework checkout or initialize a new private workspace.')
    require(not app.git(app.FRAMEWORK, 'status', '--porcelain'), 'Framework checkout must be clean')
    if project['source'].get('kind') == 's3-zip':
        source = Path(project['source']['root'])
        require(app.git(source, 'write-tree') == project['source']['commit']
                and not app.git(source, 'diff-files', '--name-only')
                and not app.git(source, 'ls-files', '--others'), 'Pinned S3 source changed')
    else:
        require(app.git(Path(project['source']['root']), 'rev-parse', 'HEAD') == project['source']['commit'], 'Pinned source changed')
        require(not app.git(Path(project['source']['root']), 'status', '--porcelain'), 'Source checkout must be clean')
    print('The framework was updated after initial setup; no assessments or approvals exist yet.')
    require(yes('Use the current framework revision for this not-yet-started experiment?'),
            'Framework revision unchanged. Use the earlier checkout or initialize a new workspace.')
    project['framework_commit'] = current
    project.setdefault('events', []).append({'event':'unstarted_framework_baseline_refreshed','previous':previous,'current':current})
    app.persist(work, project)


def next_step(project):
    if project['stage'] == 'PROVIDER_REVIEW_REQUIRED':
        return 'Review the private provider refusal and request content; automatic model retries are stopped.'
    stage = project['stage']
    if stage == 'PREPARATION_PLANNED': return 'Review reframe prepare-plan and the call budget, then reframe validate'
    if project.get('pending_action'): return 'reframe resume'
    if stage == 'PACKAGED': return 'Inspect the generated package and its deferred work boundaries.'
    if stage == 'SPECIFICATIONS_APPROVED': return 'reframe package'
    if stage == 'AWAITING_SPECIFICATIONS_APPROVAL': return 'Review the HTML, then reframe approve specifications'
    if stage == 'VALIDATION_APPROVED': return 'reframe review-site'
    if stage == 'AWAITING_VALIDATION_APPROVAL': return 'Record SME decisions, then reframe approve validation'
    if stage in {'ASSESSMENT_APPROVED', 'REVISION_PENDING', 'WAITING_FOR_RUNTIME'}: return 'reframe validate --reviewer claude-code'
    if stage == 'AWAITING_ASSESSMENT_APPROVAL': return 'Inspect findings, then reframe approve assessment'
    if project.get('active_assessment') and project.get('assessments') and stage in {'INITIALIZED', 'READY_TO_ASSESS'}:
        return 'reframe status or reframe resume'
    if stage in {'INITIALIZED', 'WAITING_FOR_CONNECTOR_APPROVAL'}: return 'reframe init'
    return 'reframe assess' if not project['assessments'] else 'reframe status or reframe resume'


def prepare_command(args, work, project):
    """Resolve saved defaults; never interpret a missing resume label as a new run."""
    import modernize as app
    if args.command in {'assess', 'resume'}:
        if getattr(args, 'source', None):
            source = args.source
            matches = source.rstrip('/').removesuffix('.git') == project['source']['repository'].rstrip('/').removesuffix('.git')
            if not source.startswith(('https://', 's3://')): matches = str(Path(source).expanduser().resolve()) == project['source']['root']
            require(matches, 'Source differs from setup. Use reframe init with a new workspace to pin different source')
        if not args.label:
            if args.command == 'resume':
                require(project.get('active_assessment'), 'No active assessment to resume; use reframe assess')
                args.label = project['active_assessment']
            else:
                args.label = 'run-' + str(len(project['assessments']) + 1)
        if args.command == 'assess' and args.label not in project['assessments']:
            if not args.findings and not args.allow_new_assessment:
                require(sys.stdin.isatty(), 'New assessment needs --allow-new-assessment in noninteractive mode')
                print('Source: ' + project['source']['repository'] + ' @ ' + project['source'].get('commit', 'pinned revision'))
                print('Scope: ' + project.get('scope', 'saved scope'))
                print('Destination bucket: ' + str(project.get('aws', {}).get('bucket', 'saved bucket')))
                args.allow_new_assessment = yes('Upload this source and start a new AWS assessment?')
            project['active_assessment'] = args.label
            app.persist(work, project)
    if args.command == 'approve':
        args.label = args.label or project.get('active_assessment')
        if not args.by or not args.note:
            require(sys.stdin.isatty(), 'Noninteractive approval requires --by and --note')
            args.by = args.by or ask('Your name / accountable approval owner')
            args.note = args.note or ask('Approval rationale')
        if args.phase == 'assessment' and not args.confirm_source_match:
            require(sys.stdin.isatty(), 'Confirm assessment provenance with --confirm-source-match')
            args.confirm_source_match = yes('Have you inspected ingestion and confirmed findings match the pinned source and scope?')
