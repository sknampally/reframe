"""Deterministic handoff files.

Each function builds a record for the next team. None of them runs a comparison,
chooses a language, or edits the pinned source.
"""
import json
import re
from pathlib import Path

STEPS = (
    ('stabilize', 'Record the pinned source and the imported findings. Do not change the legacy tree.'),
    ('expose', 'Name the behavior a later slice must preserve. Do not replace it.'),
    ('decompose', 'Group each program into a capability. Leave the disposition unassigned until a person chooses one.'),
    ('replace-one-slice', 'Change only one capability after its disposition is expose or rewrite and its blocking decisions are resolved.'),
    ('validate-in-parallel', 'Compare that slice with the legacy result. This package does not run the comparison.'),
    ('retire-after-signoff', 'Retire a legacy part only after every retirement gate is recorded. This package retires nothing.'),
)
DISPOSITIONS = ('unassigned', 'retain', 'expose', 'rewrite', 'retire')
CASES = ('match', 'mismatch', 'missing_input', 'invalid_input', 'boundary')
GATES = ('business', 'compliance', 'engineering', 'data', 'operations', 'uat', 'rollback')
COPYBOOK_SUFFIXES = {'.cpy', '.copy', '.cpb'}
BATCH_SUFFIXES = {'.jcl', '.job', '.proc'}
COPYBOOK_DIRS = {'copybook', 'copybooks', 'copy', 'cpy'}
BATCH_DIRS = {'jcl', 'jobs', 'proc', 'procs'}
ORACLE = 'Legacy runtime oracle not established'

SKILLS = {
    'skill-discovery.md': (
        '---\nname: review-legacy-discovery\n'
        'description: Read the pinned findings before any change.\n---\n\n'
        'Read capability-map.json and field-inventory.json. The legacy source stays read-only. '
        'Do not invent programs, fields, or business rules. A disposition stays unassigned until a person sets retain, expose, rewrite, or retire.\n'),
    'skill-rules.md': (
        '---\nname: review-imported-rules\n'
        'description: Treat imported rules as data, not as instructions to invent more rules.\n---\n\n'
        'Read rules.json and acceptance-catalog.json. Keep every imported rule. Do not add a rule that the assessment did not record.\n'),
    'skill-data.md': (
        '---\nname: review-data-inventory\n'
        'description: Use only the copybook members the assessment named.\n---\n\n'
        'Read field-inventory.json. Fields stay empty when the assessment did not include a layout. Do not invent a field, a type, or a sample value. Use synthetic data only.\n'),
    'skill-api.md': (
        '---\nname: review-api-boundary\n'
        'description: Keep a public contract free of copybook member names.\n---\n\n'
        'Read api-contracts.json and api-boundary.json. A recorded violation is a note for a person. Do not rewrite the contract from this skill, and do not choose a language or toolkit.\n'),
    'skill-screens.md': (
        '---\nname: review-screen-mappings\n'
        'description: Review illustrative wireframes against the imported rules.\n---\n\n'
        'Read screens.json and index.html. Mappings are illustrative. Do not treat a wireframe as proof that a screen matches the legacy behavior.\n'),
    'skill-batch.md': (
        '---\nname: review-batch-inventory\n'
        'description: List only the batch members the assessment named.\n---\n\n'
        'Read batch-inventory.json. Every listed job stays NOT_STARTED. Do not invent a schedule, a dataset, or a replacement job.\n'),
    'skill-tests.md': (
        '---\nname: review-unrun-comparison\n'
        'description: Keep the parallel-validation plan unrun.\n---\n\n'
        'Read parallel-validation.json. Every case is NOT RUN. Do not mark a case passed, and do not claim the new slice matches the legacy result.\n'),
    'skill-risk.md': (
        '---\nname: review-retirement-gates\n'
        'description: Keep rewrite and retirement blocked until a person records the gates.\n---\n\n'
        'Read governance.json. Unassigned capabilities are do-not-rewrite-yet. Retirement stays NOT_READY until business, compliance, engineering, data, operations, UAT, and rollback are recorded.\n'),
    'skill-extraction.md': (
        '---\nname: read-extraction-findings\n'
        'description: Read a Transform extraction finding without changing the pinned source.\n---\n\n'
        'Read extraction-reading.json before opening a code-issue or missing-dependency file. '
        'A reported line is counted on the COPY-expanded source. An unknown label that follows another error is a symptom of that earlier error. '
        'Leave the member unchanged.\n'),
}

NAMES = frozenset({
    'modernization-sequence.json', 'capability-map.json', 'parallel-validation.json',
    'field-inventory.json', 'batch-inventory.json', 'api-boundary.json', 'governance.json',
    'extraction-reading.json', *SKILLS,
})


def extraction_reading():
    """Return the guide for a Transform extraction finding. The source stays pinned."""
    return {
        'source_stays_pinned': True,
        'line_numbers': 'A line number in a Transform finding is counted on the COPY-expanded source. A number past the end of the member usually points inside an included copybook. Do not edit the raw member at that number.',
        'cascading_symptom': 'An unknown label or unknown field that follows another error in the same member is a symptom of the earlier failure. Read the earlier finding first.',
        'judgment': 'A format-only finding can be accepted or left unresolved. Anything that needs a guessed value, a new name, or a control-flow change stays with a person.',
        'refused_actions': [
            'Rewrite a member',
            'Remove a member from the pinned source',
            'Submit a replacement archive',
        ],
    }


