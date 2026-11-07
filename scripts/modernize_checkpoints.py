"""Bounded, independently reviewed evidence units. No cloud or model calls in planning."""
import json
import re
from pathlib import Path

from pilot import Blocked, require, sha, tracked_file, git
from modernize_artifacts import digest

UNIT_BYTES = 60000
CHECKPOINT_BYTES = 14000
MAX_LINES = 250


def json_projection(value):
    """Lossless pointer records; repeated object/array values become explicit references."""
    lines, seen = [], {}
    def visit(item, pointer):
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


def evidence_lines(raw, area):
    text = raw.decode('utf-8')
    if area == 'assessment':
        try:
            value = json.loads(text)
        except ValueError:
            pass
        else:
            if isinstance(value, (dict, list)):
                return json_projection(value), 'json-pointer-records-v1'
    return text.splitlines(), 'original-lines'


def segments(area, name, path, expected):
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


def make_plan(project, assessment_dir, run, catalog):
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
    def focus(record):
        if record['area'] == 'source': return record['path']
        label = re.sub(r'-(cbl|jcl|cpy|bms|sql|proc)\.json$', r'.\1', labels[record['path']], flags=re.IGNORECASE)
        if label in source_names: return label
        matches = [n for n in source_names if Path(n).name.lower() == Path(label).name.lower()]
        return matches[0] if len(matches) == 1 else '@assessment'
    for record in records: record['focus'] = focus(record)
    linked = {r['focus'] for r in records if r['area']=='assessment' and r['focus']!='@assessment'}
    for record in records:
        if record['area']=='source' and record['focus'] not in linked: record['focus']='@source-support'
    records.sort(key=lambda r:(r['focus']=='@assessment', r['focus'], r['area'] != 'source', r['path'], r['start']))
    units, batch, size = [], [], 0
    for record in records:
        weight = min(record['bytes'], UNIT_BYTES // 2) + len(json.dumps(record).encode())
        if batch and (size + weight > unit_limit or batch[0]['focus'] != record['focus']):
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
            'minimum_calls': len(units) * 2 + 2,
            'estimate_note': 'Lower bound: executor and reviewer per unit, plus synthesis and integration review. Rework and reads cost more.'}


def evidence(unit, project, assessment_dir):
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
    require(reply.get('kind') == 'checkpoint' and isinstance(reply.get('summary'), str)
            and reply['summary'].strip(), 'Return checkpoint with a nonempty summary')
    require(len(json.dumps(reply).encode()) <= CHECKPOINT_BYTES, 'Checkpoint exceeds 14000 bytes; return concise cited findings')
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
            require(citation.get('quote') == quoted and quoted.strip(), 'Citation quote does not match evidence')
        require(finding['status'] != 'SOURCE_VERIFIED' or any(c['area'] == 'source' for c in citations), 'AWS claims alone are not source verified')
    qids = set()
    for question in questions:
        require(isinstance(question, dict) and all(isinstance(question.get(k), str) and question[k].strip() for k in ('id','question','impact','owner','blocks')), 'Question requires owner, impact and blocked work')
        require(question['id'].startswith(unit['id'] + '-') and question['id'] not in qids, 'Question IDs must be unique within unit')
        qids.add(question['id'])
    require(not any(c['status'] == 'NEEDS_SME' for c in coverage) or questions, 'Unresolved coverage requires an SME question')
    require(not any(f['status'] != 'SOURCE_VERIFIED' for f in findings) or questions, 'Unverified findings require SME questions')


def validate_synthesis(spec, capsules):
    required_rules = {f['id'] for c in capsules for f in c['checkpoint']['findings'] if f['kind'] == 'business_rule'}
    required_questions = {q['id'] for c in capsules for q in c['checkpoint']['questions']}
    require(required_rules <= {r['id'] for r in spec['rules']}, 'Synthesis omitted checkpoint business rules')
    require(required_questions <= {q['id'] for q in spec['questions']}, 'Synthesis omitted checkpoint SME questions')
    preserved = {q['id']: q for q in spec['questions']}
    require(all(preserved[q['id']] == q for c in capsules for q in c['checkpoint']['questions']),
            'Synthesis changed an unresolved checkpoint question; preserve it for the human decision gate')


def verified_capsules(work, project, app):
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


