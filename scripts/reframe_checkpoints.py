"""Bounded, independently reviewed evidence units. No cloud or model calls in planning."""
import json
import re
from pathlib import Path

from pilot import Blocked, require, sha, tracked_file, git
from reframe_artifacts import digest

UNIT_BYTES = 60000
CHECKPOINT_BYTES = 14000
MAX_LINES = 250


def json_projection(value):
    """Lossless pointer records; repeated object/array values become explicit references."""
    lines, seen = [], {}
    def visit(item, pointer):
        """Record one JSON value, and point at an earlier copy when the same value repeats."""
        if isinstance(item, (dict, list)) and item:
            fingerprint = digest(item)
            if fingerprint in seen:
                lines.append(json.dumps({'pointer':pointer, 'same_value_as':seen[fingerprint]}, ensure_ascii=False))
                return
            seen[fingerprint] = pointer
            lines.append(json.dumps({'pointer':pointer, 'container':'object' if isinstance(item, dict) else 'array'}))
            entries = item.items() if isinstance(item, dict) else enumerate(item)
            for key, child in entries:
                token = str(key).replace('~', '~0').replace('/', '~1')
                visit(child, pointer + '/' + token)
        else:
            lines.append(json.dumps({'pointer':pointer, 'value':item}, ensure_ascii=False))
    visit(value, '')
    return lines


def text_of(raw):
    """Decode evidence. A text file with a few non-UTF-8 bytes stays readable."""
    try:
        return raw.decode('utf-8')
    except UnicodeError:
        if b'\0' in raw:
            raise
        return raw.decode('latin-1')


def evidence_lines(raw, area):
    """Split evidence into lines. JSON assessment files become pointer records so structure is kept."""
    text = text_of(raw)
    if area == 'assessment':
        try:
            value = json.loads(text)
        except ValueError:
            pass
        else:
            if isinstance(value, (dict, list)):
                projected = json_projection(value)
                encoded = sum(len(line.encode()) + 1 for line in projected)
                # A projection larger than one segment keeps the original JSON. The review still sees the finding.
                if encoded <= UNIT_BYTES and len(projected) <= MAX_LINES:
                    return projected, 'json-pointer-records-v1'
    return text.splitlines(), 'original-lines'


def segments(area, name, path, expected):
    """Split one evidence file into bounded segments. A line that is too long is marked opaque."""
    require(sha(path) == expected, 'Evidence changed while planning')
    raw = path.read_bytes()
    base = {'area': area, 'path': name, 'sha256': expected}
    try:
        require(len(raw) <= 10000000 and b'\0' not in raw, 'Opaque evidence')
        lines, format_name = evidence_lines(raw, area)
        base['format'] = format_name
    except (Blocked, UnicodeError):
        return [{**base, 'start': 0, 'end': 0, 'opaque': True, 'bytes': len(raw)}]
    if not lines:
        return [{**base, 'start': 0, 'end': 0, 'empty': True, 'bytes': 0}]
    result, start, size = [], 1, 0
    for number, line in enumerate(lines, 1):
        length = len(json.dumps(f'{number}: {line}').encode()) + 2
        # A single overlong line is explicitly unresolved, never silently truncated.
        if length > UNIT_BYTES // 2:
            if number > start:
                result.append({**base, 'start': start, 'end': number - 1, 'bytes': size})
            result.append({**base, 'start': number, 'end': number, 'opaque': True, 'bytes': length})
            start, size = number + 1, 0
            continue
        if size + length > UNIT_BYTES // 2 or number - start >= MAX_LINES:
            result.append({**base, 'start': start, 'end': number - 1, 'bytes': size})
            start, size = number, 0
        size += length
    if start <= len(lines):
        result.append({**base, 'start': start, 'end': len(lines), 'bytes': size})
    return result


def hidden_source(work, project, read):
    """True when a saved plan marked source opaque only because it was not UTF-8."""
    rel = (project.get('checkpoints') or {}).get('plan_path')
    if not rel:
        return False
    root = Path(project['source']['root'])
    for unit in read(work / rel).get('units', []):
        for part in unit.get('segments', []):
            if part.get('area') != 'source' or not part.get('opaque'):
                continue
            raw = (root / part['path']).read_bytes()
            if b'\0' in raw or len(raw) > 10000000:
                continue
            try:
                raw.decode('utf-8')
            except UnicodeError:
                return True
    return False


def match_source(label, source_names):
    """Return the one tracked source file named by a finding, or nothing when the name is ambiguous."""
    if not isinstance(label, str) or not label.strip():
        return None
    label = label.strip().replace('\\', '/')
    if label in source_names:
        return label
    base = Path(label).name.lower()
    matches = [name for name in source_names if Path(name).name.lower() == base]
    if len(matches) == 1:
        return matches[0]
    stem = Path(base).stem
    matches = [name for name in source_names if Path(name).stem.lower() == stem]
    return matches[0] if len(matches) == 1 else None


