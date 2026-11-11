"""Human-guided orchestration of existing CLI operations; never bypass their gates."""
import json
from datetime import datetime
from pathlib import Path
from pilot import require


def record_framework_update(ui, project):
    """Record a controller commit before approval. Findings stay on the saved run."""
    recorded = project.get('framework_commit')
    if not recorded:
        return True
    from reframe import FRAMEWORK, git
    if recorded == git(FRAMEWORK, 'rev-parse', 'HEAD'):
        return True
    ui.say('Reframe was updated after this run was saved. Record that update before approval. The findings stay unchanged.')
    note = ui.ask('Note for this Reframe update')
    if ui.invoke(['upgrade-framework', '--note', note]) != 0:
        ui.say('The update was not recorded. /flow retries it before approval, skip, or review.')
        return False
    return True


def workspace_path():
    """Return the active assessment folder, or nothing when no run is selected."""
    from pilot import Blocked
    from reframe_setup import workspace
    try:
        return workspace()
    except Blocked:
        return None


def say_review(ui, heading, rows):
    """Print each review file with its format and location."""
    ui.say(heading)
    for what, kind, location in rows:
        ui.say(what + ' — ' + kind + ' — ' + location)


def assessment_rows(project):
    """The assessment review page, the sealed findings, and the report index."""
    label = project.get('active_assessment') or project.get('selected')
    record = (project.get('assessments') or {}).get(label or '', {})
    directory = Path(record['directory']) if record.get('directory') else None
    if directory:
        work = directory.parent.parent if directory.parent.name == 'runs' else directory.parent
        return [
            ('Assessment review page', 'HTML', str(directory / 'assessment-review.html')),
            ('Sealed findings', 'ZIP, CSV, JSON, and PDF', str(directory / 'assessment')),
            ('Report index', 'HTML', str(work / 'reports' / 'index.html')),
        ]
    return [
        ('Assessment review page', 'HTML', 'runs/<run>/assessment-review.html'),
        ('Sealed findings', 'ZIP, CSV, JSON, and PDF', 'runs/<run>/assessment'),
        ('Report index', 'HTML', 'reports/index.html'),
    ]


def validation_rows(project):
    """The preparation review and the executor and reviewer JSON."""
    work = workspace_path()
    prefix = str(work) + '/' if work else ''
    spec = project.get('specification') or {}
    candidate = spec.get('path') or 'preparation/candidate-*.json'
    review = spec.get('review_path') or 'preparation/review-*.json'
    return [
        ('SME and architect review', 'HTML', prefix + 'preparation/review.html'),
        ('Executor candidate', 'JSON', prefix + candidate),
        ('Independent reviewer report', 'JSON', prefix + review),
    ]


def specification_rows(project):
    """The business wireframes and the screen and API JSON."""
    work = workspace_path()
    site = (project.get('site') or {}).get('directory')
    if work and site:
        directory = work / site
        return [
            ('Business review wireframes', 'HTML', str(directory / 'index.html')),
            ('Screen content', 'JSON', str(directory / 'screens.json')),
            ('API contracts', 'JSON', str(directory / 'api-contracts.json')),
        ]
    return [
        ('Business review wireframes', 'HTML', 'previews/business-*/index.html'),
        ('Screen content', 'JSON', 'previews/business-*/screens.json'),
        ('API contracts', 'JSON', 'previews/business-*/api-contracts.json'),
    ]


def draft_rows(name):
    """The skip wireframes, the handoff records, and the draft ZIP."""
    work = workspace_path()
    if not work:
        return [
            ('Wireframes', 'HTML', 'previews/' + name + '/handoff/index.html'),
            ('Imported rules', 'HTML', 'previews/' + name + '/handoff/rules.html'),
            ('Handoff records', 'JSON and Markdown', 'previews/' + name + '/handoff'),
            ('AI-DLC draft package', 'ZIP', 'packages/' + name + '/ai-dlc-draft.zip'),
        ]
    from reframe_workspace import preview_directory
    handoff = preview_directory(work, name) / 'handoff'
    archive = work / 'packages' / name / 'ai-dlc-draft.zip'
    return [
        ('Wireframes', 'HTML', str(handoff / 'index.html')),
        ('Imported rules', 'HTML', str(handoff / 'rules.html')),
        ('Handoff records', 'JSON and Markdown', str(handoff)),
        ('AI-DLC draft package', 'ZIP', str(archive)),
    ]


def package_rows(project):
    """The approved ZIP and the folder of HTML, JSON, and Markdown beside it."""
    work = workspace_path()
    recorded = (project.get('package') or {}).get('path')
    if work and recorded:
        archive = work / recorded
        return [
            ('Approved AI-DLC package', 'ZIP', str(archive)),
            ('Package files', 'HTML, JSON, and Markdown', str(archive.with_suffix(''))),
        ]
    return [
        ('Approved AI-DLC package', 'ZIP', 'packages/package-<version>.zip'),
        ('Package files', 'HTML, JSON, and Markdown', 'packages/package-<version>'),
    ]


