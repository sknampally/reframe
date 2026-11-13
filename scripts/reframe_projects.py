"""Saved-project index and infrastructure reuse checks.

The index lists workspace paths. Each workspace project.json remains the
authority. This module stores no account number and no secret.
"""
import json
import os
import subprocess
from pathlib import Path

from pilot import Blocked, require


def _config_dir():
    from reframe_setup import config_dir
    return config_dir()


def index_path():
    """Return the private list of workspace folders."""
    return _config_dir() / 'projects.json'


def _load_index():
    path = index_path()
    if not path.is_file():
        return {'workspaces': []}
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {'workspaces': []}
    if not isinstance(value, dict) or not isinstance(value.get('workspaces'), list):
        return {'workspaces': []}
    return value


def _save_index(value):
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def remember_workspace(folder, repository):
    """Remember a workspace path and its repository. No account number is stored."""
    require(bool(str(repository or '').strip()), 'Repository required')
    folder = Path(folder)
    record = {'path': str(folder), 'repository': repository}
    value = _load_index()
    kept = []
    for item in value['workspaces']:
        if not isinstance(item, dict):
            continue
        if item.get('path') == record['path']:
            continue
        kept.append({'path': item.get('path', ''), 'repository': item.get('repository', '')})
    kept.append(record)
    _save_index({'workspaces': kept})
    return record


