"""Provider processes for the preparation workflow; agents never receive AWS tools."""
import json
import hashlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

from pilot import Blocked, require
from kiro_workflow import private_save, kiro_call, RuntimeUnavailable


class ProviderSafetyRefusal(Blocked):
    """A provider policy refusal requires review, not an automatic runtime retry."""


def preparation_protocol_hash():
    root = Path(__file__).resolve().parent.parent / 'prompts/pipeline'
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in ('executor.md','reviewer.md'))).hexdigest()


def check_provider_refusals(directory):
    recovery_path = directory / 'provider-recovery.json'
    recovery = json.loads(recovery_path.read_text()) if recovery_path.exists() else {}

    for path in directory.glob('provider-*.json'):
        receipt = json.loads(path.read_text())
        if receipt.get('provider') != 'claude':
            continue
        try:
            envelope = json.loads(receipt.get('stdout', ''))
        except (ValueError, TypeError):
            continue
        result = str(envelope.get('result', ''))
        if envelope.get('is_error') and ('safeguards flagged' in result.lower() or '[reasoning_extraction]' in result):
            reviewed = recovery.get(path.name, {})
            if (reviewed.get('receipt_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
                    and reviewed.get('protocol_sha256') == preparation_protocol_hash()):
                continue
            raise ProviderSafetyRefusal('Claude refused a request under its safeguards. Review the private provider receipt and request content before any further model calls; no candidate approval is implied.')


def call(provider, run, directory, role, message):
    require(provider in {'kiro', 'claude', 'codex'}, 'Unknown provider')
    if provider == 'kiro':
        # Kiro's parser supports the legacy protocol; use a temporary wrapper kind.
        return kiro_call(run, directory, role, message)
    executable = 'claude' if provider == 'claude' else 'codex'
    if not shutil.which(executable):
        raise RuntimeUnavailable(f'Install and authenticate {executable}, then resume')
    work = directory / 'runtime' / role
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    model = run['settings'].get(role + '_model')
    if provider == 'claude':
        command = ['claude', '-p', '--setting-sources', '', '--strict-mcp-config',
                   '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands',
                   '--settings', '{"disableAllHooks":true}', '--tools', '', '--disallowedTools', 'mcp__*',
                   '--no-session-persistence', '--output-format', 'json', '--max-turns', '1']
    else:
        command = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                   '--skip-git-repo-check', '--sandbox', 'read-only', '--json',
                   '-c', 'approval_policy="never"', '-c', 'features.shell_tool=false',
                   '-c', 'features.unified_exec=false', '-c', 'features.apply_patch_freeform=false',
                   '-c', 'agents.enabled=false', '-c', 'web_search="disabled"',
                   '-c', 'mcp_servers={}', '-c', 'features.apps=false', '-']
    if model:
        command[1:1] = ['--model', model]
    private_save(directory / f'provider-request-{run["model_calls"]}.json',
                 {'provider':provider, 'role':role, 'requested_model':model, 'message':message})
    process = subprocess.Popen(command, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(message, timeout=run['settings']['call_timeout_seconds'])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise RuntimeUnavailable('Provider timed out; recorded call budget remains consumed')
    private_save(directory / f'provider-{run["model_calls"]}.json',
                 {'provider': provider, 'role': role, 'requested_model': model, 'returncode': process.returncode,
                  'stdout': stdout, 'stderr': stderr})
    check_provider_refusals(directory)
    if process.returncode:
        raise RuntimeUnavailable('Provider failed; inspect private provider receipt and resume')
    if provider == 'claude':
        envelope = json.loads(stdout)
        require(not envelope.get('is_error'), 'Claude returned an error')
        if model and role in {'executor', 'reviewer'}:
            reported = envelope.get('modelUsage', {})
            require(isinstance(reported, dict) and set(reported) == {model},
                    'Claude model identity missing or different from the pinned model; output rejected')
        reply = envelope.get('result', '')
    else:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        require(not any(x.get('type') in {'error', 'turn.failed'} for x in events), 'Codex reported failure')
        items = [x['item'] for x in events if x.get('type') == 'item.completed']
        require(not any(x.get('type') in {'command_execution', 'file_change', 'mcp_tool_call', 'web_search'}
                        for x in items), 'Unexpected tool use: reject this output and inspect provider configuration')
        messages = [x['text'] for x in items if x.get('type') == 'agent_message']
        require(messages, 'Codex returned no final message')
        reply = messages[-1]
    if reply.strip().startswith('```json'):
        reply = reply.strip()[7:-3]
    result = json.loads(reply)
    require(isinstance(result, dict), 'Provider must return a JSON object')
    return result