def open_question_ids(project):
    """Question ids with no recorded decision. An absent specification file means none yet."""
    record = project.get('specification') or {}
    work = workspace_path()
    if not work or not record.get('path'):
        return []
    path = work / record['path']
    if not path.is_file():
        return []
    from reframe import read, spec_path
    spec = read(spec_path(work, project))
    decided = project.get('decisions') or {}
    return [q['id'] for q in spec.get('questions') or [] if isinstance(q, dict) and q.get('id') not in decided]


def defer_open_questions(ui, project):
    """Record an explicit deferral for every open SME question before validation approval."""
    pending = open_question_ids(project)
    if not pending:
        return True
    ui.say(str(len(pending)) + ' SME questions are still open. A deferral is recorded; it is not an answer.')
    if not ui.yes('Defer the ' + str(len(pending)) + ' open SME questions so the package can be written for review?'):
        ui.say('Paused. Record each SME answer with reframe decide, then return with /flow. No approval was recorded.')
        return False
    owner = ui.ask('Accountable owner for these deferred questions')
    note = ui.ask('Reason for deferring the open questions')
    for question in pending:
        if ui.invoke(['decide', '--question', question, '--by', owner, '--answer', note, '--disposition', 'deferred']) != 0:
            ui.say('A deferral was not recorded. /flow retries the remaining questions. No approval was recorded.')
            return False
    return True


def offer_review_budget(ui, project):
    """Say when the paired review does not fit the saved cap. A larger cap is recorded only after a yes."""
    state = project.get('pipeline') or {}
    if state.get('reason') != 'Insufficient call budget for remaining checkpoint plan':
        ui.say('Verification paused. Inspect status and any budget or evidence blocker before continuing.')
        return None
    needed = state.get('minimum_additional_calls')
    spent = state.get('calls', 0) or 0
    ui.say('The review plan needs at least ' + str(needed) + ' more model calls. The saved cap leaves '
           + str(state.get('remaining_calls')) + '. ' + str(spent) + ' model calls were already used.')
    cap = spent + needed if isinstance(needed, int) else None
    if not isinstance(cap, int) or not spent < cap <= 256:
        ui.say('That plan needs more than 256 model calls. The cap cannot be raised that far. No model call was made.')
        return None
    if not ui.yes('Raise the model-call cap to ' + str(cap) + ' and start the review?'):
        ui.say('Review stayed within the saved cap. No model call was made.')
        return None
    owner = ui.ask('Accountable owner for this call budget')
    note = ui.ask('Reason for this call budget')
    if ui.invoke(['model-budget', '--max-calls', str(cap), '--by', owner, '--note', note]) != 0:
        return None
    if ui.invoke(['validate']) != 0:
        return ui.project()
    return ui.project()


