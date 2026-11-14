"""Opt-in design modes, human context, and extra packages.

Existing review sites, skip drafts, the single AI-DLC package, and project.json
stay in place. These commands write only beside those paths.
"""
import html
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from pilot import Blocked, require
import reframe_handoff as handoff

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
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


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
    conflicts = [(item.get('id'), item.get('status'), item.get('resolution')) for item in manifest.get('conflicts') or []]
    return _digest({'status': manifest.get('status'), 'items': items, 'conflicts': conflicts})


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
    if path.is_symlink():
        _quarantine(folder, path.name, 'symbolic link')
        raise Blocked('Context file was quarantined; symbolic links are not imported')
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
        'version': 1, 'privacy': 'private', 'layer': 'human-provided context',
        'verified_source_behavior': False,
    }
    manifest = context_status(work)
    if manifest['status'] == 'not_provided':
        manifest = {'status': 'provided', 'submissions': [], 'conflicts': []}
    require(all(item['id'] != submission['id'] for item in manifest['submissions']), 'This context file is already imported')
    extracted = folder / 'extracted' / (submission['id'] + '.txt')
    original = folder / 'submissions' / (submission['id'] + suffix)
    for target in (extracted, original):
        parent = target.parent
        if parent.is_dir():
            for existing in parent.iterdir():
                require(not (existing.name.casefold() == target.name.casefold() and existing.name != target.name),
                        'Context id collides with an existing file')
    _atomic_write(extracted, text.encode())
    _atomic_write(original, raw)
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
    if disposition in {'rejected', 'superseded'}:
        for conflict in manifest['conflicts']:
            if conflict['submission_id'] == submission_id and conflict['status'] == 'open':
                conflict['status'] = 'retained_inactive'
                _write(context_dir(work) / 'conflicts' / (conflict['id'] + '.json'), conflict)
    _write(context_dir(work) / 'manifest.json', manifest)
    return {'status': 'RECORDED', 'id': submission_id, 'disposition': disposition}


def resolve_conflict(work, conflict_id, resolution, by, note):
    """Record an explicit reconciliation. Accepting a statement does not do this."""
    require(resolution in {'keep-evidence', 'keep-human-statement', 'needs-more-evidence'},
            'Choose keep-evidence, keep-human-statement, or needs-more-evidence')
    require(by.strip() and note.strip(), 'A conflict resolution needs a person and a note')
    manifest = context_status(work)
    found = next((item for item in manifest.get('conflicts') or [] if item.get('id') == conflict_id), None)
    require(found and found.get('status') == 'open', 'Open conflict was not found')
    found['resolution'] = resolution
    found['resolved_by'] = by.strip()
    found['resolution_note'] = note.strip()
    found['status'] = 'open' if resolution == 'needs-more-evidence' else 'resolved'
    found['verified_source_behavior'] = False
    _write(context_dir(work) / 'conflicts' / (conflict_id + '.json'), found)
    _write(context_dir(work) / 'manifest.json', manifest)
    return {'status': 'RECORDED', 'id': conflict_id, 'conflict_status': found['status']}


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)
    root = path.parents[2] if path.parent.name in {'extracted', 'submissions', 'conflicts', 'decisions'} else path.parent
    require(root.resolve() in path.resolve().parents or path.resolve().parent == root.resolve(), 'Context write escaped its directory')


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


def _file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _require_hash(path, expected, label):
    require(Path(path).is_file() and expected and _file_sha(path) == expected,
            'Completed version integrity failure. The recorded hash does not match ' + label
            + '. The version was not overwritten.')


def _design_source(work, project):
    """Use the review site or the skip bundle named on the project. Folder order is not a choice."""
    site = project.get('site') or {}
    if site.get('directory'):
        source = Path(work) / site['directory']
        require(source.is_dir() and (source / 'screens.json').is_file(), 'Review site is missing')
        return source, True
    recorded = project.get('skip_bundle')
    require(recorded, 'Record the skip bundle before exporting a design mode')
    source = Path(work) / recorded
    if source.name != 'handoff':
        source = source / 'handoff'
    require(source.is_dir() and (source / 'screens.json').is_file(), 'Saved skip bundle is missing')
    return source, False


def _read_if(path):
    return _read(path) if Path(path).is_file() else None


