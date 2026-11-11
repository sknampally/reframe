"""Structured specification validation and inert, portable HTML rendering."""
import hashlib
import html
import json
import re
from pathlib import Path
from pilot import require, tracked_file

SECTIONS = ('architecture', 'ui', 'services', 'data', 'infrastructure', 'security',
            'migration', 'testing', 'operations', 'delivery')


def digest(value):
    """Return a stable SHA-256 of a JSON value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def text(value):
    """True when a value is a non-empty string."""
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    """True when a value is a list of non-empty strings."""
    return isinstance(value, list) and all(text(x) for x in value)


def validate_spec(spec, source):
    """Reject a specification that omits scope, a traced rule, a section, a question, an unrun test, or ordered work."""
    require(isinstance(spec, dict), 'Specification must be an object')
    require(text(spec.get('scope')) and text(spec.get('excluded_scope')), 'Explicit scope and exclusions required')
    rules = spec.get('rules')
    require(isinstance(rules, list) and rules, 'At least one source-traceable rule required')
    ids = set()
    for rule in rules:
        require(isinstance(rule, dict) and all(text(rule.get(k)) for k in
                ('id', 'statement', 'source_path', 'source_anchor', 'evidence_status')), 'Incomplete rule')
        require(rule['id'] not in ids, 'Duplicate rule ID')
        ids.add(rule['id'])
        require(rule['evidence_status'] in {'SOURCE_VERIFIED', 'INFERRED', 'UNKNOWN'}, 'Invalid evidence status')
        tracked_file(source, rule['source_path'])
    require(isinstance(spec.get('sections'), dict) and set(spec['sections']) == set(SECTIONS),
            'All preparation sections required; use explicit not-applicable explanations')
    for section in spec['sections'].values():
        require(isinstance(section, dict) and text(section.get('specification')) and text(section.get('implementation_prompt')),
                'Each section needs a specification and reusable implementation prompt')
        require(strings(section.get('rule_ids')) and set(section['rule_ids']) <= ids, 'Unknown section rule reference')
    require(isinstance(spec.get('questions'), list), 'SME questions list required')
    qids = set()
    for question in spec['questions']:
        require(isinstance(question, dict) and all(text(question.get(k)) for k in
                ('id', 'question', 'impact', 'owner', 'blocks')), 'SME question needs owner, impact and blocked work')
        require(question['id'] not in qids, 'Duplicate question ID')
        qids.add(question['id'])
    require(all(r['evidence_status'] == 'SOURCE_VERIFIED' for r in rules) or spec['questions'],
            'Inferred/unknown rules require explicit SME questions')
    require(isinstance(spec.get('tests'), list) and len(spec['tests']) >= 3, 'Normal, rejection and boundary scenarios required')
    require({x.get('category') for x in spec['tests'] if isinstance(x, dict)} == {'normal', 'rejection', 'boundary'},
            'Tests must cover normal, rejection and boundary categories')
    test_ids = set()
    covered = set()
    for case in spec['tests']:
        require(isinstance(case, dict) and all(text(case.get(k)) for k in ('id', 'rule_id', 'given', 'when', 'then', 'oracle')),
                'Incomplete test specification')
        require(case['rule_id'] in ids and case.get('status') == 'NOT RUN', 'Tests must reference known rules and remain NOT RUN')
        require(case['id'] not in test_ids, 'Duplicate test ID')
        test_ids.add(case['id'])
        covered.add(case['rule_id'])
    require(covered == ids, 'Every rule needs an acceptance scenario')
    require(isinstance(spec.get('work_items'), list) and spec['work_items'], 'Ordered implementation work items required')
    seen = set()
    for item in spec['work_items']:
        require(isinstance(item, dict) and all(text(item.get(k)) for k in ('id', 'title', 'acceptance')),
                'Incomplete work item')
        require(item['id'] not in seen and strings(item.get('depends_on')) and set(item['depends_on']) <= seen,
                'Work items must have unique IDs in dependency order')
        seen.add(item['id'])
    require(strings(spec.get('monorepo_layout')) and spec['monorepo_layout'], 'monorepo_layout must be a nonempty array of strings, e.g. ["apps/ui: proposed interface"], not objects')


def screen_action_valid(action, ids):
    """A legacy action is a label. A structured action also names a screen in this review."""
    if text(action):
        return True
    return (isinstance(action, dict) and text(action.get('label')) and text(action.get('screen_id'))
            and action['screen_id'] in ids)


def validate_screens(value, spec):
    """Reject a screen or API contract that is incomplete or cites an unknown rule."""
    require(isinstance(value, dict) and isinstance(value.get('screens'), list) and value['screens'], 'Screens required')
    rules = {r['id'] for r in spec['rules']}
    require(isinstance(value.get('apis'), list), 'Structured API contracts required; use an empty list only when no APIs are proposed')
    operations = set()
    for api in value['apis']:
        require(isinstance(api, dict) and all(text(api.get(key)) for key in
                ('id','method','path','purpose','authentication','uncertainties')), 'Incomplete API contract')
        require(api['method'] in {'GET','POST','PUT','PATCH','DELETE'} and api['path'].startswith('/')
                and not api['path'].startswith('//'), 'API requires a relative route and supported method')
        key = (api['method'],api['path'])
        require(key not in operations, 'Duplicate API operation')
        operations.add(key)
        require(isinstance(api.get('request'), dict) and isinstance(api.get('responses'), dict)
                and api['responses'], 'API request and response contracts required')
        require(strings(api.get('rule_ids')) and set(api['rule_ids']) <= rules, 'Unknown API rule reference')
    ids = set()
    for screen in value['screens']:
        require(isinstance(screen, dict) and all(text(screen.get(k)) for k in ('id', 'title', 'purpose', 'uncertainties')),
                'Screen requires ID, purpose and explicit uncertainties')
        require(screen['id'] not in ids, 'Duplicate screen ID')
        ids.add(screen['id'])
    for screen in value['screens']:
        require(strings(screen.get('rule_ids')) and set(screen['rule_ids']) <= rules, 'Screen has unknown rule reference')
        require(strings(screen.get('fields')) and strings(screen.get('states')) and screen['states'],
                'Screen fields and states must be text arrays')
        require(isinstance(screen.get('actions'), list) and screen['actions']
                and all(screen_action_valid(action, ids) for action in screen['actions']),
                'Screen actions must be labels, or a label plus a screen in this review')
        require('api_ids' in screen and strings(screen.get('api_ids')) and set(screen['api_ids']) <= {api['id'] for api in value['apis']},
                'Screen services must be known API ids')
    require(isinstance(value.get('journeys'), list) and value['journeys'], 'Business journeys required')
    placed = []
    journey_ids = set()
    for journey in value['journeys']:
        require(isinstance(journey, dict) and all(text(journey.get(key)) for key in ('id', 'name', 'summary')),
                'Journey needs an id, name and business summary')
        require(journey['id'] not in journey_ids, 'Duplicate journey ID')
        journey_ids.add(journey['id'])
        require(strings(journey.get('screen_ids')) and journey['screen_ids'], 'Journey needs the screens in walk order')
        require(all(item in ids for item in journey['screen_ids']), 'Journey names a screen that is not in this review')
        placed.extend(journey['screen_ids'])
    require(len(placed) == len(set(placed)) == len(ids), 'Every screen belongs to one journey')


def plain_title(screen):
    """Screen title without a trailing program name in parentheses."""
    return screen['title'].split('(')[0].strip()


def linked_actions(screens):
    """Use the screen id the model named. A label with no screen id stays on the page."""
    ids = {screen['id'] for screen in screens}
    linked = []
    for screen in screens:
        actions = []
        for action in screen.get('actions') or []:
            if isinstance(action, dict):
                target = action.get('screen_id') if action.get('screen_id') in ids and action.get('screen_id') != screen['id'] else None
                actions.append({'label': action.get('label', ''), 'screen_id': target})
            else:
                actions.append({'label': str(action), 'screen_id': None})
        linked.append(actions)
    return linked


def model_journeys(document, screen_list):
    """The journeys the model named, in the order it gave. Unplaced screens stay visible and unlabeled."""
    by_id = {screen['id']: screen for screen in screen_list}
    parts = []
    placed = set()
    for journey in document.get('journeys') or []:
        if not isinstance(journey, dict):
            continue
        chosen = []
        for item in journey.get('screen_ids') or []:
            if item in by_id and item not in placed:
                chosen.append(by_id[item])
                placed.add(item)
        if chosen:
            parts.append({'name': journey.get('name') or 'Business step', 'summary': journey.get('summary') or '', 'screens': chosen})
    rest = [screen for screen in screen_list if screen['id'] not in placed]
    if rest:
        parts.append({'name': 'Screens not placed in a journey', 'summary': 'The model did not place these screens in a business journey.', 'screens': rest})
    return parts


def _schema(value):
    """Build an OpenAPI schema from a review example. The example stays illustrative."""
    if isinstance(value, dict):
        return {'type': 'object', 'properties': {key: _schema(item) for key, item in value.items()}}
    if isinstance(value, list):
        return {'type': 'array', 'items': _schema(value[0]) if value else {'type': 'string'}}
    if isinstance(value, bool):
        return {'type': 'boolean'}
    if isinstance(value, int) and not isinstance(value, bool):
        return {'type': 'integer'}
    if isinstance(value, float):
        return {'type': 'number'}
    return {'type': 'string', 'description': str(value)}


def openapi_document(apis, title='Approved review APIs'):
    """OpenAPI 3 document for the approved contracts. It describes no running server."""
    paths = {}
    for api in apis:
        method = api['method'].lower()
        request = api.get('request') if isinstance(api.get('request'), dict) else {}
        path_examples = request.get('path_params') if isinstance(request.get('path_params'), dict) else {}
        parameters = []
        for name in re.findall(r'\{([^{}]+)\}', api['path']):
            parameter = {'name': name, 'in': 'path', 'required': True, 'schema': {'type': 'string'}}
            if isinstance(path_examples.get(name), str):
                parameter['description'] = path_examples[name]
            parameters.append(parameter)
        responses = {}
        for status, contract in api['responses'].items():
            response = {'description': contract if isinstance(contract, str) else (api.get('purpose') or str(status))}
            if not isinstance(contract, str):
                response['content'] = {'application/json': {'schema': _schema(contract), 'example': contract}}
            responses[str(status)] = response
        operation = {
            'operationId': api['id'], 'summary': api['purpose'],
            'description': api.get('uncertainties') or 'No remaining uncertainty recorded.',
            'parameters': parameters, 'responses': responses,
            'x-rule-ids': list(api.get('rule_ids') or []),
        }
        if text(api.get('authentication')):
            operation['security'] = [{'reviewAuth': []}]
            operation['description'] += ' Authentication note: ' + api['authentication']
        body = {key: item for key, item in request.items() if key != 'path_params'}
        if method in {'post', 'put', 'patch'} and body:
            operation['requestBody'] = {'required': True, 'content': {
                'application/json': {'schema': _schema(body), 'example': body}}}
        paths.setdefault(api['path'], {})[method] = operation
    return {
        'openapi': '3.0.3',
        'info': {'title': title, 'version': 'review',
                 'description': 'Proposed interfaces from the approved review. This document does not start a server and does not send a request.'},
        'paths': paths,
        'components': {'securitySchemes': {'reviewAuth': {
            'type': 'http', 'scheme': 'bearer',
            'description': 'Illustrative caller note from the review. No endpoint is served.'}}},
    }


def _button_label(label):
    """The short name of an action. The full legacy sentence stays available as its title."""
    head = re.split(r'[:()]', label, maxsplit=1)[0].strip()
    return head or label


def _field_parts(field):
    """A short label and the constraint that followed it."""
    name = field.split('(')[0].strip().rstrip('.')
    hint = field[len(name):].strip().lstrip('.').strip()
    if hint.startswith('(') and hint.endswith(')'):
        hint = hint[1:-1].strip()
    return name, hint


def _spoken(purpose):
    """Separate the proposed sentence from a legacy fact when the contract recorded both."""
    cleaned = re.sub(r'(?i)^proposed\s+', '', purpose or '').strip()
    proposed, marker, legacy = cleaned.partition('Legacy fact:')
    return (proposed.strip(' .') or cleaned), (legacy.strip() if marker else '')


def _page(title, body):
    """One standalone review page. Model text is escaped by the caller. No script and no remote asset."""
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">
<title>''' + title + '''</title><style>
body{margin:0;font:16px/1.5 system-ui;color:#172b46;background:#eef2f7}
aside{position:fixed;top:0;bottom:0;width:270px;overflow:auto;background:#10283d;color:#fff;padding:20px;box-sizing:border-box}
aside a{display:block;color:#dce9f4;text-decoration:none;padding:8px 10px;border-radius:8px;margin:2px 0}
aside a:hover{background:#27465f}
.part-name{display:block;margin:16px 0 4px;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#9fb4c7}
main{margin-left:270px;padding:28px;max-width:880px}
.screen{display:none;background:#fff;border:1px solid #d5dfeb;border-radius:16px;padding:28px}
.screen:target{display:block}
main:not(:has(.screen:target)) .screen:first-of-type{display:block}
h1{margin:0 0 8px;font-size:28px}
.kicker{color:#5c6d82;margin:0 0 8px}
.kicker a{color:#1f5f93}
ol.steps{list-style:none;padding:0;margin:8px 0 18px}
ol.steps a,ol.steps .here{display:block;padding:8px 12px;border-radius:8px;text-decoration:none}
ol.steps a{color:#1f5f93}
ol.steps .here{background:#e7f1fa;font-weight:700;color:#10283d}
label{display:block;font-weight:650;margin:14px 0}
small{display:block;font-weight:450;color:#5c6d82}
input{display:block;box-sizing:border-box;width:100%;max-width:460px;padding:12px;border:1px solid #c5d2e0;border-radius:8px;margin-top:6px}
.action{display:inline-block;margin:8px 10px 0 0;padding:12px 18px;background:#1f5f93;color:#fff;text-decoration:none;border-radius:8px}
.choice{display:block;margin:10px 0;padding:14px 16px;border:1px solid #c5d2e0;border-radius:12px;text-decoration:none;color:#172b46;background:#f7fbff}
.stay{margin-top:12px;color:#5c6d82}
button{margin-top:18px;padding:10px 16px;border:1px solid #8aa0b8;background:#fff;border-radius:8px;cursor:pointer}
.rules{border:1px solid #c5d2e0;border-radius:12px;padding:20px;max-width:760px;width:min(760px,92vw)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #e1e8f0}
.notice{background:#fff4d5;border-left:4px solid #8b6500;padding:12px 16px}
details{margin-top:18px;background:#f8fafc;border:1px solid #d5dfeb;border-radius:12px;padding:16px}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
.method{font-weight:750}
.bar{height:14px;background:#e1e8f0;border-radius:7px;overflow:hidden}
.bar span{display:block;height:100%;background:#1f5f93}
@media(max-width:800px){aside{position:static;width:auto}main{margin:0}}
</style>''' + body + '</html>'


def _rules_panel(screen, rules, esc):
    """The business-rule table for one screen. Other screens' rules are not included."""
    linked = [rules[rule_id] for rule_id in screen.get('rule_ids') or [] if rule_id in rules]
    if not linked:
        return '<p class="stay">No business rule is linked to this screen.</p>'
    table = ''.join('<tr><td>' + esc(rule['id']) + '</td><td>' + esc(rule.get('statement', '')) + '</td><td>'
                    + esc(rule.get('evidence_status', '')) + '</td><td>' + esc((rule.get('source_path') or '')
                    + ' ' + (rule.get('source_anchor') or '')) + '</td></tr>' for rule in linked)
    return ('<button popovertarget="rules-' + esc(screen['id']) + '">Business rules</button>'
            '<div class="rules" id="rules-' + esc(screen['id']) + '" popover="auto"><h2>'
            + esc(plain_title(screen)) + '</h2><table><thead><tr><th>Rule</th><th>Statement</th><th>Status</th><th>Source</th></tr></thead><tbody>'
            + table + '</tbody></table></div>')