def run(work, project, args, runtime, app):
    app.verify_source(project)
    app.validate_model_pair(project['providers']['executor'], project['settings'])
    assessment_dir, assessment_run = app.verified_assessment(project, project['selected'])
    app.approved(project, 'assessment', project['assessments'][project['selected']]['sha256'])
    state = project['pipeline']
    area = work / 'preparation'
    app.check_provider_refusals(area)
    plan, capsules = verified_capsules(work, project, app)
    minimum = 2 + sum(0 if project['checkpoints']['units'].get(u['id'], {}).get('status') == 'PASS' else
                      1 if project['checkpoints']['units'].get(u['id'], {}).get('role') == 'reviewer' else 2
                      for u in plan['units'])
    remaining = project['settings']['max_model_calls'] - state['calls']
    if remaining < minimum:
        state.update(status='BLOCKED', reason='Insufficient call budget for remaining checkpoint plan')
        project['stage'] = 'PREPARATION_PLANNED'; app.persist(work, project)
        return {'status':'BLOCKED', 'reason':state['reason'], 'calls':state['calls'],
                'remaining_calls':remaining, 'minimum_additional_calls':minimum}
    for unit in plan['units']:
        record = project['checkpoints']['units'].setdefault(unit['id'], {'status':'PENDING', 'attempt':1, 'role':'executor'})
        if record['status'] == 'PASS':
            continue
        require(record['status'] not in {'BLOCKED', 'REWORK_LIMIT'}, 'Resolve the blocked evidence unit before resuming')
        while record['status'] != 'PASS':
            if state['calls'] >= project['settings']['max_model_calls']:
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
            context = {'protocol': (app.FRAMEWORK / 'prompts/checkpoints' / (role + '.md')).read_text(),
                       'unit_id': unit['id'], 'scope': project['scope'], 'evidence': supplied,
                       'source_files': app.kw.inventory(assessment_run),
                       'previous_review': record.get('previous_review'), 'validation_error':record.get('validation_error'),
                       'human_feedback':record.get('human_feedback', [])}
            if role == 'reviewer':
                require(sha(work / record['candidate_path']) == record['candidate_sha256'], 'Unit candidate changed after handoff')
                context['candidate'] = app.read(work / record['candidate_path'])
            message = json.dumps(context)
            require(len(message.encode()) <= project['settings']['max_context_bytes'], 'Evidence unit exceeds context limit; replan before spending calls')
            app.require_dollar_cap(project, role)
            state['calls'] += 1; state.update(role=role, status='RUNNING')
            project['stage'] = 'RUNNING_' + role.upper()
            app.persist(work, project)
            call_run = dict(assessment_run, settings=project['settings'], model_calls=state['calls'], round=record['attempt'])
            reply = runtime(project['providers'][role], call_run, area, role, message)
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
                else:
                    require(reply.get('kind') == 'review' and reply.get('decision') in {'PASS','REWORK','BLOCKED'}
                            and isinstance(reply.get('findings'), list) and isinstance(reply.get('report'), str)
                            and reply['report'].strip(), 'Invalid checkpoint reviewer decision')
                    require(reply['decision'] != 'PASS' or not reply['findings'], 'PASS cannot retain reviewer defects')
                    require(reply['decision'] != 'REWORK' or reply['findings'], 'REWORK needs actionable findings')
            except Blocked as error:
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
            else:
                record.update(review_path=str(receipt.relative_to(work)), review_sha256=sha(receipt))
                if reply['decision'] == 'PASS':
                    record['status'] = 'PASS'
                elif reply['decision'] == 'BLOCKED' or record['attempt'] >= project['settings']['max_rounds']:
                    record.update(status='BLOCKED' if reply['decision'] == 'BLOCKED' else 'REWORK_LIMIT', reason=reply['report'])
                    state.update(status=record['status'], reason=record['reason']); project['stage']=record['status']
                    app.persist(work, project)
                    return {'status':record['status'], 'unit':unit['id'], 'reason':record['reason']}
                else:
                    record.update(role='executor', attempt=record['attempt']+1, previous_review=reply)
            app.persist(work, project)
    project['checkpoints']['status'] = 'READY_FOR_SYNTHESIS'
    state.update(status='RUNNING', role='executor')
    state.pop('reason', None)
    project['stage']='RUNNING_EXECUTOR'
    app.persist(work, project)
    return app.legacy_pipeline(work, project, args, runtime)