def _screen_object(raw, source):
    """Turn a review object or a skip-bundle screen list into one screen document."""
    if isinstance(raw, dict) and isinstance(raw.get('screens'), list):
        screens = json.loads(json.dumps(raw))
    elif isinstance(raw, list):
        screens = {'screens': json.loads(json.dumps(raw)), 'journeys': [], 'apis': []}
    else:
        raise Blocked('Saved screens are neither a review object nor a skip-bundle list')
    if not screens.get('apis'):
        apis = _read_if(Path(source) / 'api-contracts.json')
        if isinstance(apis, list):
            screens['apis'] = apis
    if not screens.get('journeys'):
        ids = [item.get('id') for item in screens.get('screens') or [] if isinstance(item, dict) and item.get('id')]
        if ids:
            screens['journeys'] = [{
                'id': 'recorded', 'name': 'Recorded screens',
                'summary': 'Screens saved in this bundle.', 'screen_ids': ids,
            }]
    from reframe_preview import normalize_design
    return normalize_design(screens)


def _bundle_records(source):
    """Load the specification, rules, APIs, backlog, questions, and provenance the bundle recorded."""
    spec = _read_if(Path(source) / 'specification.json')
    if not isinstance(spec, dict):
        spec = {}
    rules = _read_if(Path(source) / 'rules.json')
    if not isinstance(rules, list):
        rules = spec.get('rules') if isinstance(spec.get('rules'), list) else []
    backlog = _read_if(Path(source) / 'implementation-backlog.json')
    if not isinstance(backlog, list):
        backlog = spec.get('work_items') if isinstance(spec.get('work_items'), list) else []
    questions = _read_if(Path(source) / 'decisions-needed.json')
    if not isinstance(questions, list):
        questions = spec.get('questions') if isinstance(spec.get('questions'), list) else []
    acceptance = _read_if(Path(source) / 'acceptance-catalog.json')
    if not isinstance(acceptance, list):
        acceptance = []
    provenance = _read_if(Path(source) / 'provenance.json')
    if not isinstance(provenance, dict):
        provenance = {}
    assumptions = _read_if(Path(source) / 'assumptions.json')
    sections = spec.get('sections') if isinstance(spec.get('sections'), dict) else {}
    return {'specification': spec, 'rules': rules, 'backlog': backlog, 'questions': questions,
            'acceptance': acceptance, 'provenance': provenance,
            'assumptions': assumptions if isinstance(assumptions, dict) else {}, 'sections': sections}


def _remember_path(paths, value):
    if isinstance(value, str) and value and value not in paths:
        paths.append(value)


def _source_paths(project, screens, rules=None):
    """Collect source paths the assessment upload or the saved rules actually name."""
    paths = []
    for item in project.get('source_paths') or []:
        _remember_path(paths, item)
    for screen in screens.get('screens') or []:
        if isinstance(screen, dict):
            for item in screen.get('source_paths') or []:
                _remember_path(paths, item)
    selected = project.get('selected')
    record = ((project.get('assessments') or {}).get(selected) or {}) if selected else {}
    directory = record.get('directory')
    if directory:
        manifest = _read_if(Path(directory) / 'source-upload-manifest.json')
        if isinstance(manifest, dict):
            for item in manifest.get('included') or []:
                if isinstance(item, dict):
                    _remember_path(paths, item.get('path'))
    for rule in rules or []:
        if isinstance(rule, dict):
            _remember_path(paths, rule.get('source_path'))
    for item in _pinned_inventory_paths(project):
        _remember_path(paths, item)
    return paths


def _pinned_inventory_paths(project):
    """Name copybooks and batch members in the pinned checkout. Layouts are not parsed."""
    source = project.get('source') or {}
    root = source.get('root')
    if not isinstance(root, str) or not root:
        return []
    folder = Path(root)
    if not folder.is_dir():
        return []
    from pilot import git
    import reframe_handoff as handoff
    try:
        listing = git(folder, 'ls-files')
    except Blocked:
        return []
    found = []
    for name in listing.splitlines():
        if handoff._kind(name):
            _remember_path(found, name)
    return found


def _verification_record(project):
    skip = project.get('verification_skip')
    if skip:
        return {'kind': 'recorded_skip', 'status': 'SKIPPED_BY_OWNER',
                'owner': skip.get('owner'), 'reason': skip.get('reason')}
    approvals = project.get('approvals') or {}
    if approvals.get('validation'):
        return {'kind': 'independent_review', 'status': 'INDEPENDENTLY_REVIEWED'}
    if approvals.get('assessment'):
        return {'kind': 'assessment_approval', 'status': 'ASSESSMENT_APPROVED'}
    return {'kind': 'none', 'status': 'UNAPPROVED'}


def _output_names(mode):
    names = [
        'design.json', 'current-state/index.html', 'current-state/screens.json',
        'current-state/api.html', 'current-state/usage.html', 'current-state/openapi.json',
    ]
    if mode == 'reimagine':
        names.extend(['future-state/index.html', 'future-state/screens.json', 'future-state/deltas.json'])
    return names