def _services(screen, apis):
    """Contracts the model attached to this screen."""
    wanted = set(screen.get('api_ids') or [])
    return [api for api in apis if api.get('id') in wanted]


def render_api_page(apis, title='API reference', screens=None, journeys=None):
    """One contract at a time, under the journey the model attached it to."""
    esc = lambda value: html.escape(str(value), quote=True)
    screen_list = screens or []
    parts = model_journeys({'journeys': journeys or []}, screen_list)
    def part_name(api):
        for part in parts:
            if any(api.get('id') in (screen.get('api_ids') or []) for screen in part['screens']):
                return part['name']
        return 'Other services'
    def used_by(api):
        return [screen for screen in screen_list if api.get('id') in (screen.get('api_ids') or [])]
    navigation = ['<a href="index.html#flow">Application flow</a>', '<a href="usage.html">Token spend</a>']
    articles = []
    grouped = {}
    for api in apis:
        grouped.setdefault(part_name(api), []).append(api)
    order = [part['name'] for part in parts if part['name'] in grouped]
    order += [name for name in grouped if name not in order]
    for name in order:
        navigation.append('<span class="part-name">' + esc(name) + '</span>')
        for api in grouped[name]:
            proposed, legacy = _spoken(api.get('purpose') or '')
            navigation.append('<a href="#op-' + esc(api['id']) + '">' + esc(proposed or api['path']) + '</a>')
            request = api.get('request') if isinstance(api.get('request'), dict) else {}
            request_rows = []
            for key, value in request.items():
                if key == 'path_params' and isinstance(value, dict):
                    for path_name, note in value.items():
                        request_rows.append(('In the path', path_name, note if isinstance(note, str) else json.dumps(note)))
                    continue
                request_rows.append(('In the body', key, value if isinstance(value, str) else json.dumps(value)))
            request_table = ''.join('<tr><td>' + esc(place) + '</td><td>' + esc(field) + '</td><td>' + esc(note) + '</td></tr>'
                                     for place, field, note in request_rows)
            response_rows = []
            for status, contract in (api.get('responses') or {}).items():
                if isinstance(contract, str):
                    meaning = contract
                elif isinstance(contract, dict):
                    meaning = 'Returns ' + ', '.join(contract.keys())
                else:
                    meaning = json.dumps(contract)
                response_rows.append('<tr><td>' + esc(status) + '</td><td>' + esc(meaning) + '</td></tr>')
            where = ''.join('<a href="index.html#screen-' + esc(screen['id']) + '">' + esc(plain_title(screen)) + '</a>'
                            for screen in used_by(api)) or '<p class="stay">No screen uses this service.</p>'
            legacy_line = '<p>' + esc(legacy) + '</p>' if legacy else ''
            articles.append('<article class="screen" id="op-' + esc(api['id']) + '"><p class="kicker">' + esc(name)
                            + '</p><h1>' + esc(proposed or api['path']) + '</h1><p class="method">' + esc(api['method'])
                            + ' ' + esc(api['path']) + '</p>' + legacy_line + '<p>' + esc(api.get('authentication') or '')
                            + '</p><h2>What the caller sends</h2><table><thead><tr><th>Where</th><th>Field</th><th>Meaning</th></tr></thead><tbody>'
                            + (request_table or '<tr><td colspan="3">No request fields recorded.</td></tr>')
                            + '</tbody></table><h2>What can come back</h2><table><thead><tr><th>Status</th><th>Meaning</th></tr></thead><tbody>'
                            + ''.join(response_rows) + '</tbody></table><h2>Used from</h2>' + where
                            + '<p>Rules: ' + esc(', '.join(api.get('rule_ids') or [])) + '</p><p>'
                            + esc(api.get('uncertainties') or '') + '</p><p><a href="openapi.json">openapi.json</a> is the OpenAPI 3 document for every contract on this page.</p></article>')
    body = ('<aside>' + ''.join(navigation) + '</aside><main><p class="notice">Each service is the contract for one step. '
            'This page does not send a request. Engineers use openapi.json.</p>' + ''.join(articles) + '</main>')
    return _page(esc(title), body)


