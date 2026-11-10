#!/usr/bin/env python3
"""Resumable Kiro two-role source-validation workflow. No live service is called by import."""
import argparse
import asyncio
import copy
import fcntl
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

from pilot import Blocked, git, require, save, sha, tracked_file

FRAMEWORK = Path(__file__).resolve().parent.parent
DEFAULTS = {
    'kiro_command': ['kiro-cli'], 'executor_model': None, 'reviewer_model': None, 'wireframes_model': None,
    'max_model_calls': 16, 'max_rounds': 2, 'call_timeout_seconds': 600,
    'max_context_bytes': 180000, 'max_read_bytes': 50000,
    'max_assessment_bytes': 250000000, 'max_source_upload_bytes': 100000000,
    'poll_seconds': 60, 'max_polls_per_resume': 120, 'allow_new_assessments': False, 'transform': {},
    'max_model_dollars': 0, 'model_dollars_spent': 0,
    'model_rates': {'executor': 0.02, 'reviewer': 0.05, 'wireframes': 0.02},
}
DENIED_NAMES = {'.env', 'credentials', 'config.json', 'id_rsa', 'id_ed25519'}
TEXT_SUFFIXES = {'.json', '.md', '.txt', '.csv', '.yaml', '.yml'}


def private_save(path, value):
    save(path, value)
    path.chmod(0o600)


def source_ok(run):
    root = Path(run['source']['root'])
    if run['source'].get('kind') == 's3-zip':
        require(git(root, 'write-tree') == run['source']['commit'], 'Pinned source tree changed')
        require(not git(root, 'diff-files', '--name-only') and not git(root, 'ls-files', '--others'),
                'S3 source snapshot changed')
    else:
        require(git(root, 'rev-parse', 'HEAD') == run['source']['commit'], 'Pinned source commit changed')
        require(not git(root, 'status', '--porcelain'), 'Source checkout is not clean')
    require(git(root, 'remote', 'get-url', 'origin') == run['source']['repository'], 'Source origin changed')
    require(git(FRAMEWORK, 'rev-parse', 'HEAD') == run['framework_commit'], 'Framework revision changed')
    require(not git(FRAMEWORK, 'status', '--porcelain'), 'Framework checkout is not clean')
    return root


def safe_name(name):
    p = Path(name)
    return (not p.is_absolute() and '..' not in p.parts and not any(x.startswith('.') for x in p.parts)
            and p.name not in DENIED_NAMES and p.suffix.lower() not in {'.pem', '.key', '.p12', '.pfx'})


def settings(path):
    config = copy.deepcopy(DEFAULTS)
    if path:
        supplied = json.loads(Path(path).read_text())
        require(isinstance(supplied, dict), 'Settings must be an object')
        require(not set(supplied) - set(DEFAULTS), 'Unknown settings keys')
        config.update(supplied)
    for name in ['max_model_calls', 'max_rounds', 'call_timeout_seconds', 'max_context_bytes',
                 'max_read_bytes', 'max_assessment_bytes', 'max_source_upload_bytes', 'poll_seconds', 'max_polls_per_resume']:
        require(type(config[name]) is int and config[name] > 0, 'Positive integer required: ' + name)
    require(config['poll_seconds'] >= 5, 'Poll interval must be at least five seconds')
    require(isinstance(config['kiro_command'], list) and config['kiro_command'] and
            all(isinstance(x, str) and x for x in config['kiro_command']), 'Kiro command must be an argv array')
    require(isinstance(config['transform'], dict), 'Transform settings must be an object')
    require(type(config['allow_new_assessments']) is bool, 'allow_new_assessments must be a boolean')
    require(isinstance(config['max_model_dollars'], (int, float)) and not isinstance(config['max_model_dollars'], bool)
            and config['max_model_dollars'] >= 0, 'Dollar cap is invalid')
    require(isinstance(config['model_dollars_spent'], (int, float)) and not isinstance(config['model_dollars_spent'], bool)
            and config['model_dollars_spent'] >= 0, 'Recorded model spend is invalid')
    require(isinstance(config['model_rates'], dict), 'Model rates must be an object')
    return config


def initialize(args):
    source = Path(args.source).resolve()
    workspace = Path(args.workspace).resolve()
    require(source.is_dir() and workspace.is_dir(), 'Source and workspace must exist')
    require(not workspace.is_relative_to(source) and not workspace.is_relative_to(FRAMEWORK),
            'Run workspace must be outside source and framework checkouts')
    supplied_source = getattr(args, 'source_record', None)
    if not supplied_source or supplied_source.get('kind') != 's3-zip':
        require(not git(source, 'status', '--porcelain'), 'Source checkout is not clean')
    require(not git(FRAMEWORK, 'status', '--porcelain'), 'Framework checkout is not clean')
    config = settings(args.settings)
    identity = 'RUN-' + uuid.uuid4().hex[:16]
    directory = workspace / 'runs' / identity
    directory.mkdir(parents=True, mode=0o700)
    run = {'version': 1, 'run_id': identity, 'framework_commit': git(FRAMEWORK, 'rev-parse', 'HEAD'),
           'source': supplied_source or {'root': str(source), 'repository': git(source, 'remote', 'get-url', 'origin'),
                      'commit': git(source, 'rev-parse', 'HEAD')},
           'objective': args.objective, 'assessment_input': args.assessment,
           'settings': config, 'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
           'status': 'ASSESSMENT_PENDING', 'round': 1, 'role': 'executor', 'model_calls': 0,
           'role_reads': {}, 'contexts': {}, 'events': [], 'job_id': None,
           'create_pending': False, 'assessment_ready': False, 'reason': None}
    private_save(directory / 'run.json', run)
    return directory


def collect_assessment(run, directory, location):
    """Snapshot local/S3 text findings privately. Never extract arbitrary archives."""
    destination = directory / 'assessment'
    destination.mkdir(exist_ok=True, mode=0o700)
    limit = run['settings']['max_assessment_bytes']
    inventory = []
    if location.startswith('s3://'):
        from urllib.parse import urlparse
        parsed = urlparse(location)
        bucket, prefix = parsed.netloc, parsed.path.lstrip('/')
        require(bucket and prefix, 'S3 input must identify an object or nonempty prefix')
        def aws(*args):
            result = subprocess.run(['aws', *args], capture_output=True, text=True, timeout=120)
            require(result.returncode == 0, 'S3 operation failed; inspect AWS credentials/access privately')
            return json.loads(result.stdout) if result.stdout.strip() else {}
        if location.endswith('/'):
            objects = aws('s3api', 'list-objects-v2', '--bucket', bucket, '--prefix', prefix, '--output', 'json').get('Contents', [])
        else:
            metadata = aws('s3api', 'head-object', '--bucket', bucket, '--key', prefix, '--output', 'json')
            objects = [{'Key': prefix, 'Size': metadata['ContentLength']}]
        selected = []
        total = 0
        for obj in objects:
            relative = obj['Key'][len(prefix):] if location.endswith('/') else Path(prefix).name
            if not safe_name(relative) or Path(relative).suffix.lower() not in TEXT_SUFFIXES:
                continue
            total += obj['Size']
            selected.append((obj, relative))
        require(total <= limit, 'Assessment exceeds byte limit; choose a bounded prefix or adjust saved settings')
        for obj, name in selected:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            aws('s3api', 'get-object', '--bucket', bucket, '--key', obj['Key'], str(target))
    else:
        origin = Path(location).expanduser().resolve()
        require(origin.exists(), 'Assessment input does not exist')
        require(not destination.is_relative_to(origin), 'Assessment input must not contain this run directory')
        paths = [origin] if origin.is_file() else sorted(origin.rglob('*'))
        selected = []
        total = 0
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            name = path.name if origin.is_file() else path.relative_to(origin).as_posix()
            require(path.resolve().is_relative_to(origin if origin.is_dir() else origin.parent), 'Assessment path escapes origin')
            if safe_name(name) and path.suffix.lower() in TEXT_SUFFIXES:
                total += path.stat().st_size
                selected.append((path, name))
        require(total <= limit, 'Assessment exceeds byte limit; select a bounded folder or increase saved settings')
        for path, name in selected:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            path.chmod(0o600)
            inventory.append({'path': path.relative_to(destination).as_posix(), 'bytes': path.stat().st_size,
                              'sha256': sha(path)})
    require(inventory, 'No supported assessment text files found; JSON/Markdown/text/CSV/YAML are supported')
    private_save(directory / 'assessment-index.json', inventory)
    run['assessment_index'] = inventory
    run['assessment_ready'] = True
    run['assessment_provenance'] = {'location': location, 'baseline_match': 'UNVERIFIED; agents must inspect provenance'}


