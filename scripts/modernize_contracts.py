"""Load the YAML record templates and reject an incomplete stage record.

The YAML files remain the source of required fields. This module does not
replace those templates with a second schema.
"""
import json
from pathlib import Path

from pilot import Blocked, require


FRAMEWORK = Path(__file__).resolve().parent.parent
CONTRACTS = {
    'task': 'task-contract.yaml',
    'review': 'review-record.yaml',
    'approval': 'approval-record.yaml',
}
RECORD_FILES = {
    'task': 'task-contract.json',
    'review': 'review-record.json',
    'approval': 'approval-record.json',
}
LABELS = {'task': 'Task', 'review': 'Review', 'approval': 'Approval'}


def _parse(text):
    root = []
    stack = [(-1, root)]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        comment = ''
        body = stripped
        if '#' in stripped:
            body, comment = stripped.split('#', 1)
            body, comment = body.strip(), comment.strip()
        if ':' not in body:
            continue
        key, value = body.split(':', 1)
        node = {'key': key.strip(), 'value': value.strip(), 'comment': comment, 'children': []}
        while stack and indent <= stack[-1][0]:
            stack.pop()
        stack[-1][1].append(node)
        stack.append((indent, node['children']))
    return root


def _walk(nodes, prefix=''):
    found = []
    for node in nodes:
        path = prefix + node['key']
        if node['children']:
            found.extend(_walk(node['children'], path + '.'))
            continue
        marked = node['value'] == 'REQUIRED' or 'REQUIRED' in node['comment'].upper()
        if not marked:
            continue
        evidence = ('evidence' in node['comment'].lower() or 'evidence' in node['key'].lower()
                    or 'reference' in node['key'].lower() or node['key'].endswith('revision'))
        if evidence:
            kind = 'evidence'
        elif node['value'].startswith('['):
            kind = 'list'
        else:
            kind = 'value'
        found.append((path, kind))
    return found


def requirements(kind):
    """Return (dotted path, kind) pairs taken from the YAML template."""
    require(kind in CONTRACTS, 'Unknown contract')
    text = (FRAMEWORK / 'contracts' / CONTRACTS[kind]).read_text()
    return _walk(_parse(text))


def _lookup(record, path):
    current = record
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _filled(value):
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() != 'REQUIRED'
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, list):
        return bool(value) and all(_filled(item) or (isinstance(item, dict) and item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return False


def _evidence_link(value):
    if isinstance(value, str):
        return _filled(value)
    if isinstance(value, list):
        return bool(value) and all(_evidence_link(item) for item in value)
    if isinstance(value, dict):
        for key in ('evidence', 'link', 'ref', 'reference', 'path'):
            if _filled(value.get(key)):
                return True
        return False
    return False


def require_record(record, kind):
    require(isinstance(record, dict), LABELS[kind] + ' contract record must be an object')
    for path, field in requirements(kind):
        value = _lookup(record, path)
        if field == 'evidence':
            require(_evidence_link(value), LABELS[kind] + ' contract missing evidence link ' + path)
        else:
            require(_filled(value), LABELS[kind] + ' contract missing ' + path)


def require_stage_contract(work, kind):
    """Check a workspace record when the stage has one. An absent record is not invented."""
    path = Path(work) / RECORD_FILES[kind]
    if not path.exists():
        return
    require(path.is_file() and not path.is_symlink(), LABELS[kind] + ' contract record is not a file')
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise Blocked(LABELS[kind] + ' contract record is not readable') from error
    require_record(record, kind)
