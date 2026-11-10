#!/usr/bin/env python3
"""Local, manifest-driven pilot records. Python 3.10+, macOS/Linux, stdlib only."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


class Blocked(Exception):
    pass


def require(condition, message):
    """Stop the operation when a condition is false."""
    if not condition:
        raise Blocked(message)


def sha(path):
    """Return the SHA-256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    """Write JSON by replacing a temporary file, so a crash cannot leave a partial record."""
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def git(root, *args):
    """Run one git command in a checkout and return its output."""
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    require(result.returncode == 0, 'Source Git inspection failed: ' + result.stderr.strip())
    return result.stdout.strip()


def baseline(task):
    """Confirm the source and the framework still match the pinned commits and are clean."""
    root = Path(task['source']['root'])
    require(git(root, 'rev-parse', 'HEAD') == task['source']['commit'], 'Source commit changed')
    require(git(root, 'remote', 'get-url', 'origin') == task['source']['repository'], 'Source origin changed')
    require(not git(root, 'status', '--porcelain'), 'Source checkout is not clean')
    framework = Path(task['framework_root'])
    require(git(framework, 'rev-parse', 'HEAD') == task['framework_commit'], 'Framework commit changed')
    require(not git(framework, 'status', '--porcelain'), 'Framework checkout is not clean')
    return root


def tracked_file(root, name):
    """Resolve a source path that stays inside the pinned checkout and is tracked by git."""
    require(not (root / name).is_symlink(), 'Source symlink rejected')
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), 'Source path escapes the pinned checkout')
    require(path.is_file() and not path.is_symlink(), 'Source file missing')
    relative = path.relative_to(root.resolve()).as_posix()
    require(git(root, 'ls-files', '--', relative) == relative, 'Source file is not tracked')
    return path


@contextmanager
def locked(manifest):
    """Hold the task lock and reload the manifest and state for one operation."""
    manifest = manifest.resolve()
    require(manifest.is_file(), 'Task manifest not found; do not substitute another workspace')
    with (manifest.parent / '.pilot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        task = json.loads(manifest.read_text())
        state_path = manifest.parent / 'state.json'
        state = json.loads(state_path.read_text())
        require(sha(manifest) == state['manifest_sha256'], 'Manifest changed; create a new task attempt')
        try:
            yield task, state, manifest.parent
        finally:
            save(state_path, state)


def verify_handoff(task, state, directory):
    """Confirm the reviewer handoff still matches the sealed artifacts and the pinned baseline."""
    require(state['executor_status'] == 'READY_FOR_REVIEW', 'Executor is not READY_FOR_REVIEW')
    handoff = directory / 'reviewer-handoff.json'
    require(handoff.is_file() and sha(handoff) == state.get('handoff_sha256'), 'Handoff missing or changed')
    record = json.loads(handoff.read_text())
    require(record['manifest_sha256'] == state['manifest_sha256'], 'Handoff baseline differs')
    for name, expected in record['artifact_sha256'].items():
        path = directory / name
        require(path.is_file() and sha(path) == expected, 'Sealed artifact missing or changed: ' + name)
    baseline(task)


def charge(task, state, role, command):
    """Count one operation against the role budget and stop when that budget is spent."""
    session = state['sessions'].get(role)
    require(session is not None and session['status'] == 'ACTIVE', 'Begin an active ' + role + ' session first')
    if time.time() >= session['deadline_epoch'] or session['operations'] >= task['budget']['max_operations_per_role']:
        session['status'] = 'BLOCKED'
        state[role + '_status'] = 'BLOCKED'
        raise Blocked('Budget exhausted; affected role is BLOCKED. Create a new approved attempt; do not reset counters.')
    session['operations'] += 1
    session['events'].append({'operation': session['operations'], 'command': command, 'at_epoch': time.time()})
    return session


def init(args):
    """Create a task manifest and state outside the source checkout."""
    workspace, source = Path(args.workspace).resolve(), Path(args.source).resolve()
    framework = Path(__file__).resolve().parent.parent
    require(workspace.is_dir() and source.is_dir(), 'Workspace and source directories must exist')
    require(not workspace.is_relative_to(source), 'Evidence workspace must be outside the source checkout')
    require(not workspace.is_relative_to(framework), 'Operational evidence must be outside the public framework checkout')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.task_id), 'Invalid task ID')
    require(5 <= args.max_operations <= 1000 and math.isfinite(args.minutes) and 0 < args.minutes <= 1440,
            'Budget requires 5-1000 operations and 0-1440 finite minutes')
    require(not git(framework, 'status', '--porcelain'), 'Framework checkout must be clean before initialization')
    require(not git(source, 'status', '--porcelain'), 'Source checkout is not clean')
    entrypoint = tracked_file(source, args.entrypoint)
    directory = workspace / 'evidence' / args.task_id
    require(not directory.exists(), 'Task directory already exists; use a new attempt ID')
    directory.mkdir(parents=True)
    task = {
        'schema_version': 1, 'task_id': args.task_id, 'human_owner': args.owner,
        'framework_root': str(framework), 'framework_commit': git(framework, 'rev-parse', 'HEAD'),
        'workspace': str(workspace), 'evidence_directory': str(directory),
        'objective': args.behavior, 'classification': 'public source and synthetic fixtures only',
        'required_gate': 'G2 technical recommendation; human approval remains separate',
        'source': {'root': str(source), 'repository': git(source, 'remote', 'get-url', 'origin'),
                   'commit': git(source, 'rev-parse', 'HEAD'), 'entrypoint': entrypoint.relative_to(source).as_posix()},
        'allowed_actions': ['metered local source reads/searches', 'managed pilot artifact writes'],
        'excluded_actions': ['network or cloud calls', 'source execution or edits', 'target implementation',
                             'commits or publication', 'human approval', 'source inspection outside this runner'],
        'budget': {'max_operations_per_role': args.max_operations, 'minutes_per_role': args.minutes,
                   'new_paid_api_or_cloud_calls': 0},
        'acceptance_criteria': ['source anchors at pinned revision', 'explicit preconditions, outputs and state changes',
                                'deterministic source-derived oracles', 'positive, rejection and boundary scenarios',
                                'dependencies and uncertainty stated', 'all designed tests remain NOT RUN',
                                'separate primary-source review with no unresolved material findings'],
        'artifacts': {'candidate': 'candidate.md', 'traceability': 'traceability.csv',
                      'handoff': 'reviewer-handoff.json', 'review': 'review.md', 'review_record': 'review.json'},
        'on_missing_evidence': 'BLOCKED', 'on_budget_exhaustion': 'BLOCKED',
    }
    manifest = directory / 'task.json'
    save(manifest, task)
    save(directory / 'state.json', {'manifest_sha256': sha(manifest), 'sessions': {},
                                  'executor_status': 'NOT_STARTED', 'reviewer_status': 'NOT_STARTED'})
    return {'manifest': str(manifest), 'next': 'begin --role executor --actor <session-identity>'}