def stated_member(path):
    """Read the program name a documentation finding states. Other finding shapes stay unmatched."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    overview = value.get('documentation')
    if not isinstance(overview, dict):
        return None
    overview = overview.get('high_level_overview')
    if not isinstance(overview, dict):
        return None
    program = overview.get('program_information')
    if not isinstance(program, dict) or not isinstance(program.get('name'), str):
        return None
    return program['name'].strip() or None


def make_plan(project, assessment_dir, run, catalog):
    """Group source and assessment evidence into units a reviewer can check one at a time."""
    records = []
    root = Path(project['source']['root'])
    # Do not filter tracked files by extension or silently discard unsafe names.
    source_names = sorted(git(root, 'ls-files').splitlines())
    for name in source_names:
        path = tracked_file(root, name)
        records.extend(segments('source', name, path, sha(path)))
    expected = {x['path']: x['sha256'] for x in run['assessment_index']}
    aliases, content_paths = [], {}
    for entry in sorted(catalog, key=lambda x: x['path']):
        path = assessment_dir / 'assessment' / entry['path']
        require(path.is_file() and not path.is_symlink()
                and path.resolve().is_relative_to((assessment_dir / 'assessment').resolve()), 'Unsafe assessment path')
        fingerprint = expected[entry['path']]
        require(sha(path) == fingerprint, 'Assessment changed while planning')
        if fingerprint in content_paths:
            aliases.append({'path':entry['path'], 'same_bytes_as':content_paths[fingerprint], 'sha256':fingerprint})
        else:
            content_paths[fingerprint] = entry['path']
            records.extend(segments('assessment', entry['path'], path, fingerprint))
    unit_limit = min(90000, max(1000, project['settings']['max_context_bytes'] - CHECKPOINT_BYTES - 65000))
    labels = {x['path']:x.get('label',x['path']) for x in catalog}
    stated = {}
    def focus(record):
        """Name the source file an assessment record belongs to."""
        if record['area'] == 'source': return record['path']
        label = re.sub(r'-(cbl|jcl|cpy|bms|sql|proc)\.json$', r'.\1', labels[record['path']], flags=re.IGNORECASE)
        matched = match_source(label, source_names)
        if matched:
            return matched
        if record['path'] not in stated:
            stated[record['path']] = stated_member(assessment_dir / 'assessment' / record['path'])
        return match_source(stated[record['path']], source_names) or '@assessment'
    for record in records: record['focus'] = focus(record)
    linked = {r['focus'] for r in records if r['area']=='assessment' and r['focus']!='@assessment'}
    if linked:
        unlinked_source = sorted({r['path'] for r in records if r['area']=='source' and r['focus'] not in linked})
        unlinked_assessment = {r['path'] for r in records if r['area']=='assessment' and r['focus']=='@assessment'}
        file_bytes, by_focus = {}, {}
        for record in records:
            file_bytes[record['path']] = file_bytes.get(record['path'], 0) + record.get('bytes', 0)
            if record['area'] == 'assessment' and record['focus'] in linked:
                by_focus.setdefault(record['focus'], set()).add(record['path'])
                if record['path'] not in stated:
                    stated[record['path']] = stated_member(assessment_dir / 'assessment' / record['path'])
        chosen = {name: min(paths, key=lambda path: (0 if stated.get(path) else 1, file_bytes[path], path))
                  for name, paths in by_focus.items()}
        kept = set(chosen.values())
        unlinked_assessment.update(path for paths in by_focus.values() for path in paths if path not in kept)
        unlinked_assessment = sorted(unlinked_assessment)
        records = [r for r in records if r['focus'] in linked and (r['area'] == 'source' or r['path'] in kept)]
    else:
        unlinked_source, unlinked_assessment = [], []
        for record in records:
            if record['area']=='source' and record['focus'] not in linked: record['focus']='@source-support'
    records.sort(key=lambda r:(r['focus']=='@assessment', r['focus'], r['area'] != 'source', r['path'], r['start']))
    units, batch, size = [], [], 0
    def same_rule(existing, record):
        """True when two segments belong in the same review unit."""
        if existing['focus'] != record['focus']:
            return False
        return not (existing['area'] == 'source' and record['area'] == 'source' and existing['path'] != record['path'])
    for record in records:
        weight = min(record['bytes'], UNIT_BYTES // 2) + len(json.dumps(record).encode())
        if batch and (size + weight > unit_limit or not same_rule(batch[0], record)):
            units.append({'id': f'U{len(units)+1:04d}', 'focus':batch[0]['focus'], 'segments': batch})
            batch, size = [], 0
        batch.append(record); size += weight
    if batch:
        units.append({'id': f'U{len(units)+1:04d}', 'focus':batch[0]['focus'], 'segments': batch})
    require(units, 'No evidence units available')
    excluded = sorted(set(expected) - {x['path'] for x in catalog})
    return {'version': 1, 'source_commit': project['source']['commit'],
            'assessment': project['assessments'][project['selected']]['sha256'],
            'scope': project['scope'], 'units': units,
            'catalog_exclusions': excluded, 'exact_duplicate_assessments':aliases,
            'projection': 'JSON pointer records preserve values and identify repeated objects/arrays by exact-value reference. Line numbers in these records are projection lines, not original JSON lines.',
            'exclusion_policy': 'Operational logs, duplicate HTML and provenance metadata; originals remain sealed.',
            'unlinked_source': unlinked_source, 'unlinked_assessment': unlinked_assessment,
            'minimum_calls': len(units) * 2 + 2,
            'estimate_note': 'Lower bound: executor and reviewer for each paired program, plus synthesis and integration review. Source files and findings that do not name a program are listed and do not take a model call. Rework and reads cost more.'}


def evidence(unit, project, assessment_dir):
    """Load the sealed lines for one unit."""
    result = []
    for part in unit['segments']:
        root = Path(project['source']['root']) if part['area'] == 'source' else assessment_dir / 'assessment'
        path = tracked_file(root, part['path']) if part['area'] == 'source' else root / part['path']
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'Evidence path escapes scope')
        require(sha(path) == part['sha256'], 'Checkpoint evidence changed')
        row = dict(part)
        if not part.get('opaque') and not part.get('empty'):
            lines, format_name = evidence_lines(path.read_bytes(), part['area'])
            require(format_name == part.get('format', 'original-lines'), 'Evidence projection format changed')
            row['text'] = '\n'.join(f'{n}: {lines[n-1]}' for n in range(part['start'], part['end']+1))
        result.append(row)
    return result


def validate_checkpoint(reply, unit, supplied):
    """Reject a checkpoint that does not cover the evidence it was given."""
    require(reply.get('kind') == 'checkpoint' and isinstance(reply.get('summary'), str)
            and reply['summary'].strip(), 'Return checkpoint with a nonempty summary')
    measured = dict(reply)
    dispositions = measured.pop('dispositions', None)
    require(len(json.dumps(measured).encode()) <= CHECKPOINT_BYTES, 'Checkpoint exceeds 14000 bytes; return concise cited findings')
    if dispositions is not None:
        require(len(json.dumps(dispositions).encode()) <= 4000, 'Dispositions exceed 4000 bytes')
    coverage = reply.get('coverage')
    keys = {(p['area'], p['path'], p['start'], p['end']) for p in supplied}
    require(isinstance(coverage, list) and all(isinstance(c, dict) and isinstance(c.get('area'), str) and isinstance(c.get('path'), str)
            and type(c.get('start')) is int and type(c.get('end')) is int for c in coverage), 'Coverage list required')
    require(len(coverage) == len(keys) and {(c.get('area'), c.get('path'), c.get('start'), c.get('end')) for c in coverage} == keys,
            'Coverage must account for every supplied segment exactly once')
    require(all(c.get('status') in {'REVIEWED', 'NEEDS_SME'} and isinstance(c.get('note'), str) and c['note'].strip() for c in coverage), 'Coverage status and explanation required')
    for part in supplied:
        if part.get('opaque'):
            match = next(c for c in coverage if (c['area'], c['path'], c['start'], c['end']) == (part['area'], part['path'], part['start'], part['end']))
            require(match['status'] == 'NEEDS_SME', 'Opaque evidence cannot be marked reviewed')
    findings, questions = reply.get('findings'), reply.get('questions')
    require(isinstance(findings, list) and isinstance(questions, list), 'Findings and questions arrays required')
    ids = set()
    for finding in findings:
        require(isinstance(finding, dict), 'Finding must be an object')
        ident = finding.get('id', '')
        require(isinstance(ident, str) and ident.startswith(unit['id'] + '-') and ident not in ids, 'Finding IDs must be unique and prefixed by unit ID')
        ids.add(ident)
        require(finding.get('kind') in {'business_rule', 'observation', 'dependency', 'assessment_claim'} and isinstance(finding.get('statement'), str) and finding['statement'].strip(), 'Finding kind and statement required')
        require(finding.get('status') in {'SOURCE_VERIFIED', 'INFERRED', 'UNKNOWN'}, 'Invalid finding evidence status')
        citations = finding.get('citations')
        require(isinstance(citations, list) and citations, 'Each finding needs exact quoted citations')
        for citation in citations:
            require(isinstance(citation, dict) and type(citation.get('start')) is int and type(citation.get('end')) is int,
                    'Citation needs integer start and end lines')
            match = next((p for p in supplied if p['area'] == citation.get('area') and p['path'] == citation.get('path')
                          and not p.get('opaque') and not p.get('empty') and p['start'] <= citation['start'] <= citation['end'] <= p['end']), None)
            require(match is not None, 'Citation must refer to a supplied evidence range')
            numbered = match['text'].splitlines()
            quoted = '\n'.join(line.split(': ', 1)[1] for line in numbered[citation['start']-match['start']:citation['end']-match['start']+1])
            excerpt = citation.get('quote')
            require(isinstance(excerpt, str) and excerpt.strip() and excerpt in quoted, 'Citation quote does not match evidence')
        require(finding['status'] != 'SOURCE_VERIFIED' or any(c['area'] == 'source' for c in citations), 'AWS claims alone are not source verified')
    qids = set()
    for question in questions:
        require(isinstance(question, dict) and all(isinstance(question.get(k), str) and question[k].strip() for k in ('id','question','impact','owner','blocks')), 'Question requires owner, impact and blocked work')
        require(question['id'].startswith(unit['id'] + '-') and question['id'] not in qids, 'Question IDs must be unique within unit')
        qids.add(question['id'])
    require(not any(c['status'] == 'NEEDS_SME' for c in coverage) or questions, 'Unresolved coverage requires an SME question')
    require(not any(f['status'] != 'SOURCE_VERIFIED' for f in findings) or questions, 'Unverified findings require SME questions')


def synthesis_view(capsule):
    """The findings and questions synthesis needs, without coverage notes or quotation text."""
    checkpoint = capsule['checkpoint']
    findings = []
    for finding in checkpoint.get('findings', []):
        findings.append({
            'id': finding.get('id'), 'kind': finding.get('kind'), 'statement': finding.get('statement'),
            'status': finding.get('status'),
        })
    return {'unit': capsule['unit'], 'review_sha256': capsule.get('review_sha256'),
            'checkpoint': {'summary': checkpoint.get('summary', ''), 'findings': findings,
                           'questions': checkpoint.get('questions', [])}}


_STATUSES = {'SOURCE_VERIFIED', 'INFERRED', 'UNKNOWN'}


def _allowed_sources(plan):
    """Source files each unit actually reviewed. An opaque segment is not a usable path."""
    allowed = {}
    for unit in plan.get('units') or []:
        allowed[unit.get('id')] = [part['path'] for part in unit.get('segments') or []
                                   if part.get('area') == 'source' and not part.get('opaque')
                                   and isinstance(part.get('path'), str)]
    return allowed


def _citation(finding, area, allowed=None):
    """The first citation in one area. A source citation must name a file in that unit."""
    for item in finding.get('citations') or []:
        if not isinstance(item, dict) or item.get('area') != area:
            continue
        path = item.get('path')
        if not isinstance(path, str) or not path.strip():
            continue
        if allowed is not None and path not in allowed:
            continue
        return item
    return None


def _anchor(citation):
    """Line anchor copied from a citation. A missing range stays empty."""
    if not citation:
        return ''
    start, end = citation.get('start'), citation.get('end')
    if isinstance(start, int) and isinstance(end, int) and start > 0 and end >= start:
        return f'lines {start}-{end}'
    return ''


def _status(finding, source_backed):
    """Keep a reviewed status. Assessment text alone is not source-verified."""
    status = finding.get('status')
    if status not in _STATUSES:
        status = 'UNKNOWN'
    if status == 'SOURCE_VERIFIED' and not source_backed:
        return 'INFERRED'
    return status


def reviewed_provenance(capsules, plan):
    """Split reviewed findings into source-backed rules and assessment evidence.

    A path is copied only from a citation. No source file is invented for an
    assessment-only finding, and an assessment claim is not stored as a source rule.
    """
    allowed = _allowed_sources(plan)
    rules, evidence = [], []
    for capsule in capsules:
        sources = allowed.get(capsule.get('unit'), [])
        for finding in (capsule.get('checkpoint') or {}).get('findings') or []:
            if not isinstance(finding, dict):
                continue
            kind = finding.get('kind')
            if kind not in {'business_rule', 'assessment_claim'} or not finding.get('id'):
                continue
            source = _citation(finding, 'source', sources)
            assessment = _citation(finding, 'assessment')
            statement = finding.get('statement') if isinstance(finding.get('statement'), str) else ''
            record = {'id': finding['id'], 'statement': statement,
                      'evidence_status': _status(finding, bool(source))}
            if source:
                record['source_path'] = source['path']
                record['source_anchor'] = _anchor(source)
            if assessment:
                record['assessment_path'] = assessment['path']
                record['assessment_anchor'] = _anchor(assessment)
            if kind == 'business_rule' and source:
                rules.append(record)
            else:
                evidence.append({**record, 'kind': kind, 'unit': capsule.get('unit')})
    return rules, evidence


def provenance_gaps(records):
    """IDs whose assembled provenance is missing a required field."""
    gaps = []
    for record in records:
        if not isinstance(record, dict):
            gaps.append({'id': '', 'missing': ['id']})
            continue
        missing = []
        if not isinstance(record.get('id'), str) or not record['id'].strip():
            missing.append('id')
        if not isinstance(record.get('statement'), str) or not record['statement'].strip():
            missing.append('statement')
        if record.get('evidence_status') not in _STATUSES:
            missing.append('evidence_status')
        has_source = isinstance(record.get('source_path'), str) and bool(record['source_path'].strip())
        has_assessment = isinstance(record.get('assessment_path'), str) and bool(record['assessment_path'].strip())
        if not has_source and not has_assessment:
            missing.extend(['source_path', 'assessment_path'])
        if has_source and not (isinstance(record.get('source_anchor'), str) and record['source_anchor'].strip()):
            missing.append('source_anchor')
        if has_assessment and not (isinstance(record.get('assessment_anchor'), str) and record['assessment_anchor'].strip()):
            missing.append('assessment_anchor')
        if record.get('evidence_status') == 'SOURCE_VERIFIED' and not has_source and 'source_path' not in missing:
            missing.append('source_path')
        if missing:
            gaps.append({'id': record.get('id') or '', 'missing': missing})
    return gaps


def assembly_gaps(capsules, plan):
    """Provenance gaps in the controller's own assembly. This does not call a model."""
    rules, evidence = reviewed_provenance(capsules, plan)
    return provenance_gaps(rules + evidence)