class RuntimeUnavailable(Blocked):
    pass


def blocked_cause(error):
    if isinstance(error, Blocked): return error
    for child in getattr(error, 'exceptions', ()):
        found = blocked_cause(child)
        if found is not None: return found
    return None


def mcp_transport(directory=None):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        return ClientSession, StdioServerParameters, stdio_client
    except ImportError as error:
        if directory is not None:
            private_save(Path(directory) / 'mcp-error.json', {'type':type(error).__name__, 'detail':str(error),
                         'python_executable':sys.executable,'runtime_architecture':platform.machine()})
        blocked = Blocked('MCP dependency could not load; the package may be missing or use a different CPU architecture.')
        blocked.hint = 'From the framework checkout run: uv tool install --force --reinstall --managed-python --python 3.12 --editable .'
        raise blocked from error


class Transform:
    """Official AWS MCP SDK transport. Optional dependency imported only when used."""
    def __init__(self, config, directory):
        self.config, self.directory = config, directory

    def calls(self, requests):
        async def timed():
            return await asyncio.wait_for(self._calls(requests), timeout=self.config.get('timeout_seconds', 180))
        try:
            return asyncio.run(timed())
        except Blocked:
            raise
        except Exception as error:
            blocked = blocked_cause(error)
            if blocked is not None: raise blocked from error
            private_save(self.directory / 'mcp-error.json', {'type': type(error).__name__, 'detail': str(error)})
            raise Blocked('AWS MCP transport failed; inspect the private mcp-error.json and reconcile uncertain creation') from error

    async def _calls(self, requests):
        ClientSession, StdioServerParameters, stdio_client = mcp_transport(self.directory)
        command = self.config.get('command', ['uvx', 'awslabs.aws-transform-mcp-server@latest'])
        require(isinstance(command, list) and command, 'MCP command must be an argv array')
        env = dict(os.environ)
        env.update(self.config.get('env', {}))
        write_dir = self.directory / 'transform-artifacts'
        write_dir.mkdir(exist_ok=True, mode=0o700)
        env['AWS_TRANSFORM_MCP_WRITE_DIR'] = str(write_dir)
        parameters = StdioServerParameters(command=command[0], args=command[1:], env=env)
        responses = []
        log_path = self.directory / 'mcp-server.log'
        with log_path.open('a') as errlog:
            log_path.chmod(0o600)
            async with stdio_client(parameters, errlog=errlog) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    definitions = {tool.name: tool for tool in (await session.list_tools()).tools}
                    for name, arguments in requests:
                        require(name in definitions, 'Configured AWS tool unavailable: ' + name)
                        schema = getattr(definitions[name], 'input_schema', None) or getattr(definitions[name], 'inputSchema', {})
                        require(all(key in arguments for key in schema.get('required', [])), 'Required AWS tool arguments missing: ' + name)
                        require(not set(arguments) - set(schema.get('properties', {})), 'Unrecognized AWS arguments; do not guess')
                        result = await session.call_tool(name, arguments)
                        if getattr(result, 'is_error', getattr(result, 'isError', False)):
                            private_save(self.directory / 'mcp-tool-error.json', {'tool':name, 'text':'\n'.join(block.text for block in result.content if getattr(block,'type','')=='text')})
                            error = Blocked('AWS tool rejected the operation; no successful receipt was accepted.')
                            error.hint = 'Inspect the private mcp-tool-error.json diagnostic. Reconcile any uncertain resource creation before retrying.'
                            raise error
                        value = getattr(result, 'structured_content', None) or getattr(result, 'structuredContent', None)
                        if not value:
                            text = '\n'.join(block.text for block in result.content if getattr(block, 'type', '') == 'text')
                            value = json.loads(text)
                        try:
                            responses.append(unwrap_mcp(value))
                        except Blocked:
                            private_save(self.directory / 'mcp-tool-error.json', {'tool': name, 'response': value})
                            raise
        return responses


def unwrap_mcp(value):
    for _ in range(8):
        require(isinstance(value, dict), 'Unsupported AWS tool response')
        require(not value.get('isError') and not value.get('error') and value.get('success') is not False,
                'AWS tool did not succeed; inspect authentication/configuration privately')
        if set(value) == {'result'}:
            value = value['result']
        elif 'content' in value and isinstance(value['content'], list):
            text = '\n'.join(x['text'] for x in value['content'] if x.get('type') == 'text')
            value = json.loads(text)
        elif value.get('success') is True and 'data' in value:
            value = value['data']
        else:
            return value
    raise Blocked('AWS response envelope nesting exceeded supported limit')


def list_assessment_exports(directory, client, identity):
    """Traverse only service-returned output folders belonging to this job."""
    roots = (f"transform-output/{identity['jobId']}/",
             f"AWSTransform/Workspaces/{identity['workspaceId']}/Jobs/{identity['jobId']}/")
    queue, visited = [None], set()
    merged = {'artifacts': [], 'assets': [], 'folders': []}
    while queue:
        prefix = queue.pop(0)
        if prefix in visited:
            continue
        visited.add(prefix)
        require(len(visited) <= 1000, 'Assessment folder traversal exceeds 1000 folders')
        args = {**identity, 'resource':'artifacts'}
        if prefix is not None:
            args['pathPrefix'] = prefix
        listing = client.calls([('load_instructions', identity), ('list_resources', args)])[-1]
        private_save(directory / ('artifact-listing-' + hashlib.sha256(str(prefix).encode()).hexdigest() + '.json'), listing)
        require(not listing.get('nextToken'), 'Artifact listing has unresolved pagination')
        merged['artifacts'].extend(listing.get('artifacts', listing.get('items', [])))
        connector = listing.get('connectorId')
        for asset in listing.get('assets', []):
            require(isinstance(asset, dict) and isinstance(asset.get('assetKey'), str)
                    and asset['assetKey'].startswith(roots) and safe_name(asset['assetKey']),
                    'Returned asset is outside this job output')
            merged['assets'].append({**asset, 'connectorId':connector})
        require(len(merged['artifacts']) + len(merged['assets']) <= 10000, 'Assessment listing exceeds bounded inventory')
        for folder in listing.get('folders', []):
            require(isinstance(folder, str) and folder.startswith(roots) and folder.endswith('/')
                    and safe_name(folder.rstrip('/')), 'Returned folder is outside this job output')
            if folder not in visited and folder not in queue:
                queue.append(folder)
    merged['traversed_folders'] = sorted(p for p in visited if p is not None)
    private_save(directory / 'artifact-listing.json', merged)
    return merged


