"""The published runs in a private S3 bucket, opened with an AWS sign-in (docs/sharing.md, section 4; S-03, S-04, S-15).

Each published run sits under <project>/<run>/ in three folders: source/ (the snapshot both tools read), results/ (what
AWS Transform and Claude's plugin wrote, as Reframe retrieved it) and reports/ (the cleaned copy, with report.zip). The
bucket is one per account and region, apart from any run, so reframe teardown leaves it and what is in it.

The store has its own folder, ~/.reframe/reports-store, with the same records a run has: the Terraform root `reports`
with its local state, an intent and receipt for every change, and the teardown consent. Nothing is public: a reader
needs an AWS sign-in that may read the bucket.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
import secrets
import string
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from reframe.core.errors import NotSent, Outcome, Stop
from reframe.core.runfolder import Changes, RunFolder, home, iso
from reframe.core.shell import Aws, SigninExpired

ROOT = "reports"
TAGS = {"ManagedBy": "reframe", "ReframeStore": "reports"}  # set on the bucket by the reports root's default tags
PREFIX = "reframe-reports-"
REPORT_MANIFEST = re.compile(r"^[^/]+/[^/]+/(reports/)?manifest\.json$")  # reports/ since S-15; at the run's top before it


def store_folder() -> RunFolder:
    return RunFolder(home() / "reports-store")


def signed_in(profile: str | None, region: str | None) -> Aws:
    from reframe.core.setup import load_config
    saved = load_config()
    profile = profile or saved.get("profile")
    if not profile:
        raise Stop(Outcome.REFUSED, "no AWS profile: pass --profile, or run reframe setup once to choose and save one")
    return Aws(profile, region or saved.get("region") or "us-east-1")


class Store:
    def __init__(self, aws: Aws, folder: RunFolder | None = None, terraform: str | None = None) -> None:
        from reframe.core.pipeline import terraform_binary
        self.aws, self.folder = aws, folder or store_folder()
        self.changes = Changes(self.folder, f"{getpass.getuser()} (reframe)")
        self.binary = terraform_binary(terraform)
        self._found: str | None = None  # the bucket found by its tags, once looked up

    def terraform(self):
        from reframe.infrastructure.terraform_runner import Terraform
        return Terraform(self.folder, self.aws, self.binary)

    @property
    def bucket(self) -> str | None:
        receipt = self.changes.receipt("terraform-reports-apply")
        return (receipt or {}).get("outputs", {}).get("bucket")

    def reading_bucket(self) -> str:
        """The bucket to read reports from (S-09): this computer's own, from its records; on any other computer, the one
        found by its Reframe tags with the reader's own AWS sign-in. Finding by tag is only for reading; teardown goes
        by the records alone."""
        if self.bucket:
            return self.bucket
        if self._found:
            return self._found
        found = self.discover()
        if not found:
            raise Stop(Outcome.REFUSED, f"there is no Reframe reports bucket in {self.aws.region} that your AWS sign-in can see; publish one "
                                        "with reframe publish --to aws, or pass the region it is in with --region")
        if len(found) > 1:
            raise Stop(Outcome.REFUSED, f"there is more than one Reframe reports bucket in {self.aws.region} ({', '.join(found)}), so Reframe "
                                        "will not guess which to read")
        self._found = found[0]
        return self._found

    def discover(self) -> list[str]:
        """The Reframe reports buckets in this region: by the tag index, or, without permission for it, by listing the buckets
        named reframe-reports-… and reading their tags. The tag index can list a bucket for a while after it is deleted, so
        each one is checked to exist."""
        tags = [f"Key={k},Values={v}" for k, v in TAGS.items()]
        try:
            arns = self.aws.read("resourcegroupstaggingapi", "get-resources", "--resource-type-filters", "s3", "--tag-filters", *tags,
                                 "--query", "ResourceTagMappingList[].ResourceARN")
            names = [str(arn).rsplit(":", 1)[-1] for arn in arns or []]
        except SigninExpired:
            raise
        except NotSent:
            try:
                listed = self.aws.read("s3api", "list-buckets", "--query", f"Buckets[?starts_with(Name, '{PREFIX}')].Name")
            except SigninExpired:
                raise
            except NotSent:
                raise Stop(Outcome.REFUSED, "your AWS sign-in can neither search the account's tags (tag:GetResources) nor list its buckets "
                                            "(s3:ListAllMyBuckets), so Reframe cannot find the reports bucket; ask for one of these, with "
                                            "s3:ListBucket and s3:GetObject on the bucket") from None
            names = [n for n in listed or [] if self._tagged(n)]
        return sorted({n for n in names if n.startswith(PREFIX) and self._exists(n)})

    def _tagged(self, name: str) -> bool:
        try:
            tags = self.aws.read("s3api", "get-bucket-tagging", "--bucket", name, "--query", "TagSet")
        except SigninExpired:
            raise
        except NotSent:
            return False
        return TAGS.items() <= {t.get("Key"): t.get("Value") for t in tags or []}.items()

    def _exists(self, name: str) -> bool:
        try:
            self.aws.read("s3api", "head-bucket", "--bucket", name)
            return True
        except SigninExpired:
            raise
        except NotSent:
            return False

    def ensure(self) -> str:
        """The bucket, created the first time (a plan checked to only create)."""
        if self.bucket:
            return self.bucket
        state = self.folder.state
        name = state.get("bucket_name") or "reframe-reports-" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        self.folder.update(bucket_name=name, region=self.aws.region, profile=self.aws.profile, created_at=state.get("created_at") or iso())
        tf = self.terraform()
        tf.install()
        tf.variables(ROOT, {"region": self.aws.region, "bucket_name": name})
        tf.apply(ROOT, self.changes)
        bucket = self.bucket
        if not bucket:
            raise Stop(Outcome.FAILED, "the reports store's Terraform root did not report its bucket")
        self.folder.event("reports_store_ready")
        return bucket

    def upload(self, copy: Path, project: str, run: str, folder: RunFolder | None = None) -> str:
        """The cleaned copy to <project>/<run>/reports/, and, given the run folder, its source and the tools' results."""
        bucket = self.ensure()
        prefix = f"{project}/{run}/"
        digest = hashlib.sha256((copy / "manifest.json").read_bytes()).hexdigest()[:16]
        pieces = run_pieces(folder) if folder else []

        def cp(source: Path, key: str, *more: str) -> None:
            self.aws.write("s3", "cp", str(source), f"s3://{bucket}/{key}", *more, "--sse", "AES256", "--only-show-errors", timeout=1800)

        def send() -> dict[str, Any]:
            sent = {}
            with tempfile.TemporaryDirectory() as scratch:
                for key, source in pieces:
                    cp(source, prefix + key, *(("--recursive",) if source.is_dir() else ()))
                    assert folder is not None  # pieces come from the run folder
                    sent[key] = source.resolve().relative_to(folder.root.resolve()).as_posix()
                if folder and folder.exists("snapshot.json"):
                    note = Path(scratch) / "source.json"
                    note.write_text(json.dumps(source_note(folder.load("snapshot.json")), indent=2))
                    cp(note, prefix + "source/source.json")
                archive = zipped(copy, Path(scratch) / "report.zip", f"{project}-{run}")
                cp(copy, prefix + "reports/", "--recursive")
                cp(archive, prefix + "reports/report.zip")
            return {"zip": f"{prefix}reports/report.zip", "sent": sent}

        self.sent = sorted({k.split("/")[0] + "/" for k, _ in pieces} | {"reports/"})
        self.changes.apply(f"publish-{project}-{run}-{digest}", "s3 cp: source, results, reports and report.zip",
                           {"bucket": bucket, "prefix": prefix, "manifest": digest, "pieces": [k for k, _ in pieces]}, send)
        self.folder.event("report_uploaded", project=project, run=run)
        return prefix

    def reports(self) -> list[dict[str, Any]]:
        bucket = self.reading_bucket()
        keys = self.aws.read("s3api", "list-objects-v2", "--bucket", bucket, "--query", "Contents[?ends_with(Key, 'manifest.json')].[Key,LastModified]")
        found = []
        # a run published in both layouts is listed once, from reports/ (S-15), which download reads first
        keys = sorted(keys or [], key=lambda k: "/reports/" not in k[0])
        seen = set()
        for key, modified in keys:
            if not REPORT_MANIFEST.match(key) or "/".join(key.split("/")[:2]) in seen:  # the tools' results hold manifests of their own
                continue
            seen.add("/".join(key.split("/")[:2]))
            with tempfile.TemporaryDirectory() as scratch:
                target = Path(scratch) / "manifest.json"
                self.aws.read("s3api", "get-object", "--bucket", bucket, "--key", key, str(target))
                manifest = json.loads(target.read_text())
            found.append({"path": "/".join(key.split("/")[:2]), "project": manifest.get("project"), "run": manifest.get("run"),
                          "commit": manifest.get("commit"), "published_at": manifest.get("published_at") or modified})
        return sorted(found, key=lambda r: str(r["published_at"]), reverse=True)

    def resolve(self, name: str, run: str | None = None) -> str:
        """The published report a reader names (S-10): <project>/<run>, or a project with --run, or a project alone for its
        latest published run."""
        name = name.strip("/")
        if "/" in name:
            if run:
                raise Stop(Outcome.REFUSED, f"name the run once: {name}, or {name.split('/')[0]} --run {run}")
            return name
        if run:
            return f"{name}/{run}"
        found = self.reports()
        mine = [r for r in found if r["path"].split("/")[0] == name]
        if not mine:
            projects = sorted({r["path"].split("/")[0] for r in found})
            raise Stop(Outcome.REFUSED, f"nothing is published for project {name}" + (f"; published projects: {', '.join(projects)}" if projects else ""))
        return mine[0]["path"]  # reports() is newest first

    def download(self, path: str, target: Path | None = None) -> Path:
        """Download a published report, into the cache or a folder the reader chose, and return its index page."""
        bucket = self.reading_bucket()
        target = target or self.folder.path(f"cache/{path.strip('/')}")
        target.mkdir(parents=True, exist_ok=True)
        path = path.strip("/")
        self.aws.read("s3", "sync", f"s3://{bucket}/{path}/reports/", str(target), "--exclude", "report.zip", "--only-show-errors", timeout=1800)
        if not (target / "index.html").exists():  # published before S-15, with the pages at <project>/<run>/
            self.aws.read("s3", "sync", f"s3://{bucket}/{path}/", str(target), "--exclude", "report.zip", "--exclude", "source/*",
                          "--exclude", "results/*", "--exclude", "reports/*", "--only-show-errors", timeout=1800)
        if not (target / "index.html").exists():
            raise Stop(Outcome.REFUSED, f"{path} is not a published report in the bucket; reframe reports lists them")
        return target / "index.html"

    # ---- teardown ------------------------------------------------------------------------------------

    def inventory(self) -> list[dict[str, Any]]:
        if not self.folder.exists(f"terraform/{ROOT}/terraform.tfstate"):
            return []
        return self.terraform().inventory(ROOT)

    def teardown(self, prompt: Callable[[str], str], say: Callable[[str], None]) -> bool:
        items = self.inventory()
        if not items:
            say("There is no reports bucket to remove.\n")
            return False
        bucket = self.bucket
        count = 0
        if bucket:
            listed = self.aws.read("s3api", "list-object-versions", "--bucket", bucket, "--query", "length(Versions || `[]`)")
            count = int(listed or 0)
        say(f"Reframe will delete the reports bucket {bucket} with its {count} stored object versions (every published run's source, "
            "results and reports), and these settings, all created by Reframe for the published runs:\n\n" + "".join(f"  {n:>2}. {i['type']}  {i.get('name') or i['address']}\n" for n, i in enumerate(items, 1)))
        answer = prompt("Type yes to delete the bucket and everything published in it, or anything else to keep it: ").strip()
        if answer != "yes":
            say("Nothing was deleted.\n")
            return False
        consents = self.folder.load("teardown/consents.json", [])
        consents.append({"at": iso(), "by": getpass.getuser(), "answer": answer, "resources": items, "objects": count})
        self.folder.save("teardown/consents.json", consents)
        deleted = self.terraform().destroy(ROOT, self.changes)["deleted"]
        say(f"Deleted {len(deleted)} resources, the bucket with its contents among them.\n")
        stamp = iso().replace(":", "")
        for step in ("terraform-reports-apply", "terraform-reports-destroy"):  # archived, so a later publish creates a new bucket
            for kind in ("intent", "receipt"):
                if self.folder.exists(f"changes/{step}.{kind}.json"):
                    self.folder.rename(f"changes/{step}.{kind}.json", f"changes/{step}.{kind}-done-{stamp}.json")
        self.folder.update(torn_down=iso(), bucket_name=None)
        return True