def _output_hashes(root, mode):
    return {name: _file_sha(Path(root) / name) for name in _output_names(mode)}


def _verify_outputs(root, mode, record):
    outputs = record.get('outputs') or {}
    if outputs:
        for name in _output_names(mode):
            _require_hash(Path(root) / name, outputs.get(name), name)
        return
    _require_hash(Path(root) / 'design.json', record.get('design_sha256'), 'design.json')
    _require_hash(Path(root) / 'current-state' / 'index.html', record.get('html_sha256'), 'current-state/index.html')


def _next_revision(work, version):
    match = re.fullmatch(r'v(\d+)', version or '')
    number = int(match.group(1)) + 1 if match else 2
    while (Path(work) / 'previews' / ('v' + str(number))).exists():
        number += 1
    return 'v' + str(number)


def _selection(work, project, mode, stack, screens, spec, provenance):
    source = project.get('source') or {}
    return {
        'repository': source.get('repository'),
        'source_commit': provenance.get('source_commit') or source.get('commit'),
        'assessment_sha256': provenance.get('assessment_sha256'),
        'verification': _verification_record(project),
        'context_fingerprint': _context_fingerprint(work),
        'mode': mode,
        'stack': (stack or '').strip() or 'unspecified',
        'inputs': _digest({
            'screens': screens.get('screens'), 'journeys': screens.get('journeys'),
            'apis': screens.get('apis'), 'rules': spec.get('rules'),
        }),
    }


def _same_selection(saved, selection):
    recorded = saved.get('selection')
    if not isinstance(recorded, dict):
        return (saved.get('mode') == selection['mode']
                and (saved.get('stack') or 'unspecified') == selection['stack']
                and saved.get('context_fingerprint') == selection['context_fingerprint'])
    return recorded == selection