def export_findings(run, directory, client, identity):
    """Retrieve server-returned artifact IDs/assets; do not guess an export path."""
    target = directory / 'transform-artifacts'
    target.mkdir(exist_ok=True, mode=0o700)
    listing = list_assessment_exports(directory, client, identity)
    listed_bytes = sum(item.get('sizeInBytes', 0) or 0 for item in listing['assets'] + listing['artifacts'])
    require(listed_bytes <= run['settings']['max_assessment_bytes'],
            'Listed assessment export exceeds byte limit; resume with --max-assessment-bytes')
    requests, seen = [], set()
    for item in listing.get('artifacts', listing.get('items', [])):
        if isinstance(item, dict) and item.get('artifactId'):
            key = ('artifact', item['artifactId'])
            if key not in seen:
                seen.add(key)
                requests.append({'resource': 'artifact', 'artifactId': item['artifactId']})
    for item in listing.get('assets', []):
        require(isinstance(item, dict) and item.get('assetKey') and item.get('connectorId'),
                'Asset export lacks identity or connector; findings cannot be sealed')
        key = ('asset', item['assetKey'])
        if key not in seen:
            seen.add(key)
            requests.append({'resource': 'asset', 'assetKey': item['assetKey'], 'connectorId': item['connectorId']})
    require(len(requests) <= 1000, 'More than 1000 top-level artifacts; configure a bounded output_location in saved settings')
    require(requests, 'No downloadable top-level artifacts returned; configure output_location for nested/S3 exports')
    # Bound each transport session; a large export must not share one 180-second timeout.
    expected = []
    for offset in range(0, len(requests), 10):
        calls = [('load_instructions', identity)]
        for index, arguments in enumerate(requests[offset:offset + 10], offset):
            suffix = Path(arguments.get('assetKey', '')).suffix.lower()
            suffix = suffix if suffix in TEXT_SUFFIXES | {'.zip', '.pdf', '.log', '.xml', '.html'} else '.txt'
            filename = f'finding-{index}' + suffix
            path = target / filename
            # Never mistake an earlier failed download for a successful retry.
            if path.exists():
                path.unlink()
            expected.append(path)
            calls.append(('get_resource', {**identity, **arguments, 'savePath': str(target), 'fileName': filename}))
        client.calls(calls)
    limit = run['settings']['max_assessment_bytes']
    total = 0
    for path in expected:
        require(path.is_file() and not path.is_symlink(), 'Artifact download did not produce every expected file')
        total += path.stat().st_size
        require(total <= limit, 'Assessment export exceeds byte limit; resume with --max-assessment-bytes')
        path.chmod(0o600)
    text_root = directory / 'export-text'
    if text_root.exists():
        shutil.rmtree(text_root)
    text_root.mkdir(mode=0o700)
    originals, expanded_total = [], 0
    from reframe_source import extract_archive
    for index, (path, request) in enumerate(zip(expected, requests)):
        record = {'file': path.relative_to(directory).as_posix(), 'source':request,
                  'sha256':sha(path), 'bytes':path.stat().st_size, 'text_files':[]}
        candidates = [path]
        is_zip = zipfile.is_zipfile(path)
        require(path.suffix != '.zip' or is_zip, 'Downloaded ZIP is not a valid archive')
        if is_zip:
            extraction = directory / 'export-expanded' / str(index)
            if extraction.exists():
                shutil.rmtree(extraction)
            extraction.parent.mkdir(exist_ok=True, mode=0o700)
            with zipfile.ZipFile(path) as archive:
                expanded_total += sum(item.file_size for item in archive.infolist())
            require(expanded_total <= limit, 'Expanded assessment exceeds byte limit; resume with --max-assessment-bytes')
            extract_archive(path, extraction, limit)
            candidates = sorted(p for p in extraction.rglob('*') if p.is_file())
        binary_count = 0
        for number, candidate in enumerate(candidates):
            raw = candidate.read_bytes()
            if candidate.suffix.lower() == '.pdf' or raw.startswith(b'%PDF-'):
                binary_count += 1
                continue
            try:
                content = raw.decode('utf-8')
                if b'\0' in raw: raise UnicodeDecodeError('utf-8',raw,0,1,'binary content')
            except UnicodeDecodeError:
                binary_count += 1
                continue
            filename = (path.name if len(candidates) == 1 and candidate == path and path.suffix in TEXT_SUFFIXES
                        else f'finding-{index}-text-{number}.txt')
            (text_root / filename).write_text(content)
            record['text_files'].append({'file':filename, 'member':candidate.name if candidate == path else candidate.relative_to(extraction).as_posix()})
        record['binary_files_not_text_extracted'] = binary_count
        originals.append(record)
    require(any(record['text_files'] for record in originals), 'No readable text in exported assessment')
    private_save(text_root / 'original-artifacts.json', originals)
    private_save(directory / 'artifact-download-index.json', originals)
    collect_assessment(run, directory, str(text_root))
    run['assessment_originals'] = originals
    run['assessment_provenance']['baseline_match'] = 'Job requested for this recorded source archive; ingestion and semantic content remain unverified'


def recorded_job_id(receipt):
    """A receipt written before the call has no job. Only a successful response does."""
    if not isinstance(receipt, dict) or receipt.get('accepted') is False:
        return None
    try:
        job_id = nested_job(receipt).get('jobId')
    except Blocked:
        return None
    return job_id if isinstance(job_id, str) and job_id else None


def nested_job(value):
    for _ in range(5):
        if isinstance(value, dict) and ('jobId' in value or 'status' in value and 'job' not in value):
            return value
        if isinstance(value, dict) and 'job' in value:
            value = value['job']
        elif isinstance(value, dict) and 'data' in value:
            value = value['data']
        else:
            break
    raise Blocked('Job identity/status could not be established from the AWS response')


def select_orchestrator(run, client):
    config = run['settings']['transform']
    if config.get('orchestrator_agent'):
        return config['orchestrator_agent']
    listing = client.calls([('list_resources', {'resource':'agents','agentType':'ORCHESTRATOR_AGENT'})])[0]
    candidates = [item for item in listing.get('items', []) if isinstance(item, dict)
                  and item.get('type') == 'ORCHESTRATOR_AGENT' and item.get('jobOrchestrator') is True
                  and item.get('ownerType') == 'INTERNAL_AGENT' and 'mainframe' in str(item.get('name','')).lower()]
    require(len(candidates) == 1, 'No unique AWS mainframe orchestrator discovered. Select a service-returned orchestrator_agent in private settings before creating a job.')
    agent = candidates[0]['name']
    require(isinstance(agent,str) and agent and all(c.isalnum() or c in '-_' for c in agent), 'Invalid service agent identifier')
    config['orchestrator_agent'] = agent
    run['events'].append({'event':'orchestrator_discovered','name':agent})
    return agent


def plan_confirmation(text):
    """AWS is asking which menu choice builds the assessment plan."""
    lower = text.lower()
    return 'option 3' in lower and 'confirm' in lower and 'analy' in lower