def operate(args):
    """Run one metered pilot command: status, begin, read, search, put, seal, or finish-review."""
    manifest = Path(args.task)
    with locked(manifest) as (task, state, directory):
        role, command = args.role, args.command
        if command == 'status':
            for name, session in state['sessions'].items():
                if session['status'] == 'ACTIVE' and (time.time() >= session['deadline_epoch'] or
                        session['operations'] >= task['budget']['max_operations_per_role']):
                    session['status'] = 'BLOCKED'
                    state[name + '_status'] = 'BLOCKED'
            warnings = []
            try:
                baseline(task)
                if state['executor_status'] == 'READY_FOR_REVIEW':
                    verify_handoff(task, state, directory)
            except Blocked as error:
                warnings.append(str(error))
            return {'task': task, 'state': state, 'integrity_warnings': warnings}
        if command == 'begin':
            require(args.actor.strip(), 'Actor session identity is required')
            require(role not in state['sessions'], 'Role already started; cannot reset its budget')
            baseline(task)
            if role == 'reviewer':
                verify_handoff(task, state, directory)
                require(args.actor != state['sessions']['executor']['actor'], 'Reviewer must be a separate session')
            now = time.time()
            state['sessions'][role] = {'actor': args.actor, 'status': 'ACTIVE', 'operations': 0,
                                       'deadline_epoch': now + task['budget']['minutes_per_role'] * 60,
                                       'events': [], 'source_reads': 0}
            state[role + '_status'] = 'ACTIVE'
        session = charge(task, state, role, command)
        root = baseline(task)
        if role == 'reviewer':
            verify_handoff(task, state, directory)
        if command == 'begin':
            return {'task': task, 'role': role, 'operations_used': session['operations'],
                    'instructions': str(Path(task['framework_root']) / 'prompts' /
                                        ('04-rule-validation-pilot.md' if role == 'executor' else '02-independent-reviewer.md'))}
        if command == 'read':
            path = tracked_file(root, args.path)
            lines = path.read_text(errors='replace').splitlines()
            require(args.start >= 1 and args.end >= args.start and args.end - args.start < 500,
                    'Read ranges must be positive and at most 500 lines')
            session['source_reads'] += 1
            session['events'][-1].update({'path': args.path, 'sha256': sha(path), 'start': args.start, 'end': args.end})
            return {'source_path': args.path, 'source_commit': task['source']['commit'],
                    'lines': '\n'.join(f'{i}: {lines[i-1]}' for i in range(args.start, min(args.end, len(lines)) + 1)),
                    'operations_used': session['operations']}
        if command == 'search':
            hits = []
            pattern = re.compile(args.pattern, re.IGNORECASE)
            for name in git(root, 'ls-files').splitlines():
                path = tracked_file(root, name)
                if path.stat().st_size > 2_000_000:
                    continue
                raw = path.read_bytes()
                if b'\0' in raw:
                    continue
                for number, line in enumerate(raw.decode(errors='replace').splitlines(), 1):
                    if pattern.search(line):
                        hits.append({'path': name, 'line': number, 'text': line[:500]})
                        if len(hits) >= 60:
                            return {'hits': hits, 'truncated': True, 'operations_used': session['operations']}
            return {'hits': hits, 'truncated': False, 'operations_used': session['operations']}
        if command == 'put':
            allowed = ['candidate', 'traceability'] if role == 'executor' else ['review']
            require(args.artifact in allowed, 'Artifact not writable by this role')
            require(len(args.content.strip()) > 0, 'Empty artifact rejected')
            path = directory / task['artifacts'][args.artifact]
            require(not path.is_symlink(), 'Artifact symlink rejected')
            path.write_text(args.content)
            return {'written': str(path), 'sha256': sha(path), 'operations_used': session['operations']}
        if command == 'seal':
            require(role == 'executor', 'Only executor can seal its candidate')
            require(session['source_reads'] > 0, 'No metered source reads recorded')
            candidate, matrix = directory / 'candidate.md', directory / 'traceability.csv'
            require(candidate.is_file() and matrix.is_file(), 'Candidate or traceability artifact missing')
            rows = list(csv.DictReader(io.StringIO(matrix.read_text())))
            columns = ['rule_id', 'source_path', 'source_commit', 'source_anchor', 'scenario_id',
                       'preconditions', 'input', 'expected_result', 'oracle', 'status']
            require(len(rows) >= 3, 'At least three explicit scenario rows are required')
            for row in rows:
                require(all(row.get(key, '').strip() for key in columns), 'Traceability columns/values incomplete')
                require(row['source_commit'] == task['source']['commit'], 'Traceability source revision differs')
                tracked_file(root, row['source_path'])
                require(row['status'] == 'NOT RUN', 'This read-only pilot cannot claim executed tests')
            require(time.time() < session['deadline_epoch'], 'Deadline elapsed before sealing')
            record = {'task_id': task['task_id'], 'manifest': str(manifest.resolve()),
                      'manifest_sha256': state['manifest_sha256'], 'source': task['source'],
                      'artifact_sha256': {p.name: sha(p) for p in [candidate, matrix]},
                      'executor_actor': session['actor'], 'executor_operations': session['operations'],
                      'executor_events': session['events'], 'limitations': 'Oracles still require semantic review; tests NOT RUN'}
            save(directory / 'reviewer-handoff.json', record)
            state['handoff_sha256'] = sha(directory / 'reviewer-handoff.json')
            session['status'] = 'SEALED'
            state['executor_status'] = 'READY_FOR_REVIEW'
            return {'status': 'READY_FOR_REVIEW', 'handoff': str(directory / 'reviewer-handoff.json'),
                    'operations_used': session['operations'], 'human_approved': False}
        if command == 'finish-review':
            require(role == 'reviewer', 'Only reviewer can finish review')
            report = directory / 'review.md'
            require(report.is_file() and report.stat().st_size > 0, 'Review report missing')
            require(session['source_reads'] > 0, 'Reviewer must independently read pinned source')
            require(time.time() < session['deadline_epoch'], 'Deadline elapsed before review completion')
            save(directory / 'review.json', {'task_id': task['task_id'], 'decision': args.decision,
                 'actor': session['actor'], 'manifest_sha256': state['manifest_sha256'],
                 'handoff_sha256': state['handoff_sha256'], 'report_sha256': sha(report),
                 'operations': session['operations'], 'events': session['events'], 'human_approved': False})
            session['status'] = args.decision
            state['reviewer_status'] = args.decision
            return {'decision': args.decision, 'human_approved': False, 'operations_used': session['operations']}
        raise Blocked('Unsupported operation')


