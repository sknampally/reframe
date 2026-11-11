"""Opt-in design modes, human context, and extra packages.

Existing review sites, skip drafts, the single AI-DLC package, and project.json
stay in place. These commands write only beside those paths.
"""
import html
import json
import re
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from pilot import Blocked, require

MODES = ('functional-parity', 'reimagine')
CATEGORIES = ('current-state', 'future-state', 'clarification')
DISPOSITIONS = ('proposed', 'accepted', 'rejected', 'superseded')
TEXT_SUFFIXES = {'.md', '.txt', '.json'}
MAX_BYTES = 1_000_000


def _read(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _version(value):
    require(bool(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,40}', value or '')), 'Use a short version label')
    require(value.casefold() not in {'business-1', 'flow-review'}, 'That version name is reserved for the existing review path')
    return value


def _mode_root(work, version):
    """Refuse a version that would land in an existing review site or skip draft."""
    version = _version(version)
    requested = Path(work) / 'previews' / version
    previews = Path(work) / 'previews'
    if previews.is_dir():
        for existing in previews.iterdir():
            if not existing.is_dir():
                continue
            same_name = existing.name.casefold() == version.casefold()
            same_path = existing.resolve() == requested.resolve()
            if not same_name and not same_path:
                continue
            owned = same_path and (existing / 'mode.json').is_file()
            require(owned, 'That version name matches an existing preview')
    for existing in Path(work).iterdir():
        if existing.is_dir() and existing.name.casefold() == ('preview-' + version).casefold():
            raise Blocked('That version name matches an existing preview')
    return requested


def context_dir(work):
    return Path(work) / 'human-review-context'


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quarantine(folder, name, reason):
    record = {'file': name, 'reason': reason, 'executed': False}
    _write(folder / 'quarantine' / (_submission_id(name.encode()) + '.json'), record)


def _context_fingerprint(work):
    manifest = context_status(work)
    items = [(item.get('id'), item.get('disposition')) for item in manifest.get('submissions') or []]
    return _digest({'status': manifest.get('status'), 'items': items})


def _scan(data, label):
    from reframe import SECRET_PATTERNS
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(data):
            raise Blocked('Refusing export; ' + label + ' matches ' + kind)


def context_status(work):
    """Missing and empty context are valid. Neither is an approval."""
    manifest_path = context_dir(work) / 'manifest.json'
    if not manifest_path.is_file():
        return {'status': 'not_provided', 'submissions': [], 'conflicts': []}
    manifest = _read(manifest_path)
    submissions = manifest.get('submissions') or []
    if not submissions:
        manifest['status'] = 'not_provided'
    return manifest


def _submission_id(content):
    return 'ctx-' + sha256(content).hexdigest()[:16]


def _contradiction(text):
    match = re.search(r'(?im)^contradicts:\s*(\S+)\s*$', text)
    return match.group(1) if match else None


def import_context(work, source, by, role, category, affected):
    """Store one Markdown, text, or JSON document as untrusted data."""
    require(category in CATEGORIES, 'Context category must be current-state, future-state, or clarification')
    require(by.strip() and role.strip(), 'Context needs the name and role supplied by the user')
    path = Path(source)
    require(path.is_file(), 'Context file was not found')
    suffix = path.suffix.lower()
    folder = context_dir(work)
    if suffix not in TEXT_SUFFIXES or path.stat().st_size > MAX_BYTES:
        _quarantine(folder, path.name, 'unsupported or oversize')
        raise Blocked('Context file was quarantined; only Markdown, text, and JSON up to 1 MB are imported')
    raw = path.read_bytes().replace(b'\x00', b'')
    try:
        text = raw.decode('utf-8')
        parsed = json.loads(text) if suffix == '.json' else None
    except (UnicodeError, json.JSONDecodeError):
        _quarantine(folder, path.name, 'malformed')
        raise Blocked('Context file was quarantined; the text or JSON could not be read')
    if suffix == '.json':
        if not isinstance(parsed, dict):
            _quarantine(folder, path.name, 'malformed')
            raise Blocked('Context file was quarantined; JSON context must be an object')
        text = parsed.get('text') if isinstance(parsed.get('text'), str) else json.dumps(parsed, sort_keys=True)
        contradicts = parsed.get('contradicts') if isinstance(parsed.get('contradicts'), str) else _contradiction(text)
    else:
        contradicts = _contradiction(text)
    submission = {
        'id': _submission_id(raw), 'by': by.strip(), 'role': role.strip(), 'category': category,
        'affected': affected, 'disposition': 'proposed', 'identity_proof': False,
        'extraction': 'extracted', 'executed': False, 'source_name': path.name,
        'sha256': sha256(raw).hexdigest(), 'recorded_at': _now(),
    }
    extracted = folder / 'extracted' / (submission['id'] + '.txt')
    extracted.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    extracted.write_text(text)
    extracted.chmod(0o600)
    original = folder / 'submissions' / (submission['id'] + suffix)
    original.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    original.write_bytes(raw)
    original.chmod(0o600)
    manifest = context_status(work)
    if manifest['status'] == 'not_provided':
        manifest = {'status': 'provided', 'submissions': [], 'conflicts': []}
    require(all(item['id'] != submission['id'] for item in manifest['submissions']), 'This context file is already imported')
    manifest['status'] = 'provided'
    manifest['submissions'].append(submission)
    if contradicts:
        conflict = {
            'id': 'conflict-' + submission['id'], 'submission_id': submission['id'], 'contradicts': contradicts,
            'status': 'open', 'preserves': 'both the cited evidence and this human statement',
        }
        manifest['conflicts'].append(conflict)
        _write(folder / 'conflicts' / (conflict['id'] + '.json'), conflict)
    _write(folder / 'manifest.json', manifest)
    return {'status': 'IMPORTED', 'id': submission['id'], 'conflicts': len(manifest['conflicts'])}


def decide_context(work, submission_id, disposition, by, note):
    """Record a human disposition. This does not verify source behavior."""
    require(disposition in DISPOSITIONS and disposition != 'proposed', 'Choose accepted, rejected, or superseded')
    require(by.strip() and note.strip(), 'A context decision needs a person and a note')
    manifest = context_status(work)
    require(manifest['status'] == 'provided', 'No human context has been imported')
    found = next((item for item in manifest['submissions'] if item['id'] == submission_id), None)
    require(found, 'Context submission was not found')
    found['disposition'] = disposition
    found['reviewer'] = by.strip()
    found['note'] = note.strip()
    found['decided_at'] = _now()
    _write(context_dir(work) / 'decisions' / (submission_id + '.json'), {
        'id': submission_id, 'disposition': disposition, 'by': by.strip(), 'note': note.strip(),
        'layer': 'human-provided context', 'verified_source_behavior': False,
    })
    for conflict in manifest['conflicts']:
        if conflict['submission_id'] == submission_id and conflict['status'] == 'open':
            conflict['status'] = 'resolved_by_acceptance' if disposition == 'accepted' else 'retained_inactive'
            _write(context_dir(work) / 'conflicts' / (conflict['id'] + '.json'), conflict)
    _write(context_dir(work) / 'manifest.json', manifest)
    return {'status': 'RECORDED', 'id': submission_id, 'disposition': disposition}


def open_conflicts(work):
    manifest = context_status(work)
    return [item for item in manifest.get('conflicts') or [] if item.get('status') == 'open']


def _active(work, category):
    manifest = context_status(work)
    chosen = []
    for item in manifest.get('submissions') or []:
        if item.get('category') == category and item.get('disposition') == 'accepted':
            text = (context_dir(work) / 'extracted' / (item['id'] + '.txt')).read_text()
            chosen.append((item, text))
    return chosen


def _page(title, body):
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; form-action \'none\'; base-uri \'none\'">'
            '<title>' + html.escape(title) + '</title><body>' + body + '</body></html>')


def design_export(work, project, version, mode, stack):
    """Copy the recorded review into an opt-in folder. The existing site is not rewritten."""
    version = _version(version)
    require(mode in MODES, 'Choose functional-parity or reimagine')
    destination_root = _mode_root(work, version)
    site = project.get('site') or {}
    require(site.get('directory'), 'Record a review site before exporting a design mode')
    source = Path(work) / site['directory']
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    screens = _read(source / 'screens.json')
    from reframe_artifacts import render_site
    spec = {'scope': project.get('scope') or '', 'rules': [], 'sections': {}, 'questions': [], 'tests': []}
    spec_record = project.get('specification') or {}
    if spec_record.get('path') and (Path(work) / spec_record['path']).is_file():
        loaded = _read(Path(work) / spec_record['path'])
        spec.update({key: loaded.get(key, spec[key]) for key in spec})
    notice = ('<p>Design interpretation of the recorded review. Not a screenshot of the legacy system. '
              'Human context status: ' + html.escape(context_status(work)['status']) + '.</p>')
    clarifications = ''.join('<article><h2>' + html.escape(item['id']) + '</h2><p>Accepted human clarification by '
                             + html.escape(item['by']) + '. This is not verified source behavior.</p><pre>'
                             + html.escape(text[:4000]) + '</pre></article>' for item, text in _active(work, 'current-state'))
    current = render_site(spec, screens, project.get('decisions') or {})
    current = current.replace('</main>', notice + clarifications + '</main>')
    destination = destination_root / 'current-state'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    (destination / 'index.html').write_text(current)
    (destination / 'index.html').chmod(0o600)
    mode_record = {
        'mode': mode, 'version': version, 'stack': stack.strip() or 'unspecified',
        'human_context': context_status(work)['status'], 'context_fingerprint': _context_fingerprint(work),
        'final_approval': 'not_requested', 'site_directory': site['directory'],
    }
    _write(Path(work) / 'previews' / version / 'mode.json', mode_record)
    if mode == 'reimagine':
        _write_future(work, version, screens)
    after = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    require(before == after, 'Design export changed the existing review site')
    return {'status': 'EXPORTED', 'mode': mode, 'current_state': str(destination.relative_to(work))}


def _write_future(work, version, screens):
    accepted = _active(work, 'future-state')
    deltas = []
    for journey in screens.get('journeys') or [{'id': 'recorded', 'name': 'Recorded screens', 'screen_ids': [item['id'] for item in screens.get('screens') or []]}]:
        matched = [(item, text) for item, text in accepted if journey['id'] in (item.get('affected') or [])]
        if matched:
            item, text = matched[0]
            deltas.append({'id': 'delta-' + journey['id'], 'status': 'accepted', 'journey': journey['id'],
                           'evidence': journey.get('screen_ids') or [], 'owner': item['by'],
                           'change': text[:2000], 'authorization': 'human-accepted future-state context'})
        else:
            deltas.append({'id': 'proposal-' + journey['id'], 'status': 'proposed_unapproved', 'journey': journey['id'],
                           'evidence': journey.get('screen_ids') or [], 'owner': 'unassigned',
                           'change': 'No approved future-state change. This idea is not an implementation requirement.',
                           'benefit': 'Unreviewed', 'risk': 'Implementing it would change behavior without a decision.'})
    folder = Path(work) / 'previews' / version / 'future-state'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(folder / 'deltas.json', {'proposals': deltas})
    rows = ''.join('<tr><td>' + html.escape(item['status']) + '</td><td>' + html.escape(item['journey'])
                   + '</td><td>' + html.escape(item['change']) + '</td><td>' + html.escape(item['owner']) + '</td></tr>' for item in deltas)
    page = _page('Future-state draft', '<h1>Future-state draft</h1><p>Unapproved proposals are not requirements.</p><table><tr><th>Status</th><th>Journey</th><th>Change</th><th>Owner</th></tr>' + rows + '</table>')
    (folder / 'index.html').write_text(page)
    (folder / 'index.html').chmod(0o600)


def package_export(work, project, version, mode):
    """Write a draft mode package beside the approved single package."""
    version = _version(version)
    require(mode in MODES, 'Choose functional-parity or reimagine')
    current = Path(work) / 'previews' / version / 'current-state' / 'index.html'
    require(current.is_file(), 'Export the design mode before its package')
    if mode == 'reimagine':
        require((Path(work) / 'previews' / version / 'future-state' / 'deltas.json').is_file(), 'Reimagine needs the future-state draft')
    if mode == 'functional-parity':
        require(not (Path(work) / 'previews' / version / 'future-state').exists(), 'Parity package cannot include a future-state draft')
    mode_record = _read(Path(work) / 'previews' / version / 'mode.json')
    require(mode_record.get('context_fingerprint') == _context_fingerprint(work),
            'Human context changed this design version. Re-export it before packaging')
    conflicts = open_conflicts(work)
    files = {'README.md': _readme(mode).encode(), 'current-state.html': current.read_bytes()}
    site = Path(work) / (project.get('site') or {}).get('directory', '')
    for name in ('screens.json', 'openapi.json', 'api-contracts.json'):
        artifact = site / name
        if artifact.is_file():
            files['baseline/' + name] = artifact.read_bytes()
    if mode == 'reimagine':
        future = Path(work) / 'previews' / version / 'future-state'
        files['future-state.html'] = (future / 'index.html').read_bytes()
        files['deltas.json'] = (future / 'deltas.json').read_bytes()
    manifest = {
        'schema': 'reframe.mode-package.v1', 'mode': mode, 'version': version,
        'human_context': context_status(work)['status'],
        'final_handoff': 'BLOCKED' if conflicts else 'NOT_REQUESTED',
        'implementation_status': 'NOT_STARTED',
        'open_conflicts': [item['id'] for item in conflicts],
        'source_commit': (project.get('source') or {}).get('commit', ''),
        'files': {name: sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    for name, data in files.items():
        _scan(data, name)
        require(str(Path(work).resolve()).encode() not in data, 'Export contains a local workspace path')
    destination = Path(work) / 'packages' / version / mode
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = destination / 'ai-dlc-package.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for name in sorted(files):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, files[name])
    archive.chmod(0o600)
    return {'status': 'DRAFT_READY' if not conflicts else 'DRAFT_BLOCKED_FOR_APPROVAL',
            'archive': str(archive.relative_to(work)), 'final_handoff': manifest['final_handoff']}


def _readme(mode):
    return ('# Draft ' + mode + ' package\n\nThis is not the approved single AI-DLC package. '
            'Do not implement a proposed_unapproved item as a requirement. '
            'Tests and implementation are NOT RUN. No application has been built.\n')


def run_prefix(project_id, stamp, suffix):
    """Opt-in object-key shape. This does not create a bucket or move existing objects."""
    require(bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,40}', project_id)), 'Project id must be a short lowercase label')
    require(bool(re.fullmatch(r'\d{8}T\d{6}Z', stamp)), 'Run stamp must be UTC basic format')
    require(bool(re.fullmatch(r'[a-z0-9]{6}', suffix)), 'Run suffix must be six lowercase letters or digits')
    return 'projects/' + project_id + '/runs/' + stamp + '-' + suffix + '/'


def known_documentation_enum(schema, is_rules):
    """Refuse an unknown assessment enum. This does not call AWS."""
    if is_rules:
        require('fileLevel' in schema.get('properties', {}).get('reportScope', {}).get('enum', []), 'Business rule scope enum changed')
        return 'fileLevel'
    require('DETAILED' in schema.get('properties', {}).get('detailLevel', {}).get('enum', []), 'Documentation detail enum changed')
    return 'DETAILED'


def register_project(work, project, choice=None):
    """Record project identity locally. This does not create an AWS job or edit project.json."""
    source = project.get('source') or {}
    require(source.get('repository') and source.get('commit'), 'Saved source identity is incomplete')
    identity = {'repository': source['repository'], 'commit': source['commit']}
    path = Path(work) / 'project-registry.json'
    before = Path(work, 'project.json').read_bytes()
    if not path.is_file():
        record = {'identity': identity, 'fingerprint': _digest(identity), 'runs': [], 'aws_jobs_created': 0}
        _write(path, record)
        require(Path(work, 'project.json').read_bytes() == before, 'Registry changed project.json')
        return {'status': 'REGISTERED', 'fingerprint': record['fingerprint']}
    record = _read(path)
    require(record['identity']['repository'] == identity['repository'], 'Repository identity differs from the saved project')
    if record['identity']['commit'] == identity['commit']:
        require(Path(work, 'project.json').read_bytes() == before, 'Registry changed project.json')
        return {'status': 'REUSE', 'fingerprint': record['fingerprint'], 'aws_jobs_created': record['aws_jobs_created']}
    require(choice in {'assess-new-revision', 'reuse-baseline'}, 'Choose assess-new-revision or reuse-baseline')
    if choice == 'reuse-baseline':
        record.setdefault('unassessed_commits', []).append(identity['commit'])
    else:
        project_id = 'p' + sha256(identity['repository'].encode()).hexdigest()[:8]
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        prefix = run_prefix(project_id, stamp, identity['commit'][:6])
        record['runs'].append({'commit': identity['commit'], 'aws_job': None, 'prefix': prefix, 'uploaded': False})
        record['identity']['commit'] = identity['commit']
        record['fingerprint'] = _digest({'repository': identity['repository'], 'commit': identity['commit']})
    _write(path, record)
    require(Path(work, 'project.json').read_bytes() == before, 'Registry changed project.json')
    return {'status': 'RECORDED', 'choice': choice, 'aws_jobs_created': record['aws_jobs_created']}