def bootstrap_assessment_plan(run, directory, client, identity, snapshot):
    """Select the agreed capabilities through job chat, never approve a deliverable.

    Only recognized onboarding prompts trigger this adapter. The message is built
    from the pinned run, not from instructions in source files or AWS chat text.
    An uncertain send must be reconciled before another mutation is attempted.
    """
    if snapshot.get('plan', {}).get('steps'):
        return False
    messages = snapshot.get('messages', {}).get('messages', [])
    system = [m for m in messages if m.get('messageOrigin') == 'SYSTEM'
              and m.get('processingInfo', {}).get('messageType') == 'FINAL_RESPONSE']
    if not system:
        return False
    latest = max(system, key=lambda m: str(m.get('createdAt', '')))
    text = latest.get('text', '').lower()
    recognized = (('custom' in text and ('capabilities' in text or 'job plan' in text))
                  or ('please confirm' in text and 'assessment-only' in text)
                  or plan_confirmation(text))
    if not recognized:
        return False
    progress = run.setdefault('plan_bootstrap', {'sent_messages': [], 'attempts': 0})
    message_id = latest.get('messageId')
    require(message_id, 'Plan onboarding response has no message identity; inspect privately')
    if progress.get('pending') and progress.get('pending_attempt') and progress.get('pending_message'):
        receipt = directory / f"plan-bootstrap-{progress['pending_attempt']}-receipt.json"
        if receipt.is_file():
            progress['sent_messages'].append(progress['pending_message'])
            progress.update(pending=False, attempts=progress['pending_attempt'])
            private_save(directory / 'run.json', run)
    if progress.get('pending'):
        run['status'] = 'NEEDS_RECONCILIATION'
        run['reason'] = 'Assessment plan message outcome is uncertain. Reconcile job chat before sending again.'
        return True
    if message_id in progress['sent_messages']:
        return False
    config = run['settings']['transform']
    connector = config.get('connector_id')
    source = run.get('uploaded_source', {}).get('uri')
    require(connector and source, 'Plan setup requires the recorded connector_id and uploaded source URI; do not guess inputs')
    # AWS refuses job, workspace, connector, and archive identifiers in this chat.
    # The recorded upload is submitted later on the resource-location task.
    if plan_confirmation(text):
        if progress.get('confirmation_sent'):
            run['status'] = 'WAITING_FOR_HUMAN'
            run['reason'] = 'AWS plan confirmation did not produce a plan; inspect the private assessment status.'
            return True
        message = (
            'Select option 3. Build the custom plan with only Analyze code, Analyze data, '
            'Generate technical documentation, and Extract business logic. '
            'Do not include Analyze activity metrics. No SMF records and no SCRT file are available. '
            'Do not include reimagine.')
    else:
        if progress['attempts'] >= 2:
            run['status'] = 'WAITING_FOR_HUMAN'
            run['reason'] = 'AWS plan onboarding did not finish after two bounded attempts; inspect the private assessment status.'
            return True
        message = (
            'I want an assessment only. Analyze code. Analyze data. '
            'Generate technical documentation. Extract business logic. '
            'Do not include Analyze activity metrics. No SMF records and no SCRT file are available. '
            'Do not decompose code, plan test cases, reimagine the application, implement it, or deploy it. '
            'This capability selection is confirmed. Generate the plan and proceed. '
            'Scope: ' + run['objective'] + '. '
            'Preserve human approval of the generated findings. This is capability selection, not deliverable approval.')
    require(len(message) <= 7000, 'Assessment scope exceeds AWS job chat limit')
    attempt = progress['attempts'] + 1
    private_save(directory / f'plan-bootstrap-{attempt}-intent.json', {'reply_to': message_id, 'text': message})
    progress.update(pending=True, pending_attempt=attempt, pending_message=message_id)
    private_save(directory / 'run.json', run)
    response = client.calls([('load_instructions', identity),
                             ('send_message', {**identity, 'text': message, 'skipPolling': True})])[-1]
    private_save(directory / f'plan-bootstrap-{attempt}-receipt.json', response)
    progress.update(pending=False, attempts=attempt)
    if plan_confirmation(text):
        progress['confirmation_sent'] = True
    progress['sent_messages'].append(message_id)
    run['status'] = 'WAITING_FOR_ASSESSMENT'
    run['reason'] = 'Assessment-only plan selection sent; resume to check the same job.'
    private_save(directory / 'run.json', run)
    return True


REQUIRED_ASSESSMENT_STEPS = frozenset({
    'Kick off modernization', 'Analyze code', 'Analyze data',
    'Generate technical documentation', 'Extract business logic',
})


def assessment_plan_authorized(steps):
    """The assessment steps this run is allowed to execute."""
    return {step.get('stepName') for step in steps} == REQUIRED_ASSESSMENT_STEPS


def assessment_plan_complete(snapshot):
    """An optional invitation may keep the job open after every assessment step finishes."""
    steps = snapshot.get('plan', {}).get('steps', [])
    return bool(steps) and assessment_plan_authorized(steps) and all(step.get('status') == 'SUCCEEDED' for step in steps)


def assessment_start_offered(text):
    """True when AWS has finished the plan and is asking to run it.

    The wording changes between jobs. A finished plan asks to start, to proceed
    with execution, or what to do next. Match those offers, not one sentence.
    """
    lower = text.lower()
    planned = ('plan generated' in lower or 'plan has been generated' in lower or 'plan is ready' in lower)
    ready = ('ready to execute' in lower or 'ready to start' in lower
             or 'start the execution' in lower or 'what would you like to do next' in lower
             or ('proceed' in lower and 'execut' in lower))
    return planned and ready


def assessment_plan_announced(text):
    """True when AWS has generated the plan and setup should continue.

    The next sentence is not stable. One job asks to start, another asks what
    to do next, and another asks to configure the recorded source. A request to
    approve findings is not this announcement.
    """
    lower = text.lower()
    if 'approve' in lower and 'finding' in lower:
        return False
    return ('plan has been generated' in lower or 'plan generated' in lower
            or 'plan is ready' in lower or assessment_start_offered(text))


def exclude_activity_metrics(run, directory, client, identity, snapshot):
    """Ask AWS to rebuild a not-yet-started plan that includes activity metrics.

    That step needs an SMF file. This source has none, and the task has no way
    to continue without one. One request is sent. The plan is not started
    while the step remains.
    """
    steps = snapshot.get('plan', {}).get('steps', [])
    if not steps or any(step.get('status') != 'NOT_STARTED' for step in steps):
        return False
    if not any(step.get('stepName') == 'Analyze activity metrics' for step in steps):
        return False
    receipt = directory / 'plan-without-activity-metrics-receipt.json'
    intent = directory / 'plan-without-activity-metrics-intent.json'
    if receipt.is_file():
        run['status'] = 'WAITING_FOR_HUMAN'
        run['reason'] = 'AWS kept Analyze activity metrics after one request to remove it. Start a new assessment that excludes that step.'
        return True
    if intent.is_file():
        run['status'] = 'NEEDS_RECONCILIATION'
        run['reason'] = 'The request to remove activity metrics has an uncertain result. Reconcile before sending it again.'
        return True
    message = (
        'Regenerate this assessment plan without Analyze activity metrics. '
        'No SMF records and no SCRT file are available. '
        'Keep Kick off modernization, Analyze code, Analyze data, Generate technical documentation, and Extract business logic. '
        'Do not include reimagine, implementation, or deployment.')
    private_save(intent, {'text': message})
    response = client.calls([('load_instructions', identity),
                             ('send_message', {**identity, 'text': message, 'skipPolling': True})])[-1]
    private_save(receipt, response)
    run['status'] = 'WAITING_FOR_ASSESSMENT'
    run['reason'] = 'Asked AWS to rebuild the plan without activity metrics.'
    private_save(directory / 'run.json', run)
    return True


def source_location_requested(text):
    """True when AWS is waiting for the source archive location."""
    lower = text.lower()
    return 's3' in lower and 'uri' in lower and 'source' in lower


def supply_recorded_source(run, directory, client, identity, snapshot):
    """Give AWS the recorded upload when it asks where the source is.

    The plan chat must not carry workspace, job, or connector identifiers.
    This reply is only the upload already stored for the run, and only after
    AWS asks for that location. The source task form remains the other path.
    """
    steps = snapshot.get('plan', {}).get('steps', [])
    if not steps or any(step.get('status') != 'NOT_STARTED' for step in steps):
        return False
    if not assessment_plan_authorized(steps):
        return False
    messages = [m for m in snapshot.get('messages', {}).get('messages', [])
                if m.get('messageOrigin') == 'SYSTEM'
                and m.get('processingInfo', {}).get('messageType') == 'FINAL_RESPONSE']
    if not messages:
        return False
    latest = max(messages, key=lambda m: str(m.get('createdAt', '')))
    if not source_location_requested(latest.get('text', '')):
        return False
    source = run.get('uploaded_source', {})
    uri = source.get('uri', '')
    require(uri.startswith('s3://') and source.get('sha256'),
            'Source configuration requires a recorded upload URI and checksum')
    archive = directory / 'source.zip'
    require(archive.is_file() and sha(archive) == source['sha256'],
            'Recorded source archive changed or is missing')
    receipt = directory / 'source-chat-receipt.json'
    intent = directory / 'source-chat-intent.json'
    if receipt.is_file():
        run.update(status='WAITING_FOR_ASSESSMENT',
                   reason='Recorded source location sent; waiting for AWS to configure inputs and start the plan.')
        return True
    if intent.is_file():
        run.update(status='NEEDS_RECONCILIATION',
                   reason='Recorded source location outcome is uncertain. Reconcile before sending it again.')
        return True
    message = ('The recorded source archive is ' + uri + '. '
               'There is no SCRT file and there are no SMF records. '
               'Configure the assessment inputs with that archive and start the existing assessment plan. '
               'Execute kickoff, code analysis, data analysis, technical documentation, and business-rule extraction. '
               'Do not create another job, add reimagine, implement, or deploy.')
    private_save(intent, {'reply_to': latest.get('messageId'), 'text': message})
    response = client.calls([('load_instructions', identity),
                             ('send_message', {**identity, 'text': message, 'skipPolling': True})])[-1]
    private_save(receipt, response)
    run.update(status='WAITING_FOR_ASSESSMENT',
               reason='Recorded source location sent; waiting for AWS to configure inputs and start the plan.')
    private_save(directory / 'run.json', run)
    return True