def design_export(work, project, version, mode, stack):
    """Write one canonical design. A completed version is reused or left unchanged."""
    version = _version(version)
    require(mode in MODES, 'Choose functional-parity or reimagine')
    destination_root = _mode_root(work, version)
    source, review_site = _design_source(work, project)
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} if review_site else None
    records = _bundle_records(source)
    screens = _screen_object(_read(source / 'screens.json'), source)
    from reframe_artifacts import openapi_document, render_api_page, render_site, render_usage_page
    spec = {'scope': project.get('scope') or records['specification'].get('scope') or '',
            'rules': list(records['rules']), 'sections': json.loads(json.dumps(records['sections'])),
            'questions': list(records['questions']), 'tests': list(records['acceptance']),
            'work_items': list(records['backlog'])}
    spec_record = project.get('specification') or {}
    if spec_record.get('path') and (Path(work) / spec_record['path']).is_file():
        loaded = _read(Path(work) / spec_record['path'])
        for key in spec:
            if not spec[key] and loaded.get(key):
                spec[key] = loaded[key]
    _apply_current_context(work, spec, screens)
    stack_name = stack.strip() or 'unspecified'
    selected = project.get('selected')
    assessment = ((project.get('assessments') or {}).get(selected) or {}) if selected else {}
    provenance = {
        'repository': (project.get('source') or {}).get('repository'),
        'source_commit': records['provenance'].get('source_commit') or (project.get('source') or {}).get('commit'),
        'assessment_sha256': records['provenance'].get('assessment_sha256') or assessment.get('sha256'),
        'verification': _verification_record(project),
        'bundle': records['provenance'],
    }
    selection = _selection(work, project, mode, stack_name, screens, spec, provenance)
    mode_path = destination_root / 'mode.json'
    resumed = False
    if mode_path.is_file():
        saved = _read(mode_path)
        resumed = saved.get('status') == 'generating'
        if saved.get('status') == 'complete':
            if not _same_selection(saved, selection):
                raise Blocked('Completed version ' + version + ' is immutable. The next revision is '
                              + _next_revision(work, version) + '. No approval carries forward.')
            _verify_outputs(destination_root, mode, saved)
            return {'status': 'EXPORTED', 'mode': mode, 'reused': True,
                    'current_state': str((destination_root / 'current-state').relative_to(work))}
    destination_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(mode_path, {
        'mode': mode, 'version': version, 'stack': stack_name, 'status': 'generating',
        'context_fingerprint': selection['context_fingerprint'], 'selection': selection,
    })
    notice = ('<p>Design interpretation of the recorded review. Not a screenshot of the legacy system. '
              'Human context status: ' + html.escape(context_status(work)['status']) + '.</p>')
    current = render_site(spec, screens, project.get('decisions') or {})
    current = current.replace('</main>', notice + '</main>')
    destination = destination_root / 'current-state'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    (destination / 'index.html').write_text(current)
    (destination / 'index.html').chmod(0o600)
    _write(destination / 'screens.json', screens)
    (destination / 'api.html').write_text(render_api_page(screens.get('apis') or [], screens=screens.get('screens'), journeys=screens.get('journeys')))
    (destination / 'usage.html').write_text(render_usage_page([], project.get('settings') or {}))
    for name in ('api.html', 'usage.html'):
        (destination / name).chmod(0o600)
    _write(destination / 'openapi.json', openapi_document(
        screens.get('apis') or [], title='Draft review APIs',
        description='Proposed interfaces from the recorded review. This document does not start a server, does not send a request, and is not an approval.'))
    proposals, gaps = ([], [])
    if mode == 'reimagine':
        proposals, gaps = _write_future(work, version, screens)
    context = context_status(work)
    import reframe_handoff as handoff
    source_paths = _source_paths(project, screens, spec.get('rules'))
    fields, batches = handoff.inventories(source_paths)
    design = {
        'version': version, 'mode': mode, 'stack': stack_name,
        'screens': screens.get('screens') or [], 'journeys': screens.get('journeys') or [],
        'apis': screens.get('apis') or [], 'rules': spec.get('rules') or [],
        'sections': spec.get('sections') or {}, 'questions': spec.get('questions') or [],
        'backlog': records['backlog'], 'acceptance': records['acceptance'],
        'specification': spec,
        'context_dispositions': context.get('submissions') or [],
        'conflicts': context.get('conflicts') or [],
        'context_status': context.get('status'), 'proposals': proposals, 'gaps': gaps,
        'source_paths': source_paths,
        'inventories': {'fields': fields, 'batches': batches},
        'decisions': project.get('decisions') or {},
        'provenance': provenance,
    }
    design_path = destination_root / 'design.json'
    _write(design_path, design)
    outputs = _output_hashes(destination_root, mode)
    mode_record = {
        'mode': mode, 'version': version, 'stack': stack_name, 'status': 'complete',
        'human_context': context['status'], 'context_fingerprint': selection['context_fingerprint'],
        'selection': selection, 'final_approval': 'not_requested',
        'design_sha256': outputs['design.json'], 'html_sha256': outputs['current-state/index.html'],
        'outputs': outputs,
    }
    if review_site:
        mode_record['site_directory'] = (project.get('site') or {}).get('directory')
    _write(destination_root / 'mode.json', mode_record)
    if review_site:
        after = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
        require(before == after, 'Design export changed the existing review site')
    return {'status': 'EXPORTED', 'mode': mode, 'resumed': resumed,
            'current_state': str(destination.relative_to(work))}


def _apply_current_context(work, spec, screens):
    """Fold accepted current-state context into the generation input before rendering."""
    notes = []
    for item, text in _active(work, 'current-state'):
        note = ('Accepted human clarification by ' + item['by'] + ' (' + item['id']
                + '). This is not verified source behavior. ' + text[:2000])
        notes.append(note)
        for screen in screens.get('screens') or []:
            if screen.get('id') in (item.get('affected') or []):
                screen['uncertainties'] = (screen.get('uncertainties') or '') + ' ' + note
    if not notes:
        return
    sections = spec.setdefault('sections', {})
    if not isinstance(sections, dict):
        return
    current = sections.get('ui') if isinstance(sections.get('ui'), dict) else {}
    current['specification'] = ((current.get('specification') or '') + '\n' + '\n'.join(notes)).strip()
    sections['ui'] = current
    questions = spec.setdefault('questions', [])
    if isinstance(questions, list):
        questions.append({'id': 'human-context', 'status': 'attributed', 'text': '\n'.join(notes),
                          'layer': 'human-provided context'})


