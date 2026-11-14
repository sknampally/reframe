"""Human-guided orchestration of existing CLI operations; never bypass their gates."""
import json
from datetime import datetime
from pathlib import Path
from pilot import Blocked, require


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
        report = getattr(ui, 'last_report', None) or {}
        ui.say(str(report.get('hint') or report.get('reason') or 'The update was not recorded. /flow retries it before approval, skip, or review.'))
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
            ('API reference', 'HTML', str(directory / 'api.html')),
            ('OpenAPI document', 'JSON', str(directory / 'openapi.json')),
            ('API contracts', 'JSON', str(directory / 'api-contracts.json')),
            ('Token spend', 'HTML', str(directory / 'usage.html')),
        ]
    return [
        ('Business review wireframes', 'HTML', 'previews/business-*/index.html'),
        ('Screen content', 'JSON', 'previews/business-*/screens.json'),
        ('API reference', 'HTML', 'previews/business-*/api.html'),
        ('OpenAPI document', 'JSON', 'previews/business-*/openapi.json'),
        ('API contracts', 'JSON', 'previews/business-*/api-contracts.json'),
        ('Token spend', 'HTML', 'previews/business-*/usage.html'),
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
    ui.say('The review plan needs at least ' + str(needed) + ' more model calls. The saved allowance leaves '
           + str(state.get('remaining_calls')) + '. ' + str(spent) + ' model calls were already used.')
    cap = spent + needed if isinstance(needed, int) else None
    if not isinstance(cap, int) or not spent < cap:
        ui.say('The remaining plan has no usable call count. No model call was made.')
        return None
    if not ui.yes('Raise the model-call cap to ' + str(cap) + ' and start the review?'):
        if ui.yes('Remove the saved call cap and keep the completed work?'):
            owner = ui.ask('Accountable owner for this call budget')
            note = ui.ask('Reason for this call budget')
            if ui.invoke(['model-budget', '--clear-call-cap', '--by', owner, '--note', note]) != 0:
                return None
            if ui.invoke(['validate']) != 0:
                return ui.project()
            return ui.project()
        ui.say('Review stayed within the saved cap. No model call was made.')
        return None
    owner = ui.ask('Accountable owner for this call budget')
    note = ui.ask('Reason for this call budget')
    if ui.invoke(['model-budget', '--max-calls', str(cap), '--by', owner, '--note', note]) != 0:
        return None
    if ui.invoke(['validate']) != 0:
        return ui.project()
    return ui.project()


def offer_dollar_budget(ui, project):
    """Ask before raising the model dollar cap. The provider account remains the spend limit."""
    state = project.get('pipeline') or {}
    if state.get('reason') != 'Model call would exceed the dollar cap':
        return None
    settings = project.get('settings') or {}
    spent = settings.get('model_dollars_spent', 0)
    cap = settings.get('max_model_dollars', 0)
    price = state.get('next_call_price') or 0
    ui.say('Model dollars spent ' + str(spent) + ' of cap ' + str(cap) + '. The next billed call needs ' + str(price) + '.')
    if not ui.yes('Raise the model dollar cap so this call can run?'):
        ui.say('The review stayed within the saved dollar cap. No model call was made.')
        return None
    owner = ui.ask('Accountable owner for this dollar budget')
    note = ui.ask('Reason for this dollar budget')
    amount = ui.ask('Model dollar cap', str(round(max(cap, spent) + price, 2)))
    try:
        value = float(amount)
    except ValueError:
        ui.say('The dollar cap was not a number. No model call was made.')
        return None
    if ui.invoke(['model-budget', '--max-dollars', str(value), '--by', owner, '--note', note]) != 0:
        return None
    return ui.project()


def _choose_match(ui, matches):
    """Ask only when more than one saved workspace matches the repository."""
    if len(matches) == 1:
        return matches[0]
    for index, folder in enumerate(matches, start=1):
        ui.say(str(index) + ' ' + str(folder))
    picked = ui.ask('Saved workspace number', '1')
    require(picked.isdigit() and 1 <= int(picked) <= len(matches), 'Choose one of the saved workspaces')
    return matches[int(picked) - 1]


def offer_known_repository(ui, repository=None):
    """Recognize a repository before setup. A failed reuse check does not provision a replacement."""
    repository = repository or ui.ask('Repository URL to recognize')
    from reframe_projects import load_project, matching_workspaces, pin_requested_revision, verify_saved_infrastructure
    matches = matching_workspaces(repository)
    if not matches:
        ui.say('No saved project matches this repository. A new project is separate from every saved sandbox.')
        if not ui.yes('Create a new project?'):
            ui.say('No project was created.')
            return False
        return repository
    folder = _choose_match(ui, matches)
    saved = load_project(folder)
    ui.say('Saved project recognized at ' + str(folder) + '.')
    choice = ui.ask('Choose resume, reuse-baseline, assess-new-revision, or new-project', 'resume')
    require(choice in {'resume', 'reuse-baseline', 'assess-new-revision', 'new-project'},
            'Choose resume, reuse-baseline, assess-new-revision, or new-project')
    if choice == 'new-project':
        ui.say('A new project does not reuse this sandbox.')
        if not ui.yes('Create a new project?'):
            ui.say('No project was created.')
            return False
        return repository
    try:
        verify_saved_infrastructure(saved)
    except Blocked as error:
        ui.say(str(error))
        ui.say('Reuse is paused. No new resources were created.')
        return False
    from reframe_optional import resource_plan
    from reframe_setup import select_workspace
    import reframe
    intent = {'resume': 'resume', 'reuse-baseline': 'reuse-baseline', 'assess-new-revision': 'new-assessment'}[choice]
    plan = resource_plan(saved, intent)
    ui.say('Infrastructure verified. AWS jobs created: ' + str(plan['aws_jobs_created']) + '.')
    select_workspace(folder)
    if choice == 'reuse-baseline':
        ui.say('The saved baseline stays in place. No assessment job was created.')
        return True
    if choice == 'resume':
        ui.say('Resuming the saved job. No new assessment job was created.')
        return True
    if (saved.get('source') or {}).get('root'):
        ref = ui.ask('Source revision to assess', saved['source'].get('commit') or 'HEAD')
        try:
            pinned = pin_requested_revision(folder, saved, ref)
        except Blocked as error:
            ui.say(str(error))
            ui.say('Reuse is paused. No new resources were created.')
            return False
        reframe.persist(folder, saved)
        ui.say('Pinned revision ' + pinned['commit'] + '. The saved baseline and its approvals stay in place.')
    if not ui.yes('Authorize one new assessment of this revision on the verified infrastructure?'):
        ui.say('No assessment job was created.')
        return True
    command = ['assess', '--wait', '--allow-new-assessment']
    pending = saved.get('pending_revision') or {}
    if pending.get('commit'):
        command += ['--label', 'revision-' + pending['commit'][:8]]
    if ui.invoke(command) != 0:
        ui.say('The assessment was not started. No replacement sandbox was created.')
        return False
    return True


def finish_guided_design(ui, project):
    """Ask for mode, version, stack, and optional context, then write the draft package."""
    from reframe import budget_summary
    work = workspace_path()
    summary = budget_summary(project or {}, work)

    def cap_text(value):
        return 'no call cap' if value is None else 'cap ' + str(value)

    ui.say('Preparation model calls used ' + str(summary['preparation_calls']) + ' with ' + cap_text(summary['preparation_cap'])
           + '. Completed units ' + str(summary.get('completed_units', 0)) + '. Remaining units ' + str(summary.get('remaining_units', 0)) + '.')
    ui.say('Wireframe model calls used ' + str(summary['wireframe_calls']) + ' with ' + cap_text(summary['wireframe_cap']) + '.')
    ui.say('Skip-draft model calls used ' + str(summary['skip_draft_calls'])
           + '. Failed attempts ' + str(summary.get('skip_failures', 0)) + ' of limit ' + str(summary.get('skip_failure_limit', 3)) + '.')
    ui.say('Measured model dollars ' + str(summary['dollars_measured']) + ' of cap ' + str(summary['dollar_cap']) + '.')
    if summary.get('next_call_estimate') is not None:
        ui.say('Estimated next billed call ' + str(summary['next_call_estimate']) + '. This estimate is not measured spend.')
    mode = ui.ask('Design mode (functional-parity/reimagine)', 'functional-parity')
    require(mode in {'functional-parity', 'reimagine'}, 'Choose functional-parity or reimagine')
    version = ui.ask('Design version', 'v1')
    stack = ui.ask('Target stack', 'unspecified')
    context = ui.ask('Human context file, or none', 'none')
    if context not in {'', 'none'}:
        author = ui.ask('Context author')
        role = ui.ask('Context role', 'architect')
        category = ui.ask('Context category (current-state/future-state/clarification)', 'clarification')
        require(category in {'current-state', 'future-state', 'clarification'}, 'Choose a context category')
        affected = ui.ask('Affected ids, comma separated', '')
        if ui.invoke(['context', 'import', '--file', context, '--by', author, '--role', role,
                      '--category', category, '--affected', affected]) != 0:
            ui.say('The context file was not imported. The design was not written.')
            return
        from reframe_optional import context_status
        pending = [item for item in context_status(work).get('submissions') or [] if item.get('disposition') == 'proposed']
        disposition = ui.ask('Disposition for the imported context (accepted/rejected)', 'rejected')
        require(disposition in {'accepted', 'rejected'}, 'Choose accepted or rejected')
        if pending and ui.invoke(['context', 'decide', '--id', pending[-1]['id'], '--disposition', disposition,
                                  '--by', author, '--note', 'Recorded in the guided flow']) != 0:
            ui.say('The context disposition was not recorded. The design was not written.')
            return
        if category == 'future-state' and disposition == 'accepted':
            ui.say('Accepting this note records input only. It does not authorize a behavior change.')
    if not offer_open_conflicts(ui, work):
        return
    if ui.invoke(['design-export', '--version', version, '--mode', mode, '--stack', stack]) != 0:
        say_design_recovery(ui)
        return
    report = getattr(ui, 'last_report', None) or {}
    if report.get('resumed'):
        ui.say('Incomplete generation was resumed from the saved selection. No new model call was required.')
        ui.say('Review the completed version before packaging.')
    if category_was_future(context, locals()):
        if ui.yes('Approve one future-state delta as an implementation requirement?'):
            proposal = ui.ask('Proposal id to approve')
            owner = ui.ask('Delta approver')
            note = ui.ask('Delta approval note')
            if ui.invoke(['context', 'approve-delta', '--version', version, '--id', proposal, '--by', owner, '--note', note]) != 0:
                ui.say('The delta was not approved. The proposal stays proposed_unapproved.')
    if ui.invoke(['package-export', '--version', version, '--mode', mode]) != 0:
        ui.say('The draft ZIP was not written. /flow retries packaging. No final handoff was created.')
        return
    ui.say('Draft ' + mode + ' package written for ' + version + '. It stays DRAFT and is not the authorized implementation handoff.')


def offer_open_conflicts(ui, work):
    """Show conflicts that already exist, including when this turn imports no context."""
    from reframe_optional import open_conflicts
    conflicts = open_conflicts(work) if work else []
    if not conflicts:
        return True
    ui.say(str(len(conflicts)) + ' unresolved context conflicts are visible. Final approval and authorized handoff stay blocked until they are resolved.')
    for conflict in conflicts:
        ui.say('Conflict ' + str(conflict.get('id')) + ' contradicts ' + str(conflict.get('contradicts') or 'recorded evidence') + '.')
    if ui.yes('Resolve the open context conflict now?'):
        conflict = conflicts[0]
        resolution = ui.ask('Conflict resolution (keep-evidence/keep-human-statement/needs-more-evidence)', 'keep-evidence')
        owner = ui.ask('Conflict resolver')
        note = ui.ask('Conflict resolution note')
        if ui.invoke(['context', 'resolve', '--id', conflict['id'], '--resolution', resolution,
                      '--by', owner, '--note', note]) != 0:
            ui.say('The conflict remains open. The draft can still be written. Final approval stays blocked.')
        return True
    if ui.yes('Defer the open conflict and continue the draft? Final approval stays blocked.'):
        ui.say('The conflict stays open. The draft can still be written. Final approval stays blocked.')
        return True
    ui.say('Paused. Resolve the conflict or defer it before the design is written.')
    return False


def say_design_recovery(ui):
    """Tell the operator which recovery applies without repeating hidden paths."""
    from reframe_terminal import recovery_notice
    report = getattr(ui, 'last_report', None) or {}
    notice = recovery_notice(report.get('reason'), report)
    if notice:
        ui.say(notice['summary'])
        ui.say(notice['next_step'])
        return
    ui.say('The design was not written. /flow resumes the same version when its completion record is missing.')


def category_was_future(context, names):
    """True when this turn imported a future-state note."""
    return context not in {'', 'none'} and names.get('category') == 'future-state' and names.get('disposition') == 'accepted'


def run(ui):
    """Walk a saved project from setup through assessment, skip or review, approvals, and the package."""
    from reframe_setup import workspace
    project = ui.project()
    if project:
        choice = ui.ask('Continue saved project, recognize a repository, or start a fresh run? (continue/recognize/fresh)', 'continue')
        require(choice in {'continue', 'recognize', 'fresh'}, 'Choose continue, recognize, or fresh')
        if choice == 'recognize':
            recognized = offer_known_repository(ui, (project.get('source') or {}).get('repository'))
            if not recognized:
                return
            if isinstance(recognized, str) and ui.invoke(['init', '--guided', '--source', recognized]) != 0:
                return
            project = ui.project()
        elif choice == 'fresh':
            source = (project.get('source') or {}).get('repository')
            require(source, 'The saved project has no repository to carry into setup')
            ui.say('The new run uses the recognized repository ' + source + '.')
            parent = ui.ask('Parent directory for the new run', str(workspace().parent / ('run-' + datetime.now().strftime('%Y%m%d-%H%M%S'))))
            from reframe_workspace import output_directory
            require(not output_directory(parent, source).exists(), 'Output directory already exists; choose another parent or continue it')
            if ui.invoke(['init', '--guided', '--source', source, '--output-parent', parent]) != 0:
                return
            project = ui.project()
        else:
            repository = (project.get('source') or {}).get('repository')
            if repository:
                from reframe_projects import matching_workspaces
                from reframe_setup import select_workspace
                matches = matching_workspaces(repository)
                if len(matches) > 1:
                    folder = _choose_match(ui, matches)
                    select_workspace(folder)
                    project = ui.project()
    if not project:
        recognized = offer_known_repository(ui)
        if not recognized:
            return
        if isinstance(recognized, str) and ui.invoke(['init', '--guided', '--source', recognized]) != 0:
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
            interrupted = stage in {'WAITING_FOR_RUNTIME', 'RUNNING_EXECUTOR', 'RUNNING_REVIEWER', 'PREPARATION_PLANNED', 'REWORK_LIMIT'} and project.get('pipeline')
            if interrupted:
                ui.say('Resuming the saved review. Completed calls stay recorded. No new assessment job is created.')
                code = ui.invoke(['resume'])
                updated = ui.project() or {}
            else:
                ui.say('Assessment is approved. skip generates wireframes and the AI-DLC package without a second review. review runs the executor and an independent reviewer.')
                choice = ui.ask('Next step (skip/review)', 'skip')
                require(choice in {'skip', 'review', 'draft', 'verify'}, 'Choose skip or review')
            if not interrupted and choice in {'skip', 'draft'}:
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
                finish_guided_design(ui, ui.project() or project)
                return
            if not interrupted:
                if not ui.yes('Run independent verification using the saved model-call budget?'):
                    return
                code = ui.invoke(['validate'])
                updated = ui.project() or {}
            recovery_used = interrupted and (updated.get('stage') == 'REWORK_LIMIT')
            for _budget in range(6):
                if updated.get('stage') == 'AWAITING_VALIDATION_APPROVAL':
                    break
                if updated.get('stage') == 'REWORK_LIMIT':
                    if recovery_used:
                        report = getattr(ui, 'last_report', None) or {}
                        if (updated.get('checkpoints') or {}).get('status') == 'READY_FOR_SYNTHESIS':
                            ui.say(str(report.get('reason') or 'Synthesis review paused.'))
                            ui.say(str(report.get('next_step') or 'reframe resume'))
                        else:
                            ui.say('Review paused on unit ' + str(report.get('unit') or 'unknown') + '.')
                            ui.say(str(report.get('next_step') or 'Authorize one targeted correction with reframe feedback --authorize-correction.'))
                        ui.say('No new assessment job is created.')
                        return
                    recovery_used = True
                    ui.say('Resuming the saved review. Completed calls stay recorded. No new assessment job is created.')
                    code = ui.invoke(['resume'])
                    updated = ui.project() or {}
                    continue
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
                if state.get('reason') == 'Model call would exceed the dollar cap':
                    updated = offer_dollar_budget(ui, updated)
                    if updated is None:
                        return
                    code = ui.invoke(['validate'])
                    updated = ui.project() or {}
                    continue
                # A short allowance returns a blocked status. Calls already spent still reach the question below.
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
            finish_guided_design(ui, ui.project() or project)
            return
        if stage == 'PACKAGED':
            say_review(ui, 'The approved package is already written.', package_rows(project))
            finish_guided_design(ui, project)
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