def start_assessment_plan(run, directory, client, identity, snapshot):
    """Start the assessment plan already authorized by this run."""
    steps = snapshot.get('plan', {}).get('steps', [])
    if not steps or any(step.get('status') != 'NOT_STARTED' for step in steps):
        return False
    messages = [m for m in snapshot.get('messages', {}).get('messages', [])
                if m.get('messageOrigin') == 'SYSTEM'
                and m.get('processingInfo', {}).get('messageType') == 'FINAL_RESPONSE']
    if not messages:
        return False
    latest = max(messages, key=lambda m: str(m.get('createdAt', '')))
    if not assessment_plan_announced(latest.get('text', '')):
        return False
    require(run['settings'].get('allow_new_assessments'), 'Assessment execution is not authorized')
    require(assessment_plan_authorized(steps),
            'Generated plan differs from the authorized assessment capabilities; inspect before starting')
    intent = directory / 'plan-execution-intent.json'
    receipt = directory / 'plan-execution-receipt.json'
    if receipt.exists():
        run.update(status='WAITING_FOR_ASSESSMENT', reason='Execution request receipted; waiting for AWS to start the plan.')
        return True
    if intent.exists():
        run.update(status='NEEDS_RECONCILIATION', reason='Execution request outcome uncertain; reconcile before retrying.')
        return True
    message = ('Yes. Configure the assessment inputs from the recorded source archive, then start the existing assessment plan now. '
               'The source archive is already recorded. '
               'There is no SCRT file and there are no SMF records, so do not wait for them. '
               'Execute kickoff, code analysis, data analysis, technical documentation, and business-rule extraction. '
               'Do not create another job, add reimagine, implement, or deploy. '
               'This starts the authorized assessment; final findings remain subject to human review.')
    private_save(intent, {'identity': identity, 'reply_to': latest.get('messageId'), 'text': message,
                          'steps': steps})
    response = client.calls([('load_instructions', identity),
        ('send_message', {**identity, 'text': message, 'skipPolling': True})])[-1]
    private_save(receipt, response)
    run.update(status='WAITING_FOR_ASSESSMENT', reason='Authorized assessment execution requested.')
    return True


def configure_source_task(run, directory, client, identity, tasks):
    """Submit only the recorded upload to the known kickoff location form.

    Starting a new assessment authorizes source configuration, not approvals of
    scope changes or generated deliverables. All other task types remain human.
    """
    candidates = [task for task in tasks if isinstance(task, dict)
                  and task.get('uxComponentId') == 'SpecifyAssetsLocation'
                  and task.get('status') == 'AWAITING_HUMAN_INPUT']
    if not candidates or not run['settings'].get('allow_new_assessments'):
        return False
    require(len(candidates) == 1, 'Multiple source-location tasks; inspect privately rather than guessing')
    task_id = candidates[0].get('taskId')
    require(task_id, 'Source-location task has no identity')
    source = run.get('uploaded_source', {})
    require(source.get('uri', '').startswith('s3://') and source.get('sha256'),
            'Source configuration requires a recorded upload URI and checksum')
    archive = directory / 'source.zip'
    require(archive.is_file() and sha(archive) == source['sha256'], 'Recorded source archive changed or is missing')
    state = run.setdefault('source_task', {})
    if state.get('task_id') == task_id and state.get('receipt'):
        run.update(status='WAITING_FOR_ASSESSMENT', reason='Source submission is receipted; waiting for AWS task status to catch up.')
        return True
    if state.get('pending'):
        receipt = directory / 'source-location-submit-receipt.json'
        if state.get('task_id') == task_id and receipt.is_file():
            state.update(pending=False, receipt=receipt.name)
            run.update(status='WAITING_FOR_ASSESSMENT', reason='Recovered successful source submission; waiting for AWS task status to catch up.')
            private_save(directory / 'run.json', run)
            return True
        run.update(status='NEEDS_RECONCILIATION', reason='Source-location submission outcome uncertain; reconcile the existing task before retrying.')
        return True
    require(not state.get('task_id') or state['task_id'] == task_id,
            'A different source-location task appeared; inspect before changing an existing assessment')
    record = client.calls([('load_instructions', identity),
                           ('get_resource', {**identity, 'resource': 'task', 'taskId': task_id})])[-1]
    private_save(directory / 'source-location-task.json', record)
    task = record.get('task', {})
    schema = task.get('_outputSchema', {})
    metadata = task.get('jobMetadata', {})
    require(task.get('taskId') == task_id and task.get('uxComponentId') == 'SpecifyAssetsLocation'
            and task.get('status') == 'AWAITING_HUMAN_INPUT'
            and task.get('category') == 'REGULAR' and task.get('taskType') == 'NORMAL'
            and all(metadata.get(key) == value for key, value in identity.items()),
            'Source task identity/type/status changed; no response submitted')
    require(schema.get('type') == 'object' and schema.get('required') == ['assetLocation']
            and schema.get('properties', {}).get('assetLocation', {}).get('type') == 'string',
            'Source-location schema changed; no response submitted')
    content = {'assetLocation': source['uri']}
    private_save(directory / 'source-location-submit-intent.json', {'task_id': task_id, 'content': content})
    state.update(task_id=task_id, pending=True)
    private_save(directory / 'run.json', run)
    response = client.calls([('load_instructions', identity), ('complete_task', {
        **identity, 'taskId': task_id, 'content': json.dumps(content), 'action': 'APPROVE'})])[-1]
    receipt = directory / 'source-location-submit-receipt.json'
    private_save(receipt, response)
    state.update(pending=False, receipt=receipt.name)
    run.update(status='WAITING_FOR_ASSESSMENT', reason='Recorded source ZIP supplied to kickoff; resume checks the same job.')
    private_save(directory / 'run.json', run)
    return True


def continue_without_activity_records(run, directory, client, identity, tasks):
    """Decline activity metrics when this source has no SMF or SCRT records.

    Approving the blank form makes AWS try to read a file that was not supplied.
    Declining the step leaves a filled-in path untouched.
    """
    candidates = [task for task in tasks if isinstance(task, dict)
                  and task.get('uxComponentId') == 'MainframeSMFConfigureComponent'
                  and task.get('blockingType') == 'BLOCKING'
                  and task.get('status') in {'AWAITING_HUMAN_INPUT', 'PENDING_HUMAN_INPUT', 'IN_PROGRESS'}]
    if not candidates or run.get('smf_source') or run.get('scrt_source'):
        return False
    require(len(candidates) == 1, 'Multiple activity-metrics tasks; inspect privately rather than guessing')
    task_id = candidates[0].get('taskId')
    require(task_id, 'Activity-metrics task has no identity')
    state = run.setdefault('activity_metrics', {})
    if state.get('task_id') == task_id and state.get('receipt'):
        run.update(status='WAITING_FOR_ASSESSMENT',
                   reason='Activity metrics skipped because no SMF or SCRT records were supplied; waiting for AWS.')
        return True
    if state.get('pending'):
        receipt = directory / 'activity-metrics-submit-receipt.json'
        if state.get('task_id') == task_id and receipt.is_file():
            state.update(pending=False, receipt=receipt.name)
            run.update(status='WAITING_FOR_ASSESSMENT',
                       reason='Recovered activity-metrics confirmation; waiting for AWS.')
            private_save(directory / 'run.json', run)
            return True
        run.update(status='NEEDS_RECONCILIATION',
                   reason='Activity-metrics confirmation outcome is uncertain; reconcile before retrying.')
        return True
    record = client.calls([('load_instructions', identity),
                           ('get_resource', {**identity, 'resource': 'task', 'taskId': task_id})])[-1]
    private_save(directory / 'activity-metrics-task.json', record)
    task = record.get('task', {})
    props = (record.get('agentArtifactContent') or {}).get('properties') or {}
    require(task.get('taskId') == task_id and task.get('uxComponentId') == 'MainframeSMFConfigureComponent'
            and task.get('status') in {'AWAITING_HUMAN_INPUT', 'PENDING_HUMAN_INPUT', 'IN_PROGRESS'}
            and task.get('category') == 'REGULAR' and task.get('taskType') == 'NORMAL'
            and all(task.get('jobMetadata', {}).get(key) == value for key, value in identity.items()),
            'Activity-metrics task identity changed; no response submitted')
    require(props.get('sourceCodeS3Path') == '' and props.get('scrtFileS3Path') == '',
            'Activity metrics already names a file location; inspect before continuing')
    private_save(directory / 'activity-metrics-submit-intent.json',
                 {'task_id': task_id, 'content': {}, 'decision': 'No SMF or SCRT records were supplied'})
    state.update(task_id=task_id, pending=True)
    private_save(directory / 'run.json', run)
    response = client.calls([('load_instructions', identity), ('complete_task', {
        **identity, 'taskId': task_id, 'content': '{}', 'action': 'REJECT'})])[-1]
    receipt = directory / 'activity-metrics-submit-receipt.json'
    private_save(receipt, response)
    state.update(pending=False, receipt=receipt.name)
    run.update(status='WAITING_FOR_ASSESSMENT',
               reason='Activity metrics skipped because no SMF or SCRT records were supplied; waiting for AWS.')
    private_save(directory / 'run.json', run)
    return True