def render_site(spec, screens, decisions, title='Modernization review'):
    """Never execute model-generated HTML, JavaScript, URLs or styles."""
    esc = lambda value: html.escape(str(value), quote=True)
    rules = {rule['id']: rule for rule in spec.get('rules') or [] if isinstance(rule, dict) and rule.get('id')}
    screen_list = screens['screens']
    parts = model_journeys(screens, screen_list)
    part_of = {}
    for part in parts:
        for index, screen in enumerate(part['screens'], start=1):
            part_of[screen['id']] = (part, index)
    navigation = ['<a href="#flow">Application flow</a>', '<a href="api.html">Services</a>', '<a href="usage.html">Token spend</a>']
    flow_blocks = []
    for part in parts:
        navigation.append('<span class="part-name">' + esc(part['name']) + '</span>')
        steps = []
        for index, screen in enumerate(part['screens'], start=1):
            navigation.append('<a href="#screen-' + esc(screen['id']) + '">' + esc(plain_title(screen)) + '</a>')
            steps.append('<li><a href="#screen-' + esc(screen['id']) + '">' + str(index) + '. ' + esc(plain_title(screen)) + '</a></li>')
        flow_blocks.append('<section><h2>' + esc(part['name']) + '</h2><p>' + esc(part['summary']) + '</p><ol class="steps">'
                           + ''.join(steps) + '</ol></section>')
    articles = ['<article class="screen" id="flow"><h1>How this application works</h1><p class="notice">Start with a part of the business. '
                'Each step opens that screen. Fields can be typed. Nothing is submitted, no test has run, and no application is deployed.</p><p>'
                + esc(spec.get('scope', '')) + '</p>' + ''.join(flow_blocks)]
    reference = []
    for name in SECTIONS:
        if name in (spec.get('sections') or {}):
            reference.append('<details><summary>' + esc(name.title()) + '</summary><pre>'
                             + esc(spec['sections'][name].get('specification', '')) + '</pre></details>')
    reference.append('<details><summary>Open questions and recorded decisions</summary><pre>'
                     + esc(json.dumps({'questions': spec.get('questions'), 'decisions': decisions}, indent=2)) + '</pre></details>')
    reference.append('<details><summary>Acceptance scenarios — NOT RUN</summary><pre>'
                     + esc(json.dumps(spec.get('tests'), indent=2)) + '</pre></details>')
    articles[0] += ''.join(reference) + '</article>'
    for screen, actions in zip(screen_list, linked_actions(screen_list)):
        part, index = part_of[screen['id']]
        steps = []
        for step, item in enumerate(part['screens'], start=1):
            label = str(step) + '. ' + esc(plain_title(item))
            if item['id'] == screen['id']:
                steps.append('<li><span class="here">' + label + '</span></li>')
            else:
                steps.append('<li><a href="#screen-' + esc(item['id']) + '">' + label + '</a></li>')
        field_rows = []
        for field in screen['fields']:
            label, hint = _field_parts(field)
            field_rows.append('<label>' + esc(label) + ('<small>' + esc(hint) + '</small>' if hint else '')
                              + '<input placeholder="Illustrative only"></label>')
        buttons = []
        stays = []
        for action in actions:
            if action['screen_id']:
                buttons.append('<a class="action" href="#screen-' + esc(action['screen_id']) + '" title="'
                               + esc(action['label']) + '">' + esc(_button_label(action['label'])) + '</a>')
            else:
                stays.append(esc(_button_label(action['label'])))
        service_links = ''.join('<a href="api.html#op-' + esc(api['id']) + '">' + esc(_spoken(api.get('purpose') or '')[0] or api['path'])
                                + '</a>' for api in _services(screen, screens.get('apis') or []))
        outcomes = ''.join('<li>' + esc(state) + '</li>' for state in screen['states'])
        articles.append('<article class="screen" id="screen-' + esc(screen['id']) + '"><p class="kicker"><a href="#flow">All parts</a> · '
                        + esc(part['name']) + ' · Step ' + str(index) + ' of ' + str(len(part['screens'])) + '</p><h1>'
                        + esc(plain_title(screen)) + '</h1><p>' + esc(screen['purpose']) + '</p><h2>Steps in this part</h2><ol class="steps">'
                        + ''.join(steps) + '</ol><p>' + esc(part['summary']) + '</p>'
                        + ('<h2>What you enter</h2>' + ''.join(field_rows) if field_rows else '')
                        + ('<h2>Where you can go</h2>' if buttons else '')
                        + ''.join(buttons) + ('<p class="stay">Stays on this screen: ' + ', '.join(stays) + '</p>' if stays else '')
                        + '<h2>Services this step uses</h2>' + (service_links or '<p class="stay">No service is linked to this screen.</p>')
                        + _rules_panel(screen, rules, esc) + '<h2>What can happen</h2><ul>' + outcomes + '</ul><details><summary>Open questions</summary><p>'
                        + esc(screen['uncertainties']) + '</p></details></article>')
    return _page(esc(title), '<aside>' + ''.join(navigation) + '</aside><main>' + ''.join(articles) + '</main>')