def inspected_source_paths(spec):
    """Source files named by real citations on rules or assessment evidence."""
    paths = set()
    for collection in ('rules', 'assessment_evidence'):
        for record in spec.get(collection) or []:
            if isinstance(record, dict) and isinstance(record.get('source_path'), str) and record['source_path'].strip():
                paths.add(record['source_path'])
    return paths


SYNTHESIS_CORRECTION = (
    'Return a complete corrected candidate. rejected_specification is the assembled candidate. '
    'review_findings are the latest reviewer findings. Change only what those findings require. '
    'Section and work-item mappings exist only in rule_ids arrays. '
    'Do not infer a mapping from prose, titles, or similar rule names. '
    'A rule omitted from rule_ids stays an honest gap. '
    'Do not invent a source path for assessment evidence. '
    'Preserve every open question exactly. '
    'When cited evidence disagrees, keep the conflict as an open question and do not choose an authoritative layout.'
)


def explicit_rule_ids(value, removed):
    """Keep string ids. Drop ids that belong to assessment evidence. Do not parse prose."""
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item not in removed]


def preserve_explicit_mappings(spec, removed):
    """Retain structured section and work-item mappings. Missing ids stay gaps."""
    for section in (spec.get('sections') or {}).values():
        if isinstance(section, dict) and 'rule_ids' in section:
            kept = explicit_rule_ids(section.get('rule_ids'), removed)
            if kept is not None:
                section['rule_ids'] = kept
    for item in spec.get('work_items') or []:
        if isinstance(item, dict) and 'rule_ids' in item:
            kept = explicit_rule_ids(item.get('rule_ids'), removed)
            if kept is not None:
                item['rule_ids'] = kept