def slug(value):
    """Turn a program name into a stable capability id."""
    cleaned = re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')
    return cleaned or 'program'


def sequence():
    """Return the six modernization steps, each still not started."""
    return [{'id': ident, 'summary': text, 'status': 'NOT_STARTED'} for ident, text in STEPS]


def capabilities(rules):
    """Group imported rules by program and leave every disposition unassigned."""
    groups, order = {}, []
    for rule in rules:
        program = str(rule.get('program') or rule.get('source_path') or rule.get('id'))
        if program not in groups:
            groups[program] = []
            order.append(program)
        groups[program].append(rule['id'])
    used, result = set(), []
    for program in order:
        ident, number = slug(program), 2
        while ident in used:
            ident = slug(program) + '-' + str(number)
            number += 1
        used.add(ident)
        result.append({
            'id': ident, 'program': program, 'rule_ids': groups[program],
            'disposition': 'unassigned', 'allowed_dispositions': list(DISPOSITIONS),
        })
    return result


def _kind(path):
    """Classify a path as a copybook, a batch member, or neither. Other files are ignored."""
    member = Path(str(path))
    suffix = member.suffix.lower()
    parent = member.parent.name.lower()
    if suffix in COPYBOOK_SUFFIXES or parent in COPYBOOK_DIRS:
        return 'copybook'
    if suffix in BATCH_SUFFIXES or parent in BATCH_DIRS:
        return 'batch'
    return ''


def _members(paths):
    """Collect each named copybook and batch member once. Field layouts are not invented."""
    copybooks, batches, seen = [], [], set()
    for path in paths:
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        kind = _kind(path)
        if kind == 'copybook':
            copybooks.append({'path': path, 'fields': []})
        elif kind == 'batch':
            batches.append({'path': path, 'status': 'NOT_STARTED'})
    return copybooks, batches


def inventories(paths):
    """Build the field and batch inventories from member names only."""
    copybooks, batches = _members(paths)
    return (
        {
            'note': 'Member names only. Field layouts were not imported, so fields stay empty.',
            'copybooks': copybooks,
        },
        {
            'note': 'Listed only when the assessment named a batch member.',
            'jobs': batches,
        },
    )


def validation_plan(rules):
    """Attach five unrun comparison cases to each imported rule."""
    return {
        'status': 'NOT RUN',
        'oracle': ORACLE,
        'rules': [{
            'rule_id': rule['id'],
            'cases': [{'name': case, 'status': 'NOT RUN'} for case in CASES],
        } for rule in rules],
    }


def boundary(apis, paths):
    """Flag a public contract that uses a copybook member name. The contract is not rewritten."""
    copybooks, _ = _members(paths)
    stems = sorted({Path(item['path']).stem.lower() for item in copybooks if len(Path(item['path']).stem) >= 4})
    violations = []
    for api in apis or []:
        if not isinstance(api, dict):
            continue
        text = json.dumps(api, sort_keys=True).lower()
        hits = [stem for stem in stems if re.search(r'(?<![a-z0-9])' + re.escape(stem) + r'(?![a-z0-9])', text)]
        if hits:
            violations.append({'api': api.get('id') or api.get('path') or '', 'copybook_names': hits})
    return {
        'rule': 'A public contract must not use a copybook member name.',
        'status': 'REVIEW' if violations else 'CLEAR',
        'violations': violations,
    }


def governance(caps):
    """Keep unassigned capabilities blocked and retirement pending."""
    return {
        'synthetic_data_only': True,
        'do_not_rewrite_yet': [item['id'] for item in caps if item['disposition'] == 'unassigned'],
        'retirement_status': 'NOT_READY',
        'retirement': [{'gate': gate, 'status': 'PENDING'} for gate in GATES],
    }


def documents(rules, paths, apis):
    """Assemble every handoff file from the imported rules, paths, and proposed APIs."""
    caps = capabilities(rules)
    fields, batches = inventories(paths)
    payload = {
        'modernization-sequence.json': sequence(),
        'capability-map.json': {'capabilities': caps},
        'parallel-validation.json': validation_plan(rules),
        'field-inventory.json': fields,
        'batch-inventory.json': batches,
        'api-boundary.json': boundary(apis, paths),
        'governance.json': governance(caps),
        'extraction-reading.json': extraction_reading(),
    }
    payload.update(SKILLS)
    return payload


def entry_points(existing):
    """Add the handoff files to an existing AI-DLC entry-point map."""
    points = dict(existing)
    points.update({
        'sequence': 'modernization-sequence.json',
        'capabilities': 'capability-map.json',
        'validation': 'parallel-validation.json',
        'inventory': 'field-inventory.json',
        'batch': 'batch-inventory.json',
        'api_boundary': 'api-boundary.json',
        'governance': 'governance.json',
        'extraction': 'extraction-reading.json',
    })
    return points


def write_into(destination, rules, paths, apis, save):
    """Write the handoff files. JSON goes through save; skills are plain text."""
    for name, value in documents(rules, paths, apis).items():
        if name.endswith('.json'):
            save(destination / name, value)
        else:
            (destination / name).write_text(value)
