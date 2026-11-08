"""Bounded S3 ZIP snapshots. Git indexes track files without inventing source commits."""
import json
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from pilot import require, sha, git


def extract_archive(archive, destination, limit=100000000):
    require(not destination.exists(), 'Source snapshot destination already exists')
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(0 < len(entries) <= 20000, 'ZIP entry count exceeds supported bounds')
        require(sum(item.file_size for item in entries) <= limit, 'Expanded source exceeds byte limit')
        seen = set()
        for item in entries:
            p = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in item.filename
                    and not any(part.startswith('.') for part in p.parts)
                    and not stat.S_ISLNK(mode), 'Unsafe source archive member')
            require(item.filename.casefold() not in seen, 'Duplicate or case-colliding ZIP entry')
            seen.add(item.filename.casefold())
            require(not item.flag_bits & 1, 'Encrypted source ZIP is unsupported')
        destination.mkdir(mode=0o700)
        try:
            for item in entries:
                target = destination / item.filename
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)
        except Exception:
            shutil.rmtree(destination)
            raise


def from_s3(uri, work, profile, region, limit=100000000):
    parsed = urlsplit(uri)
    require(parsed.scheme == 's3' and parsed.netloc and parsed.path.lower().endswith('.zip')
            and not parsed.query and not parsed.fragment, 'Supply an S3 ZIP object URI')
    command = ['aws']
    if profile: command += ['--profile', profile]
    if region: command += ['--region', region]
    def aws(*args):
        result = subprocess.run(command + list(args), capture_output=True, text=True, timeout=300)
        require(result.returncode == 0, 'S3 source access failed; verify the selected AWS login and object permissions')
        return json.loads(result.stdout)
    key = parsed.path[1:]
    metadata = aws('s3api','head-object','--bucket',parsed.netloc,'--key',key)
    require(0 < metadata.get('ContentLength', 0) <= limit, 'Source ZIP exceeds download limit')
    archive = work / 'input-source.zip'
    options = ['--version-id', metadata['VersionId']] if metadata.get('VersionId') else ['--if-match', metadata['ETag']]
    aws('s3api','get-object','--bucket',parsed.netloc,'--key',key,*options,str(archive))
    archive.chmod(0o600)
    source = work / 'source'
    extract_archive(archive, source, limit)
    git(source, 'init')
    git(source, '-c', 'core.hooksPath=/dev/null', 'add', '--force', '--all')
    git(source, 'remote', 'add', 'origin', uri)
    return {'kind':'s3-zip','root':str(source),'repository':uri,'commit':git(source,'write-tree'),
            'revision_type':'git-index-tree','archive_sha256':sha(archive),
            's3_version_id':metadata.get('VersionId'),'s3_etag':metadata.get('ETag')}