def provider_usage(directories):
    """Read provider receipts. Session identifiers and reply text stay out of the result."""
    rows = []
    for directory in directories:
        folder = Path(directory)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob('provider-*.json')):
            if path.name.startswith('provider-request'):
                continue
            try:
                receipt = json.loads(path.read_text())
                envelope = json.loads(receipt.get('stdout') or '')
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(envelope, dict) or not isinstance(envelope.get('usage'), dict):
                continue
            usage = envelope['usage']
            reported = envelope.get('modelUsage') if isinstance(envelope.get('modelUsage'), dict) else {}
            model = next(iter(reported), receipt.get('requested_model') or 'unreported')
            detail = reported.get(model) if isinstance(reported.get(model), dict) else {}
            price = detail.get('costUSD', envelope.get('total_cost_usd'))
            rows.append({
                'role': receipt.get('role') or 'unreported',
                'provider': receipt.get('provider') or 'unreported',
                'model': str(model),
                'input': int(usage.get('input_tokens') or 0),
                'output': int(usage.get('output_tokens') or 0),
                'cache_write': int(usage.get('cache_creation_input_tokens') or 0),
                'cache_read': int(usage.get('cache_read_input_tokens') or 0),
                'thinking': int(detail.get('thinkingTokens') or 0),
                'list_price': float(price or 0),
                'cost_basis': str(detail.get('costBasis') or ''),
            })
    return rows