def _proposals(screens, accepted):
    """Build evidence-linked proposals. Accepted input stays unapproved until a delta approval."""
    proposals, gaps = [], []
    journeys = screens.get('journeys') or []
    if not journeys:
        gaps.append({'id': 'gap-design', 'target': 'journey',
                     'reason': 'No recorded journey is available for a reimagine proposal.'})
        return proposals, gaps
    for journey in journeys:
        related = [screen for screen in screens.get('screens') or [] if screen.get('id') in (journey.get('screen_ids') or [])]
        fields, actions, apis = [], [], []
        for screen in related:
            fields.extend(field for field in screen.get('fields') or [] if isinstance(field, str))
            actions.extend(action for action in screen.get('actions') or [] if isinstance(action, str))
            apis.extend(screen.get('api_ids') or [])
        evidence = [screen.get('id') for screen in related if screen.get('id')]
        matched = [(item, text) for item, text in accepted if journey.get('id') in (item.get('affected') or [])]
        if not (fields or actions or apis or evidence):
            gaps.append({'id': 'gap-' + str(journey.get('id')), 'target': 'journey', 'target_id': journey.get('id'),
                         'reason': 'Recorded journey has no field, action, validation, or interface to change.'})
            continue
        name = str(journey.get('name') or journey.get('id'))
        states = [state for screen in related for state in (screen.get('states') or []) if isinstance(state, str)]
        field = fields[0] if fields else ''
        action = actions[0] if actions else ''
        before = ('Journey ' + name + ' shows ' + (field or 'its recorded fields')
                  + ' and uses ' + (action or 'its recorded action') + '.')
        if matched:
            note = ' '.join(matched[0][1].split())
            after = 'Change ' + (field or name) + ' so that ' + note
            confirmed = ''
        elif field or action:
            after = ('Show ' + (field or 'the recorded field') + ' with an explicit confirmation before '
                     + (action or 'continue') + ', and keep the recorded state '
                     + (states[0] if states else 'recorded') + ' visible on the same step.')
            confirmed = 'Confirm ' + action if action else ''
        else:
            gaps.append({'id': 'gap-' + str(journey.get('id')), 'target': 'journey', 'target_id': journey.get('id'),
                         'reason': 'Missing design work: this journey has evidence but no field, action, validation, or interface to change.'})
            continue
        change = 'Before: ' + before + ' After: ' + after
        proposals.append({
            'id': 'proposal-' + str(journey.get('id')), 'status': 'proposed_unapproved',
            'target': 'journey', 'target_id': journey.get('id'), 'evidence': evidence,
            'rationale': 'Recorded journey ' + str(journey.get('id')) + ' uses ' + ', '.join(evidence or ['no screen'])
                         + '. Fields: ' + ', '.join(fields or ['none']) + '. Actions: ' + ', '.join(actions or ['none']) + '.',
            'impacts': fields + actions + [str(item) for item in apis],
            'acceptance_criteria': ['After this delta is authorized, ' + (field or name)
                                    + ' follows the after-state and the recorded validation remains visible. Until then this proposal is not a requirement.'],
            'before': before, 'after': after, 'change': change,
            'confirmed_action': confirmed, 'authorization': 'proposed_unapproved',
            'input_notes': [item['id'] for item, _text in matched],
        })
    if not proposals and not gaps:
        gaps.append({'id': 'gap-design', 'target': 'design',
                     'reason': 'No evidence-linked reimagine proposal could be formed.'})
    return proposals, gaps


def _write_future(work, version, screens):
    accepted = _active(work, 'future-state')
    proposals, gaps = _proposals(screens, accepted)
    folder = Path(work) / 'previews' / version / 'future-state'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(folder / 'deltas.json', {'proposals': proposals, 'gaps': gaps})
    future = json.loads(json.dumps(screens))
    by_journey = {item['target_id']: item for item in proposals}
    for journey in future.get('journeys') or []:
        proposal = by_journey.get(journey['id'], {})
        journey['summary'] = proposal.get('change') or journey.get('summary') or ''
    for screen in future.get('screens') or []:
        proposal = next((item for item in proposals if screen.get('id') in (item.get('evidence') or [])), None)
        if not proposal:
            continue
        screen['purpose'] = proposal['status'] + '. ' + proposal.get('after', '')
        if proposal.get('input_notes') and screen.get('fields'):
            screen['fields'] = [proposal.get('after', '')]
        elif proposal.get('confirmed_action'):
            screen['actions'] = [proposal['confirmed_action']] + [item for item in (screen.get('actions') or [])[1:]]
        screen['uncertainties'] = (screen.get('uncertainties') or '') + ' This future screen is not an approved requirement.'
    from reframe_artifacts import render_site
    scope = 'Future-state draft. Unapproved proposals are not requirements.'
    if gaps:
        scope += ' Missing design work: ' + '; '.join(item['reason'] for item in gaps)
    page = render_site({'scope': scope, 'rules': [], 'sections': {}, 'questions': [], 'tests': []}, future, {})
    (folder / 'index.html').write_text(page)
    (folder / 'index.html').chmod(0o600)
    _write(folder / 'screens.json', future)
    return proposals, gaps