def run_pieces(folder: RunFolder) -> list[tuple[str, Path]]:
    """What a run adds beside its reports (S-15): the snapshot, and each tool's results as Reframe retrieved them. These are
    the run's own files, not cleaned: the bucket is private to the account."""
    snapshot = folder.load("snapshot.json", {}) if folder.exists("snapshot.json") else {}
    pieces = []
    archive = folder.path(snapshot.get("archive") or "source/archive.zip")
    if archive.is_file():
        pieces.append((f"source/{str(snapshot.get('sha256') or 'source')[:16]}.zip", archive))
    for key, name in (("results/aws/", "outputs/aws"), ("results/claude/", "claude")):
        if folder.path(name).is_dir() and any(f.is_file() for f in folder.path(name).rglob("*")):
            pieces.append((key, folder.path(name)))
    return pieces


def source_note(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the source is, without the owner's local path: a GitHub URL is kept, a folder or file on their computer is not."""
    origin = str(snapshot.get("source") or "")
    return {"sha256": snapshot.get("sha256"), "form": snapshot.get("form"), "files": snapshot.get("files"), "bytes": snapshot.get("bytes"),
            "commit": snapshot.get("commit"), "ref": snapshot.get("ref"),
            "source": origin if origin.startswith(("https://", "http://", "git@")) else Path(origin).name or None}


def zipped(copy: Path, archive: Path, folder: str) -> Path:
    """The published copy as one file (S-11), for readers who download it from the S3 console, which downloads files but
    not folders. Everything sits in one folder, <project>-<run>/, so unzipping it spills nothing."""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        for item in sorted(copy.rglob("*")):
            if item.is_file():
                out.write(item, f"{folder}/{item.relative_to(copy).as_posix()}")
    return archive


def open_in_browser(path: Path) -> None:
    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)], check=False)