def render_usage_page(rows, settings=None, title='Token spend'):
    """Show the provider's printed list price and token counts. This is not a billing invoice."""
    esc = lambda value: html.escape(str(value), quote=True)
    settings = settings or {}
    ledger = float(settings.get('model_dollars_spent') or 0)
    cap = float(settings.get('max_model_dollars') or 0)

    def money(value):
        return '$' + format(value, ',.2f')

    def count(value):
        return format(int(value), ',')

    def grouped(key):
        buckets = {}
        for row in rows:
            item = buckets.setdefault(row[key], {'calls': 0, 'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0, 'thinking': 0, 'list_price': 0.0})
            item['calls'] += 1
            for field in ('input', 'output', 'cache_write', 'cache_read', 'thinking'):
                item[field] += row[field]
            item['list_price'] += row['list_price']
        return buckets

    total_price = sum(row['list_price'] for row in rows)
    bases = sorted({row['cost_basis'] for row in rows if row['cost_basis']})
    basis = ', '.join(bases) if bases else 'not reported'
    notice = ('The amounts are the list price printed on each provider receipt'
              + (' (' + esc(basis) + ')' if bases else '')
              + '. The local dollar ledger recorded ' + money(ledger) + ' against a cap of ' + money(cap) + '.')
    def table(buckets):
        widest = max((item['list_price'] for item in buckets.values()), default=0) or 1
        body = []
        for name, item in sorted(buckets.items(), key=lambda pair: -pair[1]['list_price']):
            width = max(1, round(item['list_price'] / widest * 100))
            body.append('<tr><td>' + esc(name) + '</td><td>' + count(item['calls']) + '</td><td>' + count(item['input'])
                        + '</td><td>' + count(item['output']) + '</td><td>' + count(item['cache_write'])
                        + '</td><td>' + count(item['cache_read']) + '</td><td>' + money(item['list_price'])
                        + '</td><td><div class="bar"><span style="width:' + str(width) + '%"></span></div></td></tr>')
        return ''.join(body)
    heading = '<p class="notice">' + notice + '</p><p><strong>' + count(len(rows)) + ' calls</strong> · list price ' + money(total_price) + '</p>'
    if not rows:
        heading += '<p>No provider receipt with token counts was found for this run.</p>'
    article = ('<article class="screen"><p class="kicker"><a href="index.html#flow">Application flow</a> · <a href="api.html">Services</a></p>'
               '<h1>Token spend</h1>' + heading
               + '<h2>By step</h2><table><thead><tr><th>Step</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache write</th><th>Cache read</th><th>List price</th><th></th></tr></thead><tbody>'
               + table(grouped('role')) + '</tbody></table><h2>By model</h2><table><thead><tr><th>Model</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache write</th><th>Cache read</th><th>List price</th><th></th></tr></thead><tbody>'
               + table(grouped('model')) + '</tbody></table></article>')
    return _page(esc(title), '<aside><a href="index.html#flow">Application flow</a><a href="api.html">Services</a></aside><main>' + article + '</main>')


def render_assessment_facts(run, directory, link):
    """Deterministic counts and verbatim descriptions from sealed AWS exports."""
    import csv
    import io
    from collections import Counter
    index = {item['path'] for item in run['assessment_index']}
    datasets = {}
    descriptions = []
    for original in run.get('assessment_originals', []):
        key = original.get('source', {}).get('assetKey', '')
        if Path(key).name == 'analyze_code_results.zip':
            for item in original.get('text_files', []):
                member = Path(item['member']).name
                kind = next((kind for kind, prefix in [('inventory','assets_'),('issues','code_issues_'),('missing','missing_')]
                             if member.startswith(prefix) and member.endswith('.csv')), None)
                if not kind:
                    continue
                require(kind not in datasets, 'Multiple AWS summary inventories; select an unambiguous assessment export')
                require(item['file'] in index, 'Summary input is not sealed assessment evidence')
                reader = csv.DictReader(io.StringIO((directory/'assessment'/item['file']).read_text()))
                required = {'inventory': {'Path','File type'}, 'issues': {'Severity'}, 'missing': {'Type'}}[kind]
                require(required.issubset(reader.fieldnames or []), 'AWS summary CSV schema changed')
                datasets[kind] = (list(reader), 'assessment/' + item['file'])
        if key.endswith('/documentation/metadata/summary.json'):
            value = json.loads((directory/original['file']).read_text())
            if isinstance(value, dict):
                programs = {k:v for k,v in value.items() if k not in {'application','metadata'}
                            and isinstance(k,str) and isinstance(v,str)}
                application = value.get('application') if isinstance(value.get('application'),str) else None
                if programs or application:
                    descriptions.append((programs, original['file'], application))
    if not datasets and not descriptions:
        return '<section><h2>AWS Transform facts</h2><p>No supported AWS inventory or supplied overview was found in the sealed evidence. No summary has been inferred.</p></section>'
    def table(title, counts):
        """Render one count table from labels supplied by AWS."""
        return ('<h3>' + title + '</h3><table><thead><tr><th>AWS label</th><th>Count</th></tr></thead><tbody>'
                + ''.join('<tr><td>' + html.escape(str(k)) + '</td><td>' + str(v) + '</td></tr>'
                          for k,v in sorted(counts.items(), key=lambda pair:(-pair[1],str(pair[0])))) + '</tbody></table>')
    blocks = ['<section><h2>AWS Transform facts</h2><p>Calculated directly from AWS export rows; no LLM was called to prepare this summary. Labels are reproduced as supplied by AWS.</p>']
    if 'inventory' in datasets:
        rows, source = datasets['inventory']
        paths = {row['Path'] for row in rows if row['Path']}
        blocks.append('<p><strong>' + str(len(rows)) + ' file records</strong> in the AWS inventory; '
                      + str(len(paths)) + ' distinct nonempty source paths. ' + link(source,'Source: AWS asset inventory') + '</p>')
        blocks.append(table('File types',Counter(row['File type'] or 'Not reported' for row in rows)))
        values = [row.get('Total lines','').strip() for row in rows]
        if values and all(value.isdigit() for value in values):
            blocks.append('<p>Total lines reported in the inventory: <strong>' + format(sum(int(v) for v in values),',') + '</strong>.</p>')
    if 'issues' in datasets:
        rows, source = datasets['issues']
        blocks.append('<p><strong>' + str(len(rows)) + ' issue occurrences</strong> (CSV rows, not unique issue categories). '
                      + link(source,'Source: AWS code issues') + '</p>')
        blocks.append(table('Issue severity',Counter(row['Severity'] or 'Not reported' for row in rows)))
    if 'missing' in datasets:
        rows, source = datasets['missing']
        blocks.append('<p><strong>' + str(len(rows)) + ' missing-dependency records</strong> (CSV rows). '
                      + link(source,'Source: AWS missing dependencies') + '</p>')
        blocks.append(table('Missing dependency types',Counter(row['Type'] or 'Not reported' for row in rows)))
    blocks.append('</section>')
    for value, source, application in descriptions:
        if application:
            blocks.append('<section><h2>AWS-provided application overview</h2>'
                          '<p>Verbatim AWS Transform output; not rewritten or independently verified. '
                          + link(source,'Source: AWS documentation summary') + '</p><blockquote style="white-space:pre-wrap">'
                          + html.escape(application) + '</blockquote></section>')
        blocks.append('<details><summary>AWS-provided program and job descriptions (' + str(len(value)) + ')</summary>'
                      '<p>Verbatim AWS-generated descriptions, not a new AI summary or an independently verified application overview. Short or incomplete descriptions are preserved as supplied. '
                      + link(source,'Source: AWS documentation summaries') + '</p><dl>'
                      + ''.join('<dt><strong>' + html.escape(k) + '</strong></dt><dd>' + html.escape(v) + '</dd>' for k,v in sorted(value.items()))
                      + '</dl></details>')
    return ''.join(blocks)