def synthesis_pause_reason(reply):
    """The latest synthesis review, including its finding ids and report."""
    findings = reply.get('findings') if isinstance(reply, dict) else []
    ids = []
    if isinstance(findings, list):
        for index, finding in enumerate(findings, 1):
            if isinstance(finding, dict):
                ident = finding.get('id') or finding.get('finding_id')
                if isinstance(ident, str) and ident.strip():
                    ids.append(ident.strip())
            elif isinstance(finding, str) and finding.strip():
                ids.append('R' + str(index))
    reason = 'Synthesis review returned REWORK.'
    if ids:
        reason += ' Findings: ' + ', '.join(ids) + '.'
    report = reply.get('report') if isinstance(reply, dict) and isinstance(reply.get('report'), str) else ''
    report = ' '.join(report.split())
    if report:
        reason += ' ' + report
    return reason


def assembly_gap_reason(gaps):
    """One reason naming every incomplete assembled record and the fields it lacks."""
    parts = [str(gap.get('id') or '(missing id)') + ' missing ' + ', '.join(gap.get('missing') or []) for gap in gaps]
    return 'Incomplete assembled evidence: ' + '; '.join(parts)


def complete_synthesis(spec, capsules, plan):
    """Keep reviewed source rules, assessment evidence, and the exact SME questions."""
    produced, evidence = reviewed_provenance(capsules, plan)
    evidence_ids = {record['id'] for record in evidence}
    rules = [rule for rule in spec.setdefault('rules', [])
             if not isinstance(rule, dict) or rule.get('id') not in evidence_ids]
    present = {rule.get('id'): index for index, rule in enumerate(rules) if isinstance(rule, dict)}
    for record in produced:
        if record['id'] in present:
            current = rules[present[record['id']]]
            current['source_path'] = record['source_path']
            current['source_anchor'] = record['source_anchor']
            current['evidence_status'] = record['evidence_status']
            if not (isinstance(current.get('statement'), str) and current['statement'].strip()):
                current['statement'] = record['statement']
            for field in ('assessment_path', 'assessment_anchor'):
                if field in record:
                    current[field] = record[field]
            continue
        rules.append({field: record[field] for field in
                      ('id', 'statement', 'source_path', 'source_anchor', 'evidence_status',
                       'assessment_path', 'assessment_anchor') if field in record})
    spec['rules'] = rules
    spec['assessment_evidence'] = evidence
    layout = spec.get('monorepo_layout')
    if isinstance(layout, list):
        spec['monorepo_layout'] = [item if isinstance(item, str) else json.dumps(item, sort_keys=True)
                                   for item in layout if item not in (None, '')]
    supplied = [item for item in spec.get('questions') or [] if isinstance(item, dict)]
    questions, seen = [], set()
    for capsule in capsules:
        for question in capsule['checkpoint'].get('questions') or []:
            if not isinstance(question, dict) or question.get('id') in seen:
                continue
            questions.append(question)
            seen.add(question['id'])
    for item in supplied:
        if item.get('id') in seen:
            continue
        if not all(isinstance(item.get(field), str) and item[field].strip()
                   for field in ('id', 'question', 'impact', 'owner', 'blocks')):
            continue
        questions.append(item)
        seen.add(item['id'])
    spec['questions'] = questions
    preserve_explicit_mappings(spec, evidence_ids)
    spec.setdefault('tests', [])
    covered = {case.get('rule_id') for case in spec['tests'] if isinstance(case, dict)}
    for rule in rules:
        if rule.get('id') in covered:
            continue
        covered.add(rule['id'])
        spec['tests'].append({
            'id': rule['id'] + '-accept', 'rule_id': rule['id'], 'category': 'boundary', 'status': 'NOT RUN',
            'given': 'The reviewed condition for ' + rule['id'],
            'when': 'That condition is evaluated',
            'then': rule.get('statement') or rule['id'],
            'oracle': 'Compare with ' + rule.get('source_path', '') + ' ' + rule.get('source_anchor', ''),
        })
    spec['coverage'] = coverage_dispositions(spec)
    return spec