def assessment_step(run, directory, client=None):
    if run['assessment_ready']:
        return
    if run['assessment_input']:
        collect_assessment(run, directory, run['assessment_input'])
        run['status'] = 'AGENTS_PENDING'
        return
    config = run['settings']['transform']
    require(config.get('workspace_id'), 'Configure an existing AWS Transform workspace once in local settings')
    client = client or Transform(config, directory)
    if not run['job_id']:
        receipt_path = directory / 'create-job-receipt.json'
        if receipt_path.is_file():
            job_id = recorded_job_id(json.loads(receipt_path.read_text()))
            if job_id:
                run['job_id'] = job_id
                run['create_pending'] = False
            else:
                run['create_pending'] = True
                run['status'] = 'NEEDS_RECONCILIATION'
                run['reason'] = 'Job creation receipt has no successful response. Attach the existing job; do not create another.'
                return
        if run['create_pending']:
            run['status'] = 'NEEDS_RECONCILIATION'
            run['reason'] = 'Job creation outcome uncertain. Reconcile and attach the existing job; never auto-create a duplicate.'
            return
        if run['job_id']:
            return assessment_step(run, directory, client)
        if not run['settings']['allow_new_assessments']:
            run['status'] = 'WAITING_FOR_AUTHORIZATION'
            run['reason'] = 'Authorize one new assessment at start/resume or enable the saved policy.'
            return
        require(config.get('upload_prefix', '').startswith('s3://'), 'New jobs require a configured private S3 upload prefix')
        client.calls([('get_status', {})])  # Establish transport before upload; service authorization may still fail.
        select_orchestrator(run, client)  # Live service requires this even when MCP marks it optional.
        root = source_ok(run)
        archive = directory / 'source.zip'
        total = 0
        included, excluded = [], []
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name in git(root, 'ls-files').splitlines():
                if not safe_name(name):
                    excluded.append(name)
                    continue
                path = tracked_file(root, name)
                total += path.stat().st_size
                require(total <= run['settings']['max_source_upload_bytes'], 'Source exceeds upload limit')
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                content = path.read_bytes()
                bundle.writestr(info, content)
                included.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest()})
        archive.chmod(0o600)
        private_save(directory / 'source-upload-manifest.json', {'included': included, 'excluded': excluded})
        source_uri = config['upload_prefix'].rstrip('/') + '/' + run['run_id'] + '/source.zip'
        result = subprocess.run(['aws', 's3', 'cp', str(archive), source_uri, '--only-show-errors'],
                                capture_output=True, text=True, timeout=120)
        require(result.returncode == 0, 'Source upload failed; no assessment job was requested')
        run['uploaded_source'] = {'uri': source_uri, 'sha256': sha(archive)}
        if config.get('assessment_policy') == 'full-upload':
            run['assessment_scope_policy'] = {'mode':'ALL_UPLOADED',
                'manifest_sha256':sha(directory / 'source-upload-manifest.json'),
                'archive_sha256':sha(archive), 'findings_approval':'REQUIRED_BEFORE_VALIDATION'}
        arguments = {'workspaceId': config['workspace_id'], 'jobName': run['run_id'],
                     'objective': 'Assess the source archive at ' + source_uri + '. Source commit ' + run['source']['commit'] +
                                  '. Scope: ' + run['objective'] +
                                  '. Produce discovery, documentation, rule candidates and data/dependency analysis. '
                                  'Do not add decomposition editing, test-plan refinement, code generation or deployment.',
                     'intent': config.get('intent', 'Assess this mainframe application; preserve required human review tasks.')}
        if config.get('orchestrator_agent'):
            arguments['orchestratorAgent'] = config['orchestrator_agent']
        run['create_pending'] = True
        run['status'] = 'CREATING_ASSESSMENT'
        private_save(directory / 'run.json', run)  # Persist before the non-idempotent MCP mutation.
        private_save(receipt_path, {'accepted': False, 'run_id': run['run_id']})
        response = client.calls([('create_job', arguments)])[0]
        private_save(receipt_path, response)
        run['job_id'] = nested_job(response)['jobId']
        run['create_pending'] = False
        private_save(directory / 'run.json', run)
    identity = {'workspaceId': config['workspace_id'], 'jobId': run['job_id']}
    responses = client.calls([('load_instructions', identity), ('get_job_status', {**identity, 'detailed': True})])
    snapshot = responses[-1]
    private_save(directory / 'assessment-status.json', snapshot)
    job = nested_job(snapshot)
    details = job.get('statusDetails', {})
    status = str(job.get('status') or (details.get('status') if isinstance(details, dict) else details) or 'UNKNOWN').upper()
    run['events'].append({'at': time.time(), 'event': 'assessment_status', 'status': status})
    task_container = snapshot.get('tasks', {})
    tasks = task_container.get('hitlTasks', []) if isinstance(task_container, dict) else task_container if isinstance(task_container, list) else []
    if configure_source_task(run, directory, client, identity, tasks):
        return
    if continue_without_activity_records(run, directory, client, identity, tasks):
        return
    pending = any(isinstance(task, dict) and task.get('blockingType') != 'NON_BLOCKING'
                  and task.get('status') in {'AWAITING_HUMAN_INPUT','PENDING_HUMAN_INPUT','AWAITING_APPROVAL'} for task in tasks)
    if pending or (not tasks and snapshot.get('_pollingGuidance', {}).get('hasPendingTasks') is True):
        run['status'] = 'WAITING_FOR_HUMAN'
        run['reason'] = 'AWS has a pending human task even though the overall job may still be executing. Complete the authorized task, then resume.'
    elif status in {'COMPLETED', 'SUCCEEDED'} or (status in {'EXECUTING', 'IN_PROGRESS', 'RUNNING'} and assessment_plan_complete(snapshot)):
        run['assessment_completion'] = {'basis': 'terminal_job' if status in {'COMPLETED', 'SUCCEEDED'} else 'all_authorized_steps_succeeded',
                                        'service_job_status': status}
        # Export paths vary by workflow. Ingest explicit configured output, never guess an artifact ID.
        output = config.get('output_location')
        if output:
            collect_assessment(run, directory, output.replace('{run_id}', run['run_id']).replace('{job_id}', run['job_id']))
        else:
            export_findings(run, directory, client, identity)
        run['status'] = 'AGENTS_PENDING'
    elif status in {'AWAITING_HUMAN_INPUT', 'PENDING_HUMAN_INPUT', 'AWAITING_APPROVAL'}:
        run['status'] = 'WAITING_FOR_HUMAN'
        run['reason'] = 'AWS Transform requires a human task decision. Complete it in the authorized UI, then resume this run.'
    elif status in {'FAILED', 'STOPPED', 'CANCELLED'}:
        run['status'] = 'ASSESSMENT_FAILED'
        run['reason'] = 'AWS stopped this assessment before it finished. Start a new assessment for the same source after the recorded cause is fixed.'
    elif exclude_activity_metrics(run, directory, client, identity, snapshot):
        return
    elif supply_recorded_source(run, directory, client, identity, snapshot):
        return
    elif start_assessment_plan(run, directory, client, identity, snapshot):
        return
    elif bootstrap_assessment_plan(run, directory, client, identity, snapshot):
        return
    else:
        run['status'] = 'WAITING_FOR_ASSESSMENT'
        run['reason'] = 'Job continues in AWS; resume or use --wait/--detach to monitor the same job.'