def run(ui):
    """Walk a saved project from setup through assessment, skip or review, approvals, and the package."""
    from reframe_setup import workspace
    project = ui.project()
    if project:
        choice = ui.ask('Continue saved project or start a fresh run? (continue/fresh)', 'continue')
        require(choice in {'continue', 'fresh'}, 'Choose continue or fresh')
        if choice == 'fresh':
            source = ui.ask('Source Git URL, local checkout or S3 ZIP', project['source']['repository'])
            parent = ui.ask('Parent directory for the new run', str(workspace().parent / ('run-' + datetime.now().strftime('%Y%m%d-%H%M%S'))))
            from reframe_workspace import output_directory
            require(not output_directory(parent, source).exists(), 'Output directory already exists; choose another parent or continue it')
            if ui.invoke(['init', '--guided', '--source', source, '--output-parent', parent]) != 0:
                return
            project = ui.project()
    if not project or project.get('stage') in {'INITIALIZED', 'WAITING_FOR_CONNECTOR_APPROVAL'}:
        if ui.invoke(['init', '--guided']) != 0:
            return
    for _ in range(12):
        project = ui.project()
        require(project is not None, 'Setup did not save a project; use /flow to retry')
        stage = project['stage']
        approved = bool(project.get('approvals', {}).get('assessment'))
        if not record_framework_update(ui, project):
            return
        if approved and not project.get('approvals', {}).get('validation') and stage != 'AWAITING_VALIDATION_APPROVAL':
            ui.say('Assessment is approved. skip generates wireframes and the AI-DLC package without a second review. review runs the executor and an independent reviewer.')
            choice = ui.ask('Next step (skip/review)', 'skip')
            require(choice in {'skip', 'review', 'draft', 'verify'}, 'Choose skip or review')
            if choice in {'skip', 'draft'}:
                ui.say('Claude or Codex will generate design content. No executor/reviewer verification or approval is implied.')
                from reframe import name as safe_name
                name = safe_name(ui.ask('Draft name (reuse the same name to resume)', 'guided-draft'))
                from reframe_workspace import preview_directory
                saved = preview_directory(workspace(), name) / 'preview-state.json'
                state = json.loads(saved.read_text()) if saved.exists() else {}
                owner = state.get('owner') or ui.ask('Accountable owner for skipping verification')
                note = state.get('reason') or ui.ask('Reason for trusting AWS findings without independent verification')
                provider = state.get('provider') or ui.ask('Design provider (claude/codex)', project.get('providers', {}).get('wireframes', 'claude'))
                require(provider in {'claude', 'codex'}, 'Choose claude or codex')
                if not ui.yes('Generate or resume this AWS-assumed draft wireframe site and AI-DLC ZIP?'):
                    return
                if ui.invoke(['preview', '--skip-validation', '--by', owner, '--note', note,
                              '--provider', provider, '--name', name]) != 0:
                    ui.say('The draft was not written. /flow retries it. No approval was recorded.')
                    return
                say_review(ui, 'Review these draft files. They are not an approval.', draft_rows(name))
                return
            if not ui.yes('Run independent verification using the saved model-call budget?'):
                return
            code = ui.invoke(['validate'])
            updated = ui.project() or ui.project() or {}
            for _budget in range(6):
                if updated.get('stage') == 'AWAITING_VALIDATION_APPROVAL':
                    break
                state = updated.get('pipeline') or {}
                settings = updated.get('settings') or {}
                spent = state.get('calls') or 0
                cap = settings.get('max_model_calls') or 0
                # Spending the last call returns exhaustion. The next check restates the remaining plan.
                if state.get('reason') == 'Total model call budget exhausted' or (
                        code != 0 and cap and spent >= cap
                        and state.get('reason') != 'Insufficient call budget for remaining checkpoint plan'):
                    ui.say('The call cap was spent. Checking how many calls the remaining programs need.')
                    code = ui.invoke(['validate'])
                    updated = ui.project() or {}
                    continue
                # A short cap returns a blocked status. Calls already spent still reach the question below.
                if code != 0 and state.get('reason') != 'Insufficient call budget for remaining checkpoint plan':
                    return
                if updated.get('stage') == 'AWAITING_VALIDATION_APPROVAL':
                    break
                updated = offer_review_budget(ui, updated)
                if updated is None:
                    return
                code = 0 if updated.get('stage') == 'AWAITING_VALIDATION_APPROVAL' else 2
            else:
                return
            stage = updated['stage']
            project = updated
        if stage in {'AWAITING_ASSESSMENT_APPROVAL', 'AWAITING_VALIDATION_APPROVAL', 'AWAITING_SPECIFICATIONS_APPROVAL'}:
            phase = {'AWAITING_ASSESSMENT_APPROVAL':'assessment', 'AWAITING_VALIDATION_APPROVAL':'validation', 'AWAITING_SPECIFICATIONS_APPROVAL':'specifications'}[stage]
            if phase == 'assessment' and ui.invoke(['assessment-report']) != 0:
                return
            rows = {'assessment': assessment_rows, 'validation': validation_rows, 'specifications': specification_rows}[phase](project)
            say_review(ui, 'Review these ' + phase + ' files before you approve. Unresolved decisions remain enforced.', rows)
            if phase == 'validation' and not defer_open_questions(ui, project):
                return
            if not ui.yes('Have you reviewed the ' + phase + ' outputs and want to record approval now?'):
                ui.say('Paused for human review. The files listed above are unchanged. Return with /flow; no approval was recorded.')
                return
            if ui.invoke(['approve', phase]) != 0:
                return
            continue
        if stage == 'VALIDATION_APPROVED':
            if ui.invoke(['review-site']) != 0:
                return
            continue
        if stage == 'SPECIFICATIONS_APPROVED':
            if ui.invoke(['package']) != 0:
                ui.say('The approved package was not written. /flow retries it.')
                return
            say_review(ui, 'The approved package is ready to review.', package_rows(ui.project()))
            return
        if stage == 'PACKAGED':
            say_review(ui, 'The approved package is already written.', package_rows(project))
            return
        if not approved:
            command = 'resume' if project.get('active_assessment') else 'assess'
            args = [command, '--wait']
            if command == 'assess':
                cap = ui.ask('Maximum expanded assessment evidence bytes (resource limit)', '250000000')
                require(cap.isdigit() and 0 < int(cap) <= 250000000, 'Enter a byte limit from 1 to 250000000')
                args += ['--max-assessment-bytes', cap]
            if ui.invoke(args) != 0:
                return
            if ui.project().get('stage') == 'AWAITING_ASSESSMENT_APPROVAL':
                continue
        ui.say('Work is saved. Use /flow to continue; inspect /status for any pending service task or blocker.')
        return
    ui.say('Paused after multiple transitions. Use /flow to continue from saved state.')