def matching_workspaces(repository):
    """Return saved folders whose project.json names this repository. Missing folders are dropped."""
    from reframe_optional import canonical_repository
    wanted = canonical_repository(repository)
    value = _load_index()
    kept, matches = [], []
    for item in value['workspaces']:
        if not isinstance(item, dict) or not item.get('path'):
            continue
        folder = Path(item['path'])
        project_file = folder / 'project.json'
        if not project_file.is_file():
            continue
        try:
            project = json.loads(project_file.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        source = (project.get('source') or {}).get('repository') or ''
        entry = {'path': str(folder), 'repository': source or item.get('repository') or ''}
        kept.append(entry)
        found = canonical_repository(source)
        same = found.get('canonical') and found.get('canonical') == wanted.get('canonical')
        if same or (not found.get('canonical') and source == repository):
            matches.append(folder)
    if kept != [{'path': item.get('path', ''), 'repository': item.get('repository', '')} for item in value['workspaces'] if isinstance(item, dict)]:
        _save_index({'workspaces': kept})
    return matches


def load_project(folder):
    """Read one saved project. The caller supplies the folder."""
    return json.loads((Path(folder) / 'project.json').read_text())


def saved_targets(project):
    """Read the fields initialize and guided setup actually save."""
    transform = (project.get('settings') or {}).get('transform') or {}
    setup = project.get('setup') or {}
    aws = project.get('aws') or {}
    role_name = transform.get('sandbox_role') or ''
    return {
        'role_name': role_name,
        'policy_name': (role_name + '-boundary') if role_name else '',
        'workspace_id': transform.get('workspace_id') or '',
        'connector_id': setup.get('connector_id') or '',
        'bucket': aws.get('bucket') or '',
        'region': aws.get('region') or '',
        'profile': aws.get('profile') or '',
        'upload_prefix': transform.get('upload_prefix') or '',
    }


def _names(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _statements(document):
    if not isinstance(document, dict):
        return []
    statements = document.get('Statement') or document.get('statement') or []
    if isinstance(statements, dict):
        return [statements]
    return [item for item in statements if isinstance(item, dict)]


def sandbox_policy_allows(document, bucket):
    """True only when the role policy grants this bucket and denies reads outside it."""
    bucket_arn = 'arn:aws:s3:::' + bucket
    object_arn = bucket_arn + '/*'
    listed = used = denied = False
    for statement in _statements(document):
        effect = statement.get('Effect') or statement.get('effect')
        actions = set(_names(statement.get('Action') or statement.get('action')))
        resources = set(_names(statement.get('Resource') or statement.get('resource')))
        excluded = set(_names(statement.get('NotResource') or statement.get('not_resource')))
        if effect == 'Allow' and {'s3:ListBucket', 's3:GetBucketLocation'} <= actions and bucket_arn in resources:
            listed = True
        if effect == 'Allow' and {'s3:GetObject', 's3:PutObject', 's3:DeleteObject'} <= actions and object_arn in resources:
            used = True
        if effect == 'Deny' and {'s3:GetObject', 's3:ListBucket'} <= actions and {bucket_arn, object_arn} <= excluded:
            denied = True
    return listed and used and denied


def transform_trusts(document, account):
    """True when Transform can assume the role only for this account."""
    for statement in _statements(document):
        effect = statement.get('Effect') or statement.get('effect')
        principal = statement.get('Principal') or statement.get('principal') or {}
        services = _names(principal.get('Service') if isinstance(principal, dict) else None)
        condition = statement.get('Condition') or statement.get('condition') or {}
        equals = condition.get('StringEquals') or {}
        accounts = _names(equals.get('aws:SourceAccount'))
        if effect == 'Allow' and 'transform.amazonaws.com' in services and account in accounts:
            return True
    return False


def _aws_command(saved, args, region):
    command = []
    if saved.get('profile'):
        command += ['--profile', saved['profile']]
    if region:
        command += ['--region', saved['region']]
    return command + list(args)


def collect_infrastructure(project, aws_call, client):
    """Read the caller, the global role, the bucket, and the saved Transform connector."""
    saved = saved_targets(project)
    missing = [name for name in ('role_name', 'policy_name', 'workspace_id', 'connector_id', 'bucket', 'region') if not saved[name]]
    require(not missing, 'Saved infrastructure is incomplete: ' + ', '.join(missing) + '. No new resources were created.')
    identity = aws_call(_aws_command(saved, ['sts', 'get-caller-identity'], True))
    role = aws_call(_aws_command(saved, ['iam', 'get-role', '--role-name', saved['role_name']], False))
    policy = aws_call(_aws_command(saved, ['iam', 'get-role-policy', '--role-name', saved['role_name'],
                                           '--policy-name', saved['policy_name']], False))
    location = aws_call(_aws_command(saved, ['s3api', 'get-bucket-location', '--bucket', saved['bucket']], True))
    account = str(identity.get('Account', ''))
    head = aws_call(_aws_command(saved, ['s3api', 'head-bucket', '--bucket', saved['bucket'],
                                         '--expected-bucket-owner', account], True))
    connector = client.calls([('get_resource', {
        'resource': 'connector', 'workspaceId': saved['workspace_id'], 'connectorId': saved['connector_id'],
    })])[0]
    connection = (connector.get('accountConnection') or {}).get('awsAccountConnection') or {}
    role_record = role.get('Role') or {}
    arn = role_record.get('Arn') or ''
    parts = arn.split(':')
    role_account = parts[4] if arn.startswith('arn:') and len(parts) > 4 else ''
    document = (policy.get('PolicyDocument') or policy.get('RolePolicy') or {})
    if isinstance(document, str):
        document = json.loads(document)
    recorded_workspace = connector.get('workspaceId')
    return {
        'caller_account': account,
        'role_account': role_account,
        'saved_account': str((project.get('aws') or {}).get('account') or ''),
        'role_trusts_transform': transform_trusts(role_record.get('AssumeRolePolicyDocument') or {}, account),
        'role_permissions_include_bucket': sandbox_policy_allows(document, saved['bucket']),
        'iam_lookup_global': True,
        'bucket_region': location.get('LocationConstraint') or 'us-east-1',
        'expected_region': saved['region'],
        'bucket_accessible': isinstance(head, dict) and 'error' not in head,
        'connector_ready': connection.get('status') == 'ACTIVE' and connection.get('accountId') == account and connection.get('roleArn') == arn,
        'workspace_matches': recorded_workspace in (None, '', saved['workspace_id']),
        'profile': saved['profile'],
    }


def infrastructure_problems(observed):
    """Return the reasons a saved sandbox cannot be reused. An empty list means it can."""
    problems = []
    if not observed.get('iam_lookup_global'):
        problems.append('The role was looked up in a region. IAM roles are global.')
    if not observed.get('caller_account') or observed.get('caller_account') != observed.get('role_account'):
        problems.append('The caller account does not own the saved role.')
    if observed.get('saved_account') and observed.get('saved_account') != observed.get('caller_account'):
        problems.append('The caller account differs from the account recorded for this project.')
    if not observed.get('role_trusts_transform'):
        problems.append('The saved role does not trust Transform for this account.')
    if not observed.get('role_permissions_include_bucket'):
        problems.append('The saved role cannot use only the saved bucket.')
    if observed.get('bucket_region') != observed.get('expected_region'):
        problems.append('The bucket is not in the saved region.')
    if not observed.get('bucket_accessible'):
        problems.append('The caller cannot use the saved bucket.')
    if not observed.get('workspace_matches'):
        problems.append('The connector does not belong to the saved Transform workspace.')
    if not observed.get('connector_ready'):
        problems.append('The connector is not active for this account and the saved role.')
    return problems


def verify_saved_infrastructure(project, aws_call=None, client=None):
    """Check the saved sandbox. A failure pauses and creates nothing."""
    if aws_call is None or client is None:
        if os.environ.get('REFRAME_ALLOW_LIVE_AWS') != '1':
            raise Blocked('Live infrastructure probe is disabled. No new resources were created.')
        aws_call = aws_call or _live_aws
        client = client or _live_transform(project)
    observed = collect_infrastructure(project, aws_call, client)
    problems = infrastructure_problems(observed)
    require(not problems, 'Saved infrastructure could not be verified. ' + ' '.join(problems) + ' No new resources were created.')
    return {'status': 'VERIFIED', 'aws_jobs_created': 0}


def _live_aws(command):
    completed = subprocess.run(['aws', *command, '--output', 'json'], capture_output=True, text=True)
    if completed.returncode != 0:
        raise Blocked('Infrastructure probe failed. No new resources were created.')
    if not completed.stdout.strip():
        return {}
    return json.loads(completed.stdout)


def _live_transform(project):
    from kiro_workflow import Transform
    config = ((project.get('settings') or {}).get('transform') or {}).copy()
    config.setdefault('env', {})
    if (project.get('aws') or {}).get('profile'):
        config['env']['AWS_PROFILE'] = project['aws']['profile']
    if (project.get('aws') or {}).get('region'):
        config['env']['AWS_REGION'] = project['aws']['region']
    return Transform(config, Path(project.get('source', {}).get('root') or '.'))


def pin_requested_revision(work, project, ref):
    """Resolve one revision into its own snapshot. The saved baseline, runs, and approvals stay."""
    source = project.get('source') or {}
    root = source.get('root')
    require(root and Path(root).is_dir(), 'Saved source checkout is missing. No assessment was started.')
    require(source.get('commit') and source.get('repository'), 'Saved source pin is missing. No assessment was started.')
    resolved = subprocess.run(['git', '-C', root, 'rev-parse', '--verify', ref + '^{commit}'], capture_output=True, text=True)
    if resolved.returncode != 0:
        raise Blocked('The requested revision could not be resolved. No assessment was started.')
    commit = resolved.stdout.strip()
    require(len(commit) == 40, 'The requested revision did not resolve to a commit. No assessment was started.')
    snapshot = Path(work) / 'revisions' / commit
    if not (snapshot / '.git').exists():
        if snapshot.exists():
            raise Blocked('The revision snapshot is incomplete. No assessment was started.')
        snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cloned = subprocess.run(['git', 'clone', '--no-local', '--no-checkout', '--', root, str(snapshot)], capture_output=True, text=True)
        require(cloned.returncode == 0, 'The revision could not be pinned. No assessment was started.')
        checked = subprocess.run(['git', '-C', str(snapshot), 'checkout', '--detach', commit], capture_output=True, text=True)
        require(checked.returncode == 0, 'The revision could not be pinned. No assessment was started.')
        origin = subprocess.run(['git', '-C', str(snapshot), 'remote', 'set-url', 'origin', source['repository']], capture_output=True, text=True)
        require(origin.returncode == 0, 'The revision could not keep the saved repository. No assessment was started.')
    head = subprocess.run(['git', '-C', str(snapshot), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    require(head.returncode == 0 and head.stdout.strip() == commit, 'Pinned revision does not match the requested commit. No assessment was started.')
    record = {
        'ref': ref, 'commit': commit, 'baseline_commit': source['commit'], 'baseline_root': source['root'],
        'snapshot': str(snapshot), 'approvals': sorted((project.get('approvals') or {}).keys()),
        'assessments': sorted((project.get('assessments') or {}).keys()),
    }
    project.setdefault('revision_history', []).append({key: record[key] for key in ('ref', 'commit', 'baseline_commit', 'approvals', 'assessments')})
    project['pending_revision'] = record
    return record