def json_reply(text):
    stripped = text.strip()
    if stripped.startswith('```json') and stripped.endswith('```'):
        stripped = stripped[7:-3].strip()
    value = json.loads(stripped)
    require(isinstance(value, dict) and value.get('kind') in {'read', 'candidate', 'checkpoint', 'review', 'blocked'},
            'Agent response is not a supported structured handshake')
    return value


def kiro_call(run, directory, role, message):
    config = run['settings']
    executable = config['kiro_command'][0]
    if not shutil.which(executable) or not os.environ.get('KIRO_API_KEY'):
        raise RuntimeUnavailable('Kiro headless setup requires kiro-cli and KIRO_API_KEY; configure them and resume')
    runtime = directory / 'runtime' / role
    agents = runtime / '.kiro' / 'agents'
    agents.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'modernization-' + role
    profile = {'name': name, 'description': 'Controlled source-validation ' + role,
               'prompt': 'Follow the repository protocol supplied in the instruction. Return one JSON object only.',
               'tools': [], 'excludedTools': ['*'], 'mcpServers': {}, 'resources': [], 'hooks': [],
               'includeMcpJson': False, 'includePowers': False,
               'permissions': {'rules': [{'capability': 'all', 'effect': 'deny'}]}}
    model = config.get(role + '_model')
    if model:
        profile['model'] = model
    private_save(agents / (name + '.json'), profile)
    command = config['kiro_command'] + ['chat', '--v3', '--no-interactive', '--agent', name,
                                         '--trust-tools=', '--output-format', 'text']
    # No shell expansion, inherited working directory, --resume, or trust-all flag.
    process = subprocess.Popen(command, cwd=runtime, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(message, timeout=config['call_timeout_seconds'])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        private_save(directory / f'model-{run["model_calls"]}-timeout.json', {'stdout': stdout, 'stderr': stderr})
        raise RuntimeUnavailable('Kiro call exceeded its wall-clock limit and was terminated; resume within remaining call budget')
    private_save(directory / f'model-{run["model_calls"]}-receipt.json',
                 {'role': role, 'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr})
    if process.returncode != 0:
        raise RuntimeUnavailable('Kiro failed or requires authentication; inspect the private model receipt and resume')
    return json_reply(stdout)


def inventory(run):
    root = source_ok(run)
    names = [name for name in git(root, 'ls-files').splitlines() if safe_name(name)]
    require(len(json.dumps(names).encode()) <= run['settings']['max_context_bytes'] // 2,
            'Source inventory exceeds context budget; configure a bounded source checkout')
    return names


def source_read(run, directory, role, request):
    paths = request.get('paths')
    require(isinstance(paths, list) and 0 < len(paths) <= 5 and all(isinstance(x, str) for x in paths),
            'Read request must name one to five paths')
    area = request.get('area', 'source')
    require(area in {'source', 'assessment'}, 'Read area must be source or assessment')
    root = source_ok(run) if area == 'source' else directory / 'assessment'
    chunks = []
    for name in paths:
        require(safe_name(name), 'Rejected path in agent read request')
        path = tracked_file(root, name) if area == 'source' else (root / name).resolve()
        require(path.is_relative_to(root.resolve()) and path.is_file() and not (root / name).is_symlink(), 'Read path escapes scope')
        if area == 'assessment':
            expected = next((x['sha256'] for x in run['assessment_index'] if x['path'] == name), None)
            require(expected == sha(path), 'Assessment snapshot changed')
        raw = path.read_bytes()
        require(len(raw) <= 10000000 and b'\0' not in raw, 'Requested file exceeds 10 MB or is binary')
        text = raw.decode('utf-8')
        start, end = request.get('start', 1), request.get('end', 300)
        require(type(start) is int and type(end) is int and 1 <= start <= end and end - start < 500,
                'Read ranges require positive start/end and at most 500 lines')
        selected = text.splitlines()[start-1:end]
        chunks.append({'area': area, 'path': name, 'sha256': sha(path),
                       'text': '\n'.join(f'{i}: {line}' for i, line in enumerate(selected, start))})
        require(len(chunks[-1]['text'].encode()) <= run['settings']['max_read_bytes'], 'Read result exceeds byte budget; request fewer lines')
    if area == 'source':
        run['role_reads'][f'{run["round"]}:{role}'] = run['role_reads'].get(f'{run["round"]}:{role}', 0) + len(chunks)
    run['events'].append({'event': 'read', 'role': role, 'round': run['round'],
                          'files': [{k: x[k] for k in ['area', 'path', 'sha256']} for x in chunks]})
    return chunks


def agent_step(run, directory, runtime=kiro_call):
    root = source_ok(run)
    role, number = run['role'], run['round']
    require(run['model_calls'] < run['settings']['max_model_calls'], 'Total model-call budget exhausted')
    if role == 'reviewer':
        require(sha(directory / f'candidate-{number}.json') == run['candidate_sha256'], 'Candidate changed after handoff')
    key = f'{number}:{role}'
    history = run['contexts'].setdefault(key, [])
    protocol = (FRAMEWORK / 'prompts' / 'automated' / (role + '.md')).read_text()
    context = {'protocol': protocol, 'task': {'objective': run['objective'], 'source': run['source'],
               'assessment_provenance': run.get('assessment_provenance'), 'round': number,
               'remaining_model_calls': run['settings']['max_model_calls'] - run['model_calls']},
               'source_files': inventory(run), 'assessment_files': run['assessment_index'], 'history': history}
    if role == 'reviewer':
        context['candidate'] = json.loads((directory / f'candidate-{number}.json').read_text())
    if number > 1:
        review_path = directory / f'review-{number-1}.json'
        require(sha(review_path) == run['review_sha256'][str(number-1)], 'Previous review changed')
        context['previous_review'] = json.loads(review_path.read_text())
    message = json.dumps(context)
    require(len(message.encode()) <= run['settings']['max_context_bytes'], 'Context budget exhausted; do not silently truncate evidence')
    run['model_calls'] += 1
    run['status'] = 'RUNNING_' + role.upper()
    run['inflight'] = {'role': role, 'round': number, 'call': run['model_calls']}
    private_save(directory / 'run.json', run)  # Budget persists even if the process dies.
    response = runtime(run, directory, role, message)
    source_ok(run)
    private_save(directory / f'reply-{run["model_calls"]}.json', response)
    run.pop('inflight', None)
    kind = response['kind']
    if kind == 'read':
        history.append({'request': response, 'response': source_read(run, directory, role, response)})
    elif kind == 'blocked':
        run['status'] = 'BLOCKED'
        run['reason'] = response.get('reason', 'Agent reports missing evidence')
    elif role == 'executor' and kind == 'candidate':
        require(run['role_reads'].get(key, 0) > 0, 'Executor did not inspect primary source')
        require(isinstance(response.get('specification'), str) and response['specification'].strip(), 'Candidate specification missing')
        rows = response.get('traceability')
        require(isinstance(rows, list) and len(rows) >= 3, 'Candidate needs positive/rejection/boundary scenario records')
        required = ['rule_id', 'source_path', 'source_anchor', 'scenario_id', 'expected_result', 'oracle', 'status']
        for row in rows:
            require(isinstance(row, dict) and all(isinstance(row.get(x), str) and row[x].strip() for x in required), 'Incomplete scenario')
            require(row['status'] == 'NOT RUN', 'Source-only workflow cannot claim executed tests')
            tracked_file(root, row['source_path'])
        private_save(directory / f'candidate-{number}.json', response)
        run['candidate_sha256'] = sha(directory / f'candidate-{number}.json')
        run['role'] = 'reviewer'
        run['status'] = 'REVIEW_PENDING'
    elif role == 'reviewer' and kind == 'review':
        require(run['role_reads'].get(key, 0) > 0, 'Reviewer did not independently inspect primary source')
        require(response.get('decision') in {'PASS', 'REWORK', 'BLOCKED'}, 'Unsupported review decision')
        require(isinstance(response.get('report'), str) and response['report'].strip(), 'Review report missing')
        require(isinstance(response.get('findings'), list), 'Review findings must be an array')
        if response['decision'] == 'PASS':
            require(not response['findings'], 'Automatic technical PASS requires no open findings; no human waiver is inferred')
        private_save(directory / f'review-{number}.json', response)
        run.setdefault('review_sha256', {})[str(number)] = sha(directory / f'review-{number}.json')
        if response['decision'] == 'PASS':
            run['status'] = 'AWAITING_HUMAN_ACCEPTANCE'
        elif response['decision'] == 'BLOCKED':
            run['status'] = 'BLOCKED'
            run['reason'] = response['report']
        elif number >= run['settings']['max_rounds']:
            run['status'] = 'REWORK_LIMIT'
        else:
            run['round'] += 1
            run['role'] = 'executor'
            run['status'] = 'EXECUTOR_PENDING'
    else:
        raise Blocked('Role returned an unsupported action; no output was accepted')
    run['events'].append({'event': kind, 'role': role, 'round': number, 'call': run['model_calls'], 'at': time.time()})


def summary(run, directory):
    return {'run': str(directory), 'run_id': run['run_id'], 'status': run['status'],
            'round': run['round'], 'model_calls': run['model_calls'], 'reason': run.get('reason'),
            'human_approved': False}


def drive(directory, wait=False, authorize=False, runtime=kiro_call, transform=None, assessment=None):
    directory = Path(directory).resolve()
    with (directory / '.workflow.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Blocked('Another controller owns this run; use status rather than a second resume') from error
        run = json.loads((directory / 'run.json').read_text())
        if assessment:
            require(not run['assessment_ready'], 'Assessment already snapshotted; use a new run for different findings')
            run['assessment_input'] = assessment
            run['events'].append({'event': 'assessment_input_attached', 'at': time.time()})
        if authorize:
            run['settings']['allow_new_assessments'] = True
            run['events'].append({'event': 'new_assessment_authorized', 'at': time.time()})
        try:
            source_ok(run)
            if run.get('inflight'):
                # Model calls have no tool effects. A lost call may be retried within the remaining recorded budget.
                run['events'].append({'event': 'interrupted_model_call', 'receipt': run.pop('inflight')})
            polls = 0
            while True:
                if not run['assessment_ready']:
                    polls += 1
                    assessment_step(run, directory, transform)
                else:
                    terminal = {'AWAITING_HUMAN_ACCEPTANCE', 'BLOCKED', 'REWORK_LIMIT', 'ASSESSMENT_FAILED', 'NEEDS_RECONCILIATION'}
                    if run['status'] in terminal:
                        break
                    agent_step(run, directory, runtime)
                private_save(directory / 'run.json', run)
                if run['status'] == 'WAITING_FOR_ASSESSMENT' and wait:
                    if polls >= run['settings']['max_polls_per_resume']:
                        run['reason'] = 'Watcher poll limit reached; AWS job continues. Resume later with the same run.'
                        break
                    time.sleep(run['settings']['poll_seconds'])
                    continue
                if run['status'] in {'WAITING_FOR_ASSESSMENT', 'WAITING_FOR_HUMAN', 'WAITING_FOR_AUTHORIZATION',
                                     'ASSESSMENT_FAILED', 'NEEDS_RECONCILIATION', 'BLOCKED', 'REWORK_LIMIT', 'AWAITING_HUMAN_ACCEPTANCE'}:
                    break
        except RuntimeUnavailable as error:
            run['status'], run['reason'] = 'WAITING_FOR_RUNTIME', str(error)
        except (Blocked, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            run['status'] = 'NEEDS_RECONCILIATION' if run['create_pending'] and not run['job_id'] else 'BLOCKED'
            run['reason'] = str(error)
        finally:
            private_save(directory / 'run.json', run)
        return summary(run, directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start')
    for name in ['source', 'workspace']:
        start.add_argument('--' + name, required=True)
    start.add_argument('--settings')
    start.add_argument('--assessment', help='Existing local text findings or S3 object/prefix; omit for new assessment')
    start.add_argument('--objective', default='Validate one bounded business rule against source and assessment; produce explicit oracles and gaps.')
    start.add_argument('--allow-new-assessment', action='store_true')
    start.add_argument('--wait', action='store_true')
    start.add_argument('--detach', action='store_true')
    for name in ['resume', 'status', 'attach-job']:
        command = commands.add_parser(name)
        command.add_argument('--run', required=True)
        if name == 'resume':
            command.add_argument('--wait', action='store_true')
            command.add_argument('--allow-new-assessment', action='store_true')
            command.add_argument('--detach', action='store_true')
            command.add_argument('--assessment', help='Attach existing findings if automatic export needs an explicit location')
        if name == 'attach-job':
            command.add_argument('--job-id', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'start':
            # Fail preflight before uploading source or making a new AWS job.
            config = settings(args.settings)
            require(shutil.which(config['kiro_command'][0]), 'Install kiro-cli before starting an automated run')
            require(os.environ.get('KIRO_API_KEY'), 'Configure KIRO_API_KEY for Kiro headless mode')
            directory = initialize(args)
        else:
            directory = Path(args.run).resolve()
        if args.command == 'status':
            print(json.dumps(summary(json.loads((directory / 'run.json').read_text()), directory), indent=2))
            return 0
        if args.command == 'attach-job':
            with (directory / '.workflow.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                run = json.loads((directory / 'run.json').read_text())
                require(run['status'] == 'NEEDS_RECONCILIATION' and not run['job_id'], 'Attach is only for uncertain job creation')
                cfg = run['settings']['transform']
                responses = Transform(cfg, directory).calls([('load_instructions', {'workspaceId': cfg['workspace_id'], 'jobId': args.job_id}),
                    ('get_job_status', {'workspaceId': cfg['workspace_id'], 'jobId': args.job_id, 'detailed': True})])
                job = nested_job(responses[-1])
                require(run['run_id'] in json.dumps(job), 'Job does not reference this run; refusing to attach')
                run['job_id'], run['create_pending'], run['status'] = args.job_id, False, 'WAITING_FOR_ASSESSMENT'
                private_save(directory / 'run.json', run)
                print(json.dumps(summary(run, directory), indent=2))
                return 0
        if args.detach:
            command = [sys.executable, str(Path(__file__).resolve()), 'resume', '--run', str(directory), '--wait']
            if args.allow_new_assessment:
                command.append('--allow-new-assessment')
            if args.command == 'resume' and args.assessment:
                command += ['--assessment', args.assessment]
            with (directory / 'controller.log').open('ab') as output:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
            print(json.dumps({'run': str(directory), 'pid': process.pid, 'status': 'CONTROLLER_STARTED',
                              'note': 'Local process, not a cloud service; use resume after shutdown.'}, indent=2))
        else:
            result = drive(directory, args.wait, args.allow_new_assessment,
                           assessment=args.assessment if args.command == 'resume' else None)
            print(json.dumps(result, indent=2))
            return 2 if result['status'] in {'BLOCKED', 'ASSESSMENT_FAILED', 'NEEDS_RECONCILIATION', 'REWORK_LIMIT'} else 0
    except (Blocked, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(error)}))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
