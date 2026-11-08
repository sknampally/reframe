"""Repository-named output roots and verified relocation of private workspaces."""
import html
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from pilot import require, sha


def repository_name(source):
    value = str(source).replace('\\', '/').rstrip('/')
    path = urlsplit(value).path if '://' in value else value
    name = path.rsplit('/', 1)[-1].removesuffix('.git')
    if name.endswith('.zip'): name = name[:-4]
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name).strip('._-')
    require(bool(name), 'Cannot derive a repository name from this source')
    return name


def default_parent(source, framework):
    cwd = Path.cwd().resolve()
    if cwd.is_relative_to(framework.resolve()): return framework.resolve().parent
    if '://' not in source:
        root=Path(source).expanduser().resolve()
        if cwd.is_relative_to(root): return root.parent
    return cwd


def output_directory(parent, source):
    return Path(parent).expanduser().resolve() / (repository_name(source) + '_assessment')


def preview_directory(work, name):
    legacy=work/('preview-'+name)
    target=work/'previews'/name
    if legacy.exists() and not target.exists(): return legacy
    target.parent.mkdir(exist_ok=True,mode=0o700)
    return target


def report_index(work):
    paths=[]
    for pattern in ('runs/*/assessment-review.html','preparation/review.html',
                    'previews/*/handoff/index.html','previews/business-*/index.html','preview-*/handoff/index.html','review-site-*/index.html'):
        paths.extend(work.glob(pattern))
    links=[]
    for path in sorted(set(paths)):
        relative=path.relative_to(work).as_posix()
        links.append('<li><a href="../'+quote(relative,safe='/')+'">'+html.escape(relative)+'</a></li>')
    for path in sorted((work/'packages').rglob('*.zip')) if (work/'packages').exists() else []:
        relative=path.relative_to(work).as_posix()
        links.append('<li><a href="../'+quote(relative,safe='/')+'">'+html.escape(relative)+'</a></li>')
    directory=work/'reports';directory.mkdir(exist_ok=True,mode=0o700)
    path=directory/'index.html'
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Assessment outputs</title>'
                   '<style>body{font:17px/1.7 system-ui;max-width:1000px;margin:48px auto;padding:0 24px}li{margin:14px 0}</style>'
                   '<h1>Assessment outputs</h1><p>Private reports, wireframes and packages for '+html.escape(work.name)+'.</p><ul>'
                   +(''.join(links) or '<li>No reports generated yet.</li>')+'</ul></html>')
    path.chmod(0o600)
    return path


def rebase(value, old, new):
    if isinstance(value,dict): return {k:rebase(v,old,new) for k,v in value.items()}
    if isinstance(value,list): return [rebase(v,old,new) for v in value]
    if isinstance(value,str) and (value==str(old) or value.startswith(str(old)+'/')):
        return str(new)+value[len(str(old)):]
    return value


def relocate(work, project, parent, app):
    from reframe_setup import select_workspace
    work=work.resolve(); target=output_directory(parent,project['source']['repository'])
    require(target!=work, 'Workspace already has the requested repository-based location')
    require(not target.exists(), 'Destination already exists; no files overwritten')
    require(not target.is_relative_to(work) and not work.is_relative_to(target), 'Destination must be outside the existing assessment folder')
    require(not target.is_relative_to(app.FRAMEWORK.resolve()), 'Keep assessment output outside the framework repository')
    source=Path(project['source']['root']).resolve()
    require(source.is_relative_to(work) or not target.is_relative_to(source), 'Destination must be outside the source checkout')
    require(not source.is_relative_to(work) or (source/'.git').is_dir(), 'Managed source must be a standalone Git checkout before relocation')
    current=app.git(app.FRAMEWORK,'rev-parse','HEAD')
    app.verify_source({**project,'framework_commit':current})
    for label in project.get('assessments',{}): app.verified_assessment(project,label)
    # Reject active provider work, even if a different process forgot the CLI lock.
    require(project.get('stage') not in {'RUNNING_EXECUTOR','RUNNING_REVIEWER'}, 'Stop active agent work before relocating')
    for path in work.rglob('*'):
        require(not path.is_symlink(), 'Workspace contains symbolic links; inspect them before relocation')
    files={p.relative_to(work).as_posix():sha(p) for p in work.rglob('*') if p.is_file() and p.name!='.project.lock'}
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=target.parent/('.'+target.name+'-moving-'+uuid.uuid4().hex[:8])
    try:
        shutil.copytree(work,staging)
        require(all(sha(staging/name)==expected for name,expected in files.items()), 'Copied output verification failed; original workspace retained')
        updated=rebase(project,work,target)
        updated.setdefault('events',[]).append({'event':'workspace_relocated','from':str(work),'to':str(target),'at':time.time(),
                                               'verified_files':len(files)})
        app.write(staging/'project.json',updated)
        for record in project.get('assessments',{}).values():
            original=Path(record['directory'])/'run.json'
            require(original.is_relative_to(work), 'Assessment run is outside the workspace; relocate it explicitly')
            app.write(staging/original.relative_to(work),rebase(app.read(original),work,target))
        (staging/'previews').mkdir(exist_ok=True,mode=0o700)
        (staging/'packages').mkdir(exist_ok=True,mode=0o700)
        for legacy in list(staging.glob('preview-*')):
            if not legacy.is_dir() or not (legacy/'preview-state.json').is_file(): continue
            name=legacy.name[len('preview-'):]
            new=staging/'previews'/name
            require(not new.exists(), 'Conflicting preview directories; original workspace retained')
            legacy.rename(new)
        for preview in (staging/'previews').iterdir():
            state_file=preview/'preview-state.json'
            if not preview.is_dir() or not state_file.is_file():continue
            archive=preview/'ai-dlc-draft.zip'
            if archive.exists():
                destination=staging/'packages'/preview.name/'ai-dlc-draft.zip'
                require(not destination.exists(), 'Conflicting package destination')
                destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                archive.rename(destination)
                state=app.read(state_file);state['archive_path']=destination.relative_to(staging).as_posix();app.write(state_file,state)
        # Raw evidence, generated HTML, ZIP bytes and provider receipts are never rewritten.
        for label in updated.get('assessments',{}):
            check=json.loads(json.dumps(updated))
            check['assessments'][label]['directory']=str(staging/Path(updated['assessments'][label]['directory']).relative_to(target))
            app.verified_assessment(check,label)
        staging.rename(target)
        report_index(target)
        select_workspace(target)
    except Exception:
        if staging.exists(): shutil.rmtree(staging)
        raise
    # Only after verified copy and active-selection switch do we remove the old tree.
    cleanup_error = None
    try: shutil.rmtree(work)
    except OSError as error: cleanup_error = str(error)
    return {'cleanup_pending':str(work) if cleanup_error else None, 'status':'WORKSPACE_RELOCATED','workspace':str(target),'reports':str(target/'reports/index.html'),
            'verified_files':len(files),'next_step':'Use reframe status; the existing run and approvals are preserved.'}