def coverage_dispositions(spec):
    """Record how each retained rule is traced. Assessment claims stay evidence, not new work."""
    section_refs, test_refs, work_refs = {}, {}, {}
    for name, section in (spec.get('sections') or {}).items():
        if not isinstance(section, dict):
            continue
        for ident in section.get('rule_ids') or []:
            section_refs.setdefault(ident, []).append(name)
    for case in spec.get('tests') or []:
        if isinstance(case, dict) and isinstance(case.get('rule_id'), str):
            test_refs.setdefault(case['rule_id'], []).append(case.get('id'))
    for item in spec.get('work_items') or []:
        if isinstance(item, dict):
            for ident in item.get('rule_ids') or []:
                work_refs.setdefault(ident, []).append(item.get('id'))
    rows = []
    for rule in spec.get('rules') or []:
        if not isinstance(rule, dict) or not rule.get('id'):
            continue
        ident = rule['id']
        gaps = [name for name, refs in (('section', section_refs), ('test', test_refs), ('work_item', work_refs))
                if ident not in refs]
        rows.append({'id': ident, 'kind': 'source_rule', 'disposition': 'gap' if gaps else 'covered', 'gaps': gaps,
                     'sections': section_refs.get(ident, []), 'tests': test_refs.get(ident, []),
                     'work_items': work_refs.get(ident, [])})
    for record in spec.get('assessment_evidence') or []:
        if not isinstance(record, dict) or not record.get('id'):
            continue
        rows.append({'id': record['id'], 'kind': record.get('kind') or 'assessment_evidence',
                     'disposition': 'evidence_only', 'gaps': [], 'sections': [], 'tests': [], 'work_items': [],
                     'evidence_status': record.get('evidence_status') or '',
                     'source_path': record.get('source_path') or '',
                     'assessment_path': record.get('assessment_path') or ''})
    return rows


def validate_synthesis(spec, capsules, plan):
    """Reject a specification that drops a rule, assessment finding, or question."""
    source_rules, evidence = reviewed_provenance(capsules, plan)
    saved_rules = {rule['id']: rule for rule in spec.get('rules') or [] if isinstance(rule, dict) and rule.get('id')}
    require({rule['id'] for rule in source_rules} <= set(saved_rules), 'Synthesis omitted checkpoint business rules')
    for rule in source_rules:
        saved = saved_rules[rule['id']]
        require(saved.get('source_path') == rule['source_path'] and saved.get('evidence_status') == rule['evidence_status'],
                'Synthesis changed source provenance for ' + rule['id'])
    saved_evidence = {record['id']: record for record in spec.get('assessment_evidence') or []
                      if isinstance(record, dict) and record.get('id')}
    require({record['id'] for record in evidence} <= set(saved_evidence), 'Synthesis omitted assessment evidence')
    require(set(saved_evidence).isdisjoint(saved_rules), 'Assessment evidence was copied into source rules')
    for record in evidence:
        saved = saved_evidence[record['id']]
        require(saved.get('evidence_status') == record['evidence_status']
                and saved.get('source_path') == record.get('source_path')
                and saved.get('assessment_path') == record.get('assessment_path'),
                'Synthesis changed assessment provenance for ' + record['id'])
    rows = {row['id']: row for row in spec.get('coverage') or [] if isinstance(row, dict) and row.get('id')}
    for rule in source_rules:
        row = rows.get(rule['id'])
        require(row and row.get('kind') == 'source_rule' and row.get('disposition') in {'covered', 'gap'},
                'Synthesis has no coverage disposition for ' + rule['id'])
        require(bool(row.get('gaps')) == (row.get('disposition') == 'gap'),
                'Coverage disposition does not match the gaps for ' + rule['id'])
    require(spec.get('coverage') == coverage_dispositions(spec),
            'Coverage does not match the final candidate')
    evidence_ids = {record['id'] for record in evidence}
    linked = set()
    for section in (spec.get('sections') or {}).values():
        if isinstance(section, dict):
            linked.update(section.get('rule_ids') or [])
    for item in spec.get('work_items') or []:
        if isinstance(item, dict):
            linked.update(item.get('rule_ids') or [])
    for case in spec.get('tests') or []:
        if isinstance(case, dict) and case.get('rule_id'):
            linked.add(case['rule_id'])
    for record in evidence:
        row = rows.get(record['id'])
        require(row and row.get('disposition') == 'evidence_only',
                'Assessment evidence needs an evidence-only disposition for ' + record['id'])
        require(row.get('source_path', '') == (record.get('source_path') or '')
                and row.get('assessment_path', '') == (record.get('assessment_path') or '')
                and row.get('evidence_status') == record.get('evidence_status'),
                'Coverage changed the citation for ' + record['id'])
        require(record['id'] not in linked, 'Assessment evidence was turned into an implementation requirement: ' + record['id'])
    required_questions = {q['id'] for c in capsules for q in c['checkpoint']['questions']}
    require(required_questions <= {q['id'] for q in spec['questions']}, 'Synthesis omitted checkpoint SME questions')
    preserved = {q['id']: q for q in spec['questions']}
    require(all(preserved[q['id']] == q for c in capsules for q in c['checkpoint']['questions']),
            'Synthesis changed an unresolved checkpoint question; preserve it for the human decision gate')


def verified_capsules(work, project, app):
    """Load checkpoints whose hashes still match the evidence."""
    state = project['checkpoints']
    plan_path = work / state['plan_path']
    require(sha(plan_path) == state['plan_sha256'], 'Preparation plan changed')
    plan = app.read(plan_path)
    require(plan['source_commit'] == project['source']['commit'] and plan['scope'] == project['scope']
            and plan['assessment'] == project['assessments'][project['selected']]['sha256'], 'Preparation plan belongs to a different baseline')
    capsules = []
    for unit in plan['units']:
        record = state['units'].get(unit['id'], {})
        if record.get('status') != 'PASS':
            continue
        for kind in ('candidate', 'review'):
            require(sha(work / record[kind + '_path']) == record[kind + '_sha256'], 'Checkpoint handoff changed')
        candidate = app.read(work / record['candidate_path'])
        review = app.read(work / record['review_path'])
        require(review.get('decision') == 'PASS' and review.get('findings') == [], 'Checkpoint lacks independent PASS')
        capsules.append({'unit': unit['id'], 'checkpoint': candidate, 'review_sha256': record['review_sha256']})
    return plan, capsules


_LOCATION = re.compile(
    r'^candidate\.(findings|questions)\[([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\]\.(statement|impact|question)$')


def review_defects(review):
    """Actionable reviewer findings, with a stable id when the model omitted one."""
    findings = review.get('findings') if isinstance(review, dict) else None
    if not isinstance(findings, list):
        return []
    defects = []
    for index, finding in enumerate(findings, 1):
        if isinstance(finding, str) and finding.strip():
            defects.append({'id': 'R' + str(index), 'target': '', 'problem': finding.strip(),
                            'required_action': '', 'location': '', 'required_change': '', 'severity': ''})
            continue
        if not isinstance(finding, dict):
            continue
        ident = finding.get('id') or finding.get('finding_id') or ('R' + str(index))
        problem = finding.get('problem') or finding.get('issue') or finding.get('statement') or ''
        required_change = finding.get('required_change') if isinstance(finding.get('required_change'), str) else ''
        required_action = finding.get('required_action') if isinstance(finding.get('required_action'), str) else ''
        defects.append({
            'id': str(ident),
            'target': finding.get('target') if isinstance(finding.get('target'), str) else '',
            'problem': problem if isinstance(problem, str) else '',
            'required_action': required_action or required_change,
            'location': finding.get('location') if isinstance(finding.get('location'), str) else '',
            'required_change': required_change,
            'severity': finding.get('severity') if isinstance(finding.get('severity'), str) else '',
        })
    return defects