def approve_delta(work, version, proposal_id, by, note):
    """Authorize one named proposal. Accepting the input note does not do this."""
    require(by.strip() and note.strip(), 'Named owner and delta rationale required')
    root = Path(work) / 'previews' / _version(version)
    require(not (root / 'completion.json').is_file(),
            'Completed version ' + version + ' is immutable. Choose the next version. No approval carries forward.')
    deltas_path = root / 'future-state' / 'deltas.json'
    require(deltas_path.is_file(), 'Export the reimagine design before approving a delta')
    deltas = _read(deltas_path)
    found = None
    for item in deltas.get('proposals') or []:
        if item.get('id') == proposal_id:
            found = item
    require(found, 'Unknown proposal')
    require(found.get('status') == 'proposed_unapproved', 'Only an unapproved proposal can receive delta approval')
    found['status'] = 'implementation_requirement'
    found['authorization'] = 'implementation_requirement'
    found['approved_by'] = by
    found['approval_note'] = note
    _write(deltas_path, deltas)
    design_path = root / 'design.json'
    design = _read(design_path) if design_path.is_file() else {}
    for item in design.get('proposals') or []:
        if item.get('id') == proposal_id:
            item.update(status='implementation_requirement', authorization='implementation_requirement',
                        approved_by=by, approval_note=note)
    if design_path.is_file():
        _write(design_path, design)
    future_screens = root / 'future-state' / 'screens.json'
    if future_screens.is_file():
        future = _read(future_screens)
        for screen in future.get('screens') or []:
            if screen.get('id') in (found.get('evidence') or []):
                screen['purpose'] = 'implementation_requirement. ' + found.get('change', '')
                screen['proposed_status'] = 'implementation_requirement'
                screen['uncertainties'] = 'This delta is an implementation requirement.'
        _write(future_screens, future)
        from reframe_artifacts import render_site
        page = render_site({
            'scope': 'Approved delta ' + proposal_id + '. Status implementation_requirement for the named change only.',
            'rules': [], 'sections': {}, 'questions': [], 'tests': [],
        }, future, {})
        html_path = root / 'future-state' / 'index.html'
        html_path.write_text(page)
        html_path.chmod(0o600)
    mode_path = root / 'mode.json'
    if mode_path.is_file():
        record = _read(mode_path)
        outputs = _output_hashes(root, design.get('mode') or record.get('mode') or 'reimagine')
        record['outputs'] = outputs
        record['design_sha256'] = outputs.get('design.json')
        record['html_sha256'] = outputs.get('current-state/index.html')
        record['status'] = 'complete'
        _write(mode_path, record)
    return {'status': 'DELTA_APPROVED', 'id': proposal_id, 'authorization': 'implementation_requirement'}


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
    completion_path = Path(work) / 'previews' / version / 'completion.json'
    archive = Path(work) / 'packages' / version / mode / 'ai-dlc-package.zip'
    if completion_path.is_file():
        recorded = _read(completion_path)
        _verify_outputs(Path(work) / 'previews' / version, mode, recorded)
        _require_hash(archive, recorded.get('zip_sha256'), 'ai-dlc-package.zip')
        return {'status': recorded.get('package_status') or 'DRAFT_READY',
                'archive': str(archive.relative_to(work)), 'final_handoff': recorded.get('final_handoff'),
                'reused': True}
    design = _read(Path(work) / 'previews' / version / 'design.json')
    conflicts = [item for item in (design.get('conflicts') or []) if item.get('status', 'open') == 'open']
    files = _mode_package_files(work, project, version, mode, current.read_bytes(), conflicts)
    manifest = json.loads(files['manifest.json'].decode())
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
    status = 'DRAFT_READY' if not conflicts else 'DRAFT_BLOCKED_FOR_APPROVAL'
    design_path = Path(work) / 'previews' / version / 'design.json'
    _write(completion_path, {
        'version': version, 'mode': mode, 'status': 'complete', 'package_status': status,
        'context_fingerprint': mode_record.get('context_fingerprint'),
        'design_sha256': _file_sha(design_path), 'zip_sha256': _file_sha(archive),
        'outputs': mode_record.get('outputs') or {},
        'final_handoff': manifest['final_handoff'],
    })
    return {'status': status, 'archive': str(archive.relative_to(work)), 'final_handoff': manifest['final_handoff']}


