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


def run(ui):
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
                ui.invoke(['preview', '--skip-validation', '--by', owner, '--note', note,
                           '--provider', provider, '--name', name])
                ui.say('Use /artifacts for saved reports and packages (/privacy reveals local paths). Use /flow to resume.')
                return
            if not ui.yes('Run independent verification using the saved model-call budget?'):
                return
            if ui.invoke(['validate']) != 0:
                return
            updated = ui.project()
            if updated.get('stage') != 'AWAITING_VALIDATION_APPROVAL':
                ui.say('Verification paused. Inspect status and any budget or evidence blocker before continuing.')
                return
            stage = updated['stage']
        if stage in {'AWAITING_ASSESSMENT_APPROVAL', 'AWAITING_VALIDATION_APPROVAL', 'AWAITING_SPECIFICATIONS_APPROVAL'}:
            phase = {'AWAITING_ASSESSMENT_APPROVAL':'assessment', 'AWAITING_VALIDATION_APPROVAL':'validation', 'AWAITING_SPECIFICATIONS_APPROVAL':'specifications'}[stage]
            if phase == 'assessment' and ui.invoke(['assessment-report']) != 0:
                return
            ui.say('Review the saved reports before approval. /artifacts lists them when privacy is off. Unresolved decisions remain enforced.')
            if not ui.yes('Have you reviewed the ' + phase + ' outputs and want to record approval now?'):
                ui.say('Paused for human review. Return with /flow; no approval was recorded.')
                return
            if ui.invoke(['approve', phase]) != 0:
                return
            continue
        if stage == 'VALIDATION_APPROVED':
            if ui.invoke(['review-site']) != 0:
                return
            continue
        if stage == 'SPECIFICATIONS_APPROVED':
            ui.invoke(['package'])
            return
        if stage == 'PACKAGED':
            ui.say('Approved package already exists. Use /artifacts to find it.')
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