def correction_location(location):
    """Parse one allowlisted candidate path. Other text is rejected and never evaluated."""
    if not isinstance(location, str) or not location.strip():
        return None
    match = _LOCATION.fullmatch(location.strip())
    require(match, 'Unsupported correction location')
    collection, ident, prop = match.group(1), match.group(2), match.group(3)
    allowed = {'findings': {'statement'}, 'questions': {'impact', 'question'}}
    require(prop in allowed[collection], 'Unsupported correction location')
    return collection, ident, prop


def record_by_id(candidate, collection, ident):
    """The finding or question named by an allowlisted location."""
    if not isinstance(candidate, dict) or not isinstance(candidate.get(collection), list):
        return None
    for row in candidate[collection]:
        if isinstance(row, dict) and row.get('id') == ident:
            return row
    return None


def claim_text(candidate, target):
    """The statement a reviewer targeted, when that finding is present."""
    if not target or not isinstance(candidate, dict):
        return None
    for finding in candidate.get('findings') or []:
        if isinstance(finding, dict) and finding.get('id') == target:
            statement = finding.get('statement')
            return statement if isinstance(statement, str) else ''
    return None


def normalized_claim(text):
    """Words only, so spacing and punctuation cannot impersonate a correction."""
    return ' '.join(re.findall(r'[a-z0-9]+', (text or '').lower()))


def evidence_supports(evidence, supplied):
    """True when disputed evidence quotes a line the unit actually supplied."""
    bodies = []
    for part in supplied:
        raw = part.get('text') or ''
        bodies.append('\n'.join(line.split(': ', 1)[-1] for line in raw.splitlines()))
    blob = '\n'.join(bodies)
    text = evidence.strip()
    if text and text in blob:
        return True
    return any(quote in blob for quote in re.findall(r'"([^"]{8,})"', text))


def correction_allowance(record):
    """One automatic correction, plus one for each recorded authorization."""
    return 1 + int(record.get('correction_authorizations') or 0)


def corrections_remaining(record):
    """Targeted corrections still allowed. Passed units have none."""
    if record.get('status') == 'PASS':
        return 0
    return max(0, correction_allowance(record) - int(record.get('corrections_used') or 0))


def remember_correction(record, state, outcome, finding_ids, candidate_sha=None):
    """Append one correction outcome without clearing earlier attempts or call counts."""
    record.setdefault('correction_history', []).append({
        'outcome': outcome,
        'finding_ids': list(finding_ids),
        'calls': state.get('calls'),
        'attempt': record.get('attempt'),
        'candidate_sha256': candidate_sha,
    })


def validate_dispositions(reply, defects, previous_candidate, supplied):
    """Require a disposition per defect and reject an unchanged corrected claim."""
    rows = reply.get('dispositions')
    require(isinstance(rows, list), 'Each reviewer defect needs a disposition')
    found = {}
    expected = {defect['id'] for defect in defects}
    for row in rows:
        require(isinstance(row, dict), 'Disposition must be an object')
        ident = row.get('finding_id')
        disposition = row.get('disposition')
        evidence = row.get('evidence')
        require(isinstance(ident, str) and ident in expected, 'Disposition must name a reviewer finding id')
        require(ident not in found, 'Duplicate disposition')
        require(disposition in {'corrected', 'disputed', 'needs_human'},
                'Disposition must be corrected, disputed, or needs_human')
        require(isinstance(evidence, str) and evidence.strip(), 'Disposition needs supporting evidence')
        if disposition == 'disputed':
            require(evidence_supports(evidence, supplied), 'A disputed defect needs a quote from the supplied evidence')
        found[ident] = row
    require(set(found) == expected, 'Every reviewer defect needs one disposition')
    unchanged = []
    for defect in defects:
        row = found[defect['id']]
        if row['disposition'] != 'corrected':
            continue
        if defect.get('target'):
            before = claim_text(previous_candidate, defect['target'])
            after = claim_text(reply, defect['target'])
            if before is not None and after is not None and normalized_claim(before) == normalized_claim(after):
                unchanged.append(defect['id'])
        located = correction_location(defect.get('location'))
        if not located:
            continue
        collection, ident, prop = located
        before_row = record_by_id(previous_candidate, collection, ident)
        after_row = record_by_id(reply, collection, ident)
        require(before_row is not None, 'Correction location is not in the saved candidate')
        require(after_row is not None, 'Corrected checkpoint dropped ' + ident)
        if collection == 'questions':
            require(after_row.get('owner') == before_row.get('owner') and after_row.get('blocks') == before_row.get('blocks'),
                    'Question owner and blocked work must stay unchanged')
        before_text = before_row.get(prop) if isinstance(before_row.get(prop), str) else ''
        after_text = after_row.get(prop) if isinstance(after_row.get(prop), str) else ''
        if normalized_claim(before_text) == normalized_claim(after_text) and defect['id'] not in unchanged:
            unchanged.append(defect['id'])
    require(not unchanged, 'Unchanged defective content: ' + ', '.join(unchanged))
    return {'needs_human': [defect['id'] for defect in defects if found[defect['id']]['disposition'] == 'needs_human']}


def recovery_instruction(rows):
    """Tell a person how to authorize one more correction. No assessment is involved."""
    units = ', '.join(row['unit'] for row in rows) or 'the paused unit'
    return ('Authorize one targeted correction with reframe feedback --unit ' + units.split(',')[0].strip()
            + ' --authorize-correction --by NAME --note REASON. '
            + 'Paused units: ' + units + '. Passed units stay sealed. No assessment job is created.')


def unresolved_rows(project, plan):
    """One record per unfinished unit, using that unit's own review."""
    rows = []
    for unit in plan['units']:
        record = project['checkpoints']['units'].get(unit['id'], {})
        if record.get('status') == 'PASS':
            continue
        defects = review_defects(record.get('previous_review'))
        reason = record.get('reason') or 'Review paused before this unit passed'
        if not str(reason).startswith(unit['id']):
            reason = unit['id'] + ': ' + str(reason)
        rows.append({
            'unit': unit['id'],
            'status': record.get('status'),
            'attempts': record.get('attempt'),
            'corrections_used': int(record.get('corrections_used') or 0),
            'correction_authorizations': int(record.get('correction_authorizations') or 0),
            'defects': [{'id': defect['id'], 'target': defect.get('target') or '',
                         **({'location': defect['location']} if defect.get('location') else {})} for defect in defects],
            'reason': reason,
        })
    return rows