def main():
    """Parse the pilot command and print JSON. A blocked operation exits with status 2."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init')
    for name in ['workspace', 'source', 'task-id', 'owner', 'behavior', 'entrypoint']:
        create.add_argument('--' + name, required=True)
    create.add_argument('--max-operations', type=int, default=30)
    create.add_argument('--minutes', type=float, default=30)
    for name in ['status', 'begin', 'read', 'search', 'put', 'seal', 'finish-review']:
        command = commands.add_parser(name)
        command.add_argument('--task', required=True)
        command.add_argument('--role', choices=['executor', 'reviewer'], default='executor')
        if name == 'begin':
            command.add_argument('--actor', required=True)
        if name == 'read':
            command.add_argument('--path', required=True)
            command.add_argument('--start', type=int, default=1)
            command.add_argument('--end', type=int, default=300)
        if name == 'search':
            command.add_argument('--pattern', required=True)
        if name == 'put':
            command.add_argument('--artifact', choices=['candidate', 'traceability', 'review'], required=True)
        if name == 'finish-review':
            command.add_argument('--decision', choices=['PASS', 'REWORK', 'BLOCKED'], required=True)
    args = parser.parse_args()
    if args.command == 'put':
        args.content = sys.stdin.read()
    try:
        result = init(args) if args.command == 'init' else operate(args)
        print(json.dumps(result, indent=2))
    except (Blocked, ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(error)}))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