def _mode_package_files(work, project, version, mode, current_html, conflicts):
    """Assemble a draft package with the same handoff records as the approved builder."""
    import reframe_handoff as handoff
    from reframe_artifacts import openapi_document
    site = Path(work) / (project.get('site') or {}).get('directory', '')
    design_path = Path(work) / 'previews' / version / 'design.json'
    require(design_path.is_file(), 'Export the canonical design before its package')
    design = _read(design_path)
    screens = {'screens': design.get('screens') or [], 'journeys': design.get('journeys') or [],
               'apis': design.get('apis') or []}
    spec_body = design.get('specification') if isinstance(design.get('specification'), dict) else {}
    rules = design.get('rules') if isinstance(design.get('rules'), list) and design['rules'] else (
        spec_body.get('rules') if isinstance(spec_body.get('rules'), list) else [])
    if isinstance(design.get('backlog'), list) and design['backlog']:
        work_items = design['backlog']
    elif isinstance(spec_body.get('work_items'), list) and spec_body['work_items']:
        work_items = spec_body['work_items']
    elif rules:
        work_items = []
    else:
        work_items = [{
            'id': 'GAP-1', 'title': 'Unresolved source behavior stays open', 'depends_on': [], 'status': 'NOT_RUN',
        }]
    staging = Path(work) / 'packages' / version / ('.' + mode + '-stage')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    handoff.write_into(staging, rules, design.get('source_paths') or [], screens.get('apis') or [], _write)
    files = {}
    for path in sorted(staging.rglob('*')):
        if path.is_file():
            files[path.relative_to(staging).as_posix()] = path.read_bytes()
    shutil.rmtree(staging)
    specification = {
        'scope': spec_body.get('scope') or '',
        'evidence_status': design.get('context_status') or 'not_provided',
        'assumptions': ['Draft from the recorded review. Equivalence is NOT RUN.'],
        'rules': rules, 'work_items': work_items,
        'sections': spec_body.get('sections') or design.get('sections') or {},
        'questions': design.get('questions') or [],
        'excluded_scope': spec_body.get('excluded_scope') or [],
    }
    files['specification.json'] = (json.dumps(specification, indent=2, sort_keys=True) + '\n').encode()
    files['implementation-backlog.json'] = (json.dumps(work_items, indent=2, sort_keys=True) + '\n').encode()
    if design.get('acceptance'):
        files['acceptance-catalog.json'] = (json.dumps(design['acceptance'], indent=2, sort_keys=True) + '\n').encode()
    if design.get('questions'):
        files['decisions-needed.json'] = (json.dumps(design['questions'], indent=2, sort_keys=True) + '\n').encode()
    if design.get('rules'):
        files['rules.json'] = (json.dumps(design['rules'], indent=2, sort_keys=True) + '\n').encode()
    files['design.json'] = design_path.read_bytes()
    files['current-state.html'] = current_html
    version_root = Path(work) / 'previews' / version
    current_dir = version_root / 'current-state'
    files['screens.json'] = (current_dir / 'screens.json').read_bytes()
    files['api.html'] = (current_dir / 'api.html').read_bytes()
    openapi = current_dir / 'openapi.json'
    files['openapi.json'] = openapi.read_bytes() if openapi.is_file() else (json.dumps(openapi_document(
        screens.get('apis') or [], title='Draft review APIs',
        description='Proposed interfaces from the recorded review. This document does not start a server, does not send a request, and is not an approval.'), indent=2) + '\n').encode()
    files['context-conflicts.json'] = (json.dumps(design.get('conflicts') or [], indent=2, sort_keys=True) + '\n').encode()
    files['inventories.json'] = (json.dumps(design.get('inventories') or {}, indent=2, sort_keys=True) + '\n').encode()
    contracts = version_root / 'api-contracts.json'
    if not contracts.is_file():
        contracts = site / 'api-contracts.json'
    if contracts.is_file():
        files['api-contracts.json'] = contracts.read_bytes()
    if mode == 'reimagine':
        future = Path(work) / 'previews' / version / 'future-state'
        files['future-state.html'] = (future / 'index.html').read_bytes()
        files['deltas.json'] = (future / 'deltas.json').read_bytes()
        files['future-screens.json'] = (future / 'screens.json').read_bytes()
    files['README.md'] = _readme(mode).encode()
    files['reframe-notice.md'] = handoff.framework_notice().encode()
    files['AGENTS.md'] = (b'# Engineering instructions\n\nRead specification.json, decisions are not implied, and leave tests NOT RUN.\n')
    files['SKILL.md'] = (b'---\nname: draft-mode-implementation\ndescription: Implement one approved backlog item from this draft package.\n---\n\n'
                         b'Read AGENTS.md. Do not implement a proposed_unapproved item. Do not claim a test passed.\n')
    files['ai-dlc.json'] = (json.dumps({
        'version': 1, 'phase': 'DRAFT_HANDOFF', 'mode': mode, 'stack': design.get('stack') or 'unspecified',
        'implementation_status': 'NOT_STARTED', 'equivalence': 'NOT_RUN',
        'final_handoff': 'BLOCKED' if conflicts else 'NOT_REQUESTED',
    }, indent=2, sort_keys=True) + '\n').encode()
    checksums = {name: sha256(data).hexdigest() for name, data in sorted(files.items())}
    files['checksums.json'] = (json.dumps(checksums, indent=2, sort_keys=True) + '\n').encode()
    manifest = {
        'schema': 'reframe.mode-package.v1', 'mode': mode, 'version': version,
        'package_status': 'DRAFT',
        'design_gaps': [item.get('id') for item in (design.get('gaps') or [])],
        'human_context': design.get('context_status') or 'not_provided',
        'final_handoff': 'BLOCKED' if conflicts else 'NOT_REQUESTED',
        'implementation_status': 'NOT_STARTED',
        'open_conflicts': [item['id'] for item in conflicts],
        'source_commit': (design.get('provenance') or {}).get('source_commit') or '',
        'files': {name: sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    return files


def _readme(mode):
    return ('# Draft ' + mode + ' package\n\nThis is not the approved single AI-DLC package. '
            'Do not implement a proposed_unapproved item as a requirement. '
            'Tests and implementation are NOT RUN. No application has been built.\n'
            'reframe-notice.md applies only to copied Reframe framework instructions.\n')


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


def canonical_repository(url):
    """Separate host, owner, and repository. A fork is a different owner."""
    value = (url or '').strip()
    if value.endswith('.git'):
        value = value[:-4]
    match = re.fullmatch(r'https?://([^/]+)/([^/]+)/([^/]+)', value)
    if not match:
        return {'kind': 'unknown-remote', 'repository': value}
    host, owner, repo = match.group(1).casefold(), match.group(2).casefold(), match.group(3).casefold()
    return {'kind': 'git', 'host': host, 'owner': owner, 'repository': repo,
            'canonical': 'https://' + host + '/' + owner + '/' + repo}


def _same_repository(left, right):
    first, second = canonical_repository(left), canonical_repository(right)
    if first.get('canonical') and second.get('canonical'):
        return first['canonical'] == second['canonical']
    return left == right


def resource_plan(project, intent, stamp='20261111T120000Z', suffix='abc123'):
    """Plan resource reuse without calling AWS or creating a job."""
    require(intent in {'resume', 'new-assessment', 'reuse-baseline', 'new-project'}, 'Unknown run intent')
    source = project.get('source') or {}
    identity = canonical_repository(source.get('repository'))
    plan = {'status': 'PLAN_ONLY', 'aws_jobs_created': 0, 'job_created': False, 'upload': False,
            'intent': intent, 'identity': identity.get('canonical') or identity.get('repository')}
    if intent == 'new-project':
        plan.update(resources='isolated', approval_required=True)
    elif intent == 'resume':
        plan.update(resources='same-run', same_job=True)
    elif intent == 'reuse-baseline':
        plan.update(resources='saved-baseline')
    else:
        project_id = 'p' + sha256((identity.get('canonical') or '').encode()).hexdigest()[:8]
        plan.update(resources='reuse-saved', approval_required=True, prefix=run_prefix(project_id, stamp, suffix))
    return plan


def review_cycle(findings, attempts):
    """Bound executor repair. A human-only finding is not auto-approved."""
    require(isinstance(attempts, int) and 1 <= attempts <= 3, 'Review attempts exhausted')
    if any(isinstance(item, dict) and item.get('decision') == 'NEEDS_HUMAN_DECISION' for item in findings):
        return {'decision': 'NEEDS_HUMAN_DECISION', 'attempts': attempts}
    if findings:
        require(attempts < 3, 'Review attempts exhausted')
        return {'decision': 'FAIL', 'attempts': attempts}
    return {'decision': 'PASS', 'attempts': attempts}


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
        _remember(work, source['repository'])
        return {'status': 'REGISTERED', 'fingerprint': record['fingerprint']}
    record = _read(path)
    require(_same_repository(record['identity']['repository'], identity['repository']), 'Repository identity differs from the saved project')
    if record['identity']['commit'] == identity['commit']:
        require(Path(work, 'project.json').read_bytes() == before, 'Registry changed project.json')
        _remember(work, source['repository'])
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
    _remember(work, source['repository'])
    return {'status': 'RECORDED', 'choice': choice, 'aws_jobs_created': record['aws_jobs_created']}


def _remember(work, repository):
    """Add the workspace to the private index. A failure leaves the registry result intact."""
    try:
        import reframe_projects as projects
        projects.remember_workspace(work, repository)
    except (OSError, UnicodeError, json.JSONDecodeError, Blocked):
        return