def rework_result(project, plan, state):
    """Stop with every paused unit beside its own defects. Do not borrow another unit's report."""
    rows = unresolved_rows(project, plan)
    reason = ' | '.join(row['reason'] for row in rows) or 'A program reached its review limit'
    state.update(status='REWORK_LIMIT', reason=reason, unresolved_units=[row['unit'] for row in rows])
    project['stage'] = 'REWORK_LIMIT'
    return {'status': 'REWORK_LIMIT',
            'unit': rows[0]['unit'] if len(rows) == 1 else ', '.join(row['unit'] for row in rows),
            'attempts': rows[0]['attempts'] if len(rows) == 1 else [row['attempts'] for row in rows],
            'defects': rows[0]['defects'] if len(rows) == 1 else rows,
            'unresolved': rows,
            'reason': reason,
            'calls': state.get('calls'),
            'recovery': recovery_instruction(rows),
            'next_step': recovery_instruction(rows)}


def run(work, project, args, runtime, app):
    """Review each evidence unit until they are accepted or the call budget stops."""
    app.verify_source(project)
    app.validate_model_pair(project['providers']['executor'], project['settings'])
    assessment_dir, assessment_run = app.verified_assessment(project, project['selected'])
    app.approved(project, 'assessment', project['assessments'][project['selected']]['sha256'])
    state = project['pipeline']
    area = work / 'preparation'
    app.check_provider_refusals(area)
    plan, capsules = verified_capsules(work, project, app)
    saved_units = project['checkpoints']['units']
    if plan['units'] and all(unit['id'] in saved_units for unit in plan['units']):
        pending = [saved_units[unit['id']] for unit in plan['units'] if saved_units[unit['id']].get('status') != 'PASS']
        if pending and all(record.get('status') == 'REWORK_LIMIT' and not corrections_remaining(record) for record in pending):
            result = rework_result(project, plan, state)
            app.persist(work, project)
            return result
    def remaining_calls_for(record):
        """Calls still required for one program. An exhausted unit does not reserve another review."""
        if record.get('status') == 'PASS':
            return 0
        if record.get('status') == 'REWORK_LIMIT':
            return 2 if corrections_remaining(record) and record.get('candidate_path') else 0
        return 1 if record.get('role') == 'reviewer' else 2

    minimum = 2 + sum(remaining_calls_for(project['checkpoints']['units'].get(u['id'], {})) for u in plan['units'])
    limit = app.kw.call_limit(project['settings'])
    remaining = None if limit is None else limit - state['calls']
    state['minimum_additional_calls'] = minimum
    state['remaining_calls'] = remaining
    if limit is not None and remaining < minimum:
        state.update(status='BLOCKED', reason='Insufficient call budget for remaining checkpoint plan')
        project['stage'] = 'PREPARATION_PLANNED'; app.persist(work, project)
        return {'status':'BLOCKED', 'reason':state['reason'], 'calls':state['calls'],
                'remaining_calls':remaining, 'minimum_additional_calls':minimum}
    if state.get('reason') == 'Insufficient call budget for remaining checkpoint plan':
        state.pop('reason', None)
    for unit in plan['units']:
        record = project['checkpoints']['units'].setdefault(unit['id'], {'status':'PENDING', 'attempt':1, 'role':'executor'})
        if record['status'] == 'PASS':
            continue
        if record['status'] == 'BLOCKED':
            reason = unit['id'] + ': ' + str(record.get('reason') or 'Blocked')
            state.update(status='BLOCKED', reason=reason)
            project['stage'] = 'BLOCKED'
            app.persist(work, project)
            return {'status': 'BLOCKED', 'unit': unit['id'], 'reason': reason, 'calls': state['calls']}
        # A bounded correction goes to the executor with the latest candidate. It does not
        # review that same file again, and it does not reset attempts or call counts.
        if record['status'] == 'REWORK_LIMIT':
            if corrections_remaining(record) and record.get('candidate_path') and review_defects(record.get('previous_review')):
                if record.get('review_path'):
                    record['previous_review'] = app.read(work / record['review_path'])
                record.update(status='PENDING', role='executor', correction_active=True)
            else:
                continue
        while record['status'] != 'PASS':
            if not app.kw.within_call_limit(project['settings'], state['calls']):
                state.update(status='BLOCKED', reason='Total model call budget exhausted')
                project['stage'] = 'BLOCKED'; app.persist(work, project)
                return {'status':'BLOCKED', 'reason':state['reason'], 'calls':state['calls']}
            role = record['role']
            supplied = evidence(unit, project, assessment_dir)
            seen_ranges = {(p['area'],p['path'],p['start'],p['end']) for p in supplied}
            for part in evidence({'segments':record.get('source_reads', [])}, project, assessment_dir):
                identity = (part['area'],part['path'],part['start'],part['end'])
                if identity not in seen_ranges:
                    supplied.append(part); seen_ranges.add(identity)
            defects = review_defects(record.get('previous_review'))
            previous_candidate = None
            context = {'protocol': (app.FRAMEWORK / 'prompts/checkpoints' / (role + '.md')).read_text(),
                       'unit_id': unit['id'], 'scope': project['scope'], 'evidence': supplied,
                       'source_files': sorted({part['path'] for part in unit['segments'] if part['area'] == 'source'}),
                       'assessment_files': sorted({part['path'] for part in unit['segments'] if part['area'] == 'assessment'}),
                       'previous_review': record.get('previous_review'), 'validation_error':record.get('validation_error'),
                       'human_feedback':record.get('human_feedback', [])}
            if role == 'executor' and defects and record.get('candidate_path'):
                require(sha(work / record['candidate_path']) == record['candidate_sha256'], 'Unit candidate changed before correction')
                previous_candidate = app.read(work / record['candidate_path'])
                context['candidate'] = previous_candidate
                for defect in defects:
                    correction_location(defect.get('location'))
                context['correction_request'] = {
                    'instruction': 'Return the full checkpoint and one disposition for every finding id. '
                                   'corrected must change the words at target or location. '
                                   'Whitespace and punctuation alone do not count. '
                                   'A question location keeps that question id, owner, and blocks. '
                                   'disputed must quote supplied evidence. needs_human leaves the defect for a person.',
                    'findings': defects,
                }
            if role == 'reviewer':
                require(sha(work / record['candidate_path']) == record['candidate_sha256'], 'Unit candidate changed after handoff')
                context['candidate'] = app.read(work / record['candidate_path'])
            message = json.dumps(context)
            require(len(message.encode()) <= project['settings']['max_context_bytes'], 'Evidence unit exceeds context limit; replan before spending calls')
            app.require_stage_contract(work, 'task')
            if role == 'reviewer':
                app.require_stage_contract(work, 'review')
            app.require_dollar_cap(project, role)
            state['calls'] += 1; state.update(role=role, status='RUNNING')
            project['stage'] = 'RUNNING_' + role.upper()
            app.persist(work, project)
            call_run = dict(assessment_run, settings=project['settings'], model_calls=state['calls'], round=record['attempt'])
            reply = runtime(project['providers'][role], call_run, area, role, message)
            if role == 'reviewer':
                require(sha(work / record['candidate_path']) == record['candidate_sha256'], 'Reviewer cannot write the candidate')
            app.verify_source(project)
            evidence(unit, project, assessment_dir)
            receipt = area / f'reply-{state["calls"]}.json'
            app.write(receipt, reply)
            if reply.get('kind') == 'read':
                require(reply.get('area', 'source') == 'source', 'Checkpoint supplemental reads must request primary source')
                read_run = dict(assessment_run, settings=project['settings'], round=record['attempt'], role_reads={}, events=[])
                try:
                    chunks = app.kw.source_read(read_run, assessment_dir, role, reply)
                    additions = []
                    for chunk in chunks:
                        lines = chunk['text'].splitlines()
                        require(lines, 'Source range is empty; request an existing range')
                        start = int(lines[0].split(':',1)[0]); end = int(lines[-1].split(':',1)[0])
                        additions.append({'area':'source', 'path':chunk['path'], 'sha256':chunk['sha256'],
                                          'start':start, 'end':end, 'format':'original-lines', 'bytes':len(chunk['text'].encode())})
                    fresh = evidence({'segments':additions}, project, assessment_dir)
                    require(len(json.dumps({**context, 'evidence':supplied+fresh}).encode()) <= project['settings']['max_context_bytes'],
                            'Supplemental evidence exceeds unit context; request a smaller range')
                    saved = record.setdefault('source_reads', [])
                    for part in additions:
                        if part not in saved: saved.append(part)
                    record.pop('validation_error', None)
                except Blocked as error:
                    if str(error) not in {'Read result exceeds byte budget; request fewer lines',
                            'Read ranges require positive start/end and at most 500 lines',
                            'Read request must name one to five paths', 'Source range is empty; request an existing range',
                            'Supplemental evidence exceeds unit context; request a smaller range'}:
                        raise
                    record['validation_error'] = str(error)
                app.persist(work, project)
                continue
            if reply.get('kind') == 'blocked':
                record.update(status='BLOCKED', reason=reply.get('reason', 'Evidence missing'))
                state.update(status='BLOCKED', reason=record['reason']); project['stage']='BLOCKED'
                app.persist(work, project)
                return {'status':'BLOCKED', 'unit':unit['id'], 'reason':record['reason']}
            try:
                if role == 'executor':
                    validate_checkpoint(reply, unit, supplied)
                    if defects and previous_candidate is not None:
                        outcome = validate_dispositions(reply, defects, previous_candidate, supplied)
                        if outcome['needs_human']:
                            record['corrections_used'] = int(record.get('corrections_used') or 0) + 1
                            record['correction_active'] = False
                            remember_correction(record, state, 'needs_human', outcome['needs_human'], record.get('candidate_sha256'))
                            record.update(status='REWORK_LIMIT', role='executor',
                                          reason=unit['id'] + ': ' + ', '.join(outcome['needs_human']) + ' needs a human decision')
                            app.persist(work, project)
                            break
                else:
                    require(reply.get('kind') == 'review' and reply.get('decision') in {'PASS','REWORK','BLOCKED'}
                            and isinstance(reply.get('findings'), list) and isinstance(reply.get('report'), str)
                            and reply['report'].strip(), 'Invalid checkpoint reviewer decision')
                    require(reply['decision'] != 'PASS' or not reply['findings'], 'PASS cannot retain reviewer defects')
                    require(reply['decision'] != 'REWORK' or reply['findings'], 'REWORK needs actionable findings')
            except Blocked as error:
                if str(error).startswith('Unchanged defective content'):
                    record['corrections_used'] = int(record.get('corrections_used') or 0) + 1
                    record['correction_active'] = False
                    ident = [part.strip() for part in str(error).split(':', 1)[-1].split(',') if part.strip()]
                    remember_correction(record, state, 'unchanged', ident, record.get('candidate_sha256'))
                    record.update(status='REWORK_LIMIT', role='executor', reason=str(error))
                    app.persist(work, project)
                    break
                record['validation_error'] = str(error)
                record['invalid_replies'] = record.get('invalid_replies', 0) + 1
                if record['invalid_replies'] >= 3:
                    record.update(status='BLOCKED', reason='Three invalid unit replies; inspect schema feedback')
                    state.update(status='BLOCKED', reason=record['reason']); project['stage']='BLOCKED'
                app.persist(work, project)
                if record['status'] == 'BLOCKED':
                    return {'status':'BLOCKED', 'unit':unit['id'], 'reason':record['reason']}
                continue
            record.pop('validation_error', None)
            if role == 'executor':
                record.update(candidate_path=str(receipt.relative_to(work)), candidate_sha256=sha(receipt), role='reviewer')
                if record.get('correction_active'):
                    record['corrections_used'] = int(record.get('corrections_used') or 0) + 1
                    record['correction_active'] = False
                    remember_correction(record, state, 'submitted', [defect['id'] for defect in defects], record['candidate_sha256'])
            else:
                record.update(review_path=str(receipt.relative_to(work)), review_sha256=sha(receipt))
                if reply['decision'] == 'PASS':
                    record['status'] = 'PASS'
                elif reply['decision'] == 'BLOCKED' or record['attempt'] >= project['settings']['max_rounds']:
                    if reply['decision'] == 'REWORK':
                        record['previous_review'] = reply
                    record.update(status='BLOCKED' if reply['decision'] == 'BLOCKED' else 'REWORK_LIMIT',
                                  reason=reply['report'])
                    if record['status'] == 'BLOCKED':
                        reason = unit['id'] + ': ' + record['reason']
                        state.update(status='BLOCKED', reason=reason)
                        project['stage'] = 'BLOCKED'
                        app.persist(work, project)
                        return {'status': 'BLOCKED', 'unit': unit['id'], 'reason': reason, 'calls': state['calls']}
                    app.persist(work, project)
                    break
                else:
                    record.update(role='executor', attempt=record['attempt']+1, previous_review=reply)
            app.persist(work, project)
    unfinished = [unit['id'] for unit in plan['units']
                  if project['checkpoints']['units'].get(unit['id'], {}).get('status') != 'PASS']
    if unfinished:
        result = rework_result(project, plan, state)
        app.persist(work, project)
        return result
    project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
    state.update(status='RUNNING', role='executor')
    state.pop('reason', None)
    project['stage']='RUNNING_EXECUTOR'
    app.persist(work, project)
    return app.legacy_pipeline(work, project, args, runtime)
