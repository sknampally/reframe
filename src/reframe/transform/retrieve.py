"""Stage 7: retrieval (specification section 4, stage 7).

AWS writes the job's outputs to the source bucket under transform-output/<job>/ (spike 1), not as job
artifacts. Reframe downloads the requirements archive (spec_gen/*.zip, the latest), the assessment
(results/) and the business function catalog (business-functions/delivery.zip); it skips the graph-database
files and AWS's internal state. Every archive is checked before it is unpacked, and a manifest (file, size,
SHA-256) and a summary are written.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from reframe.core.errors import Outcome, Stop
from reframe.core.runfolder import RunFolder
from reframe.core.shell import Aws
from reframe.source.s3 import Bucket, download, list_keys
from reframe.source.snapshot import check_archive
from reframe.transform.documents import count_questions
from reframe.transform.traceability import load as load_traceability

REQUIREMENT = re.compile(r"^(REQ-[A-Z]+)-\d+", re.M)


def wanted(key: str, root: str) -> bool:
    relative = key[len(root):]
    return (relative.startswith("spec_gen/") and relative.endswith(".zip")) or relative == "business-functions/delivery.zip" \
        or (relative.startswith("results/") and not relative.endswith("/"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def unpack(archive: Path, target: Path) -> int:
    check_archive(archive)
    count = 0
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            destination = (target / PurePosixPath(member.filename.replace("\\", "/"))).resolve()
            if target.resolve() not in destination.parents:
                raise Stop(Outcome.REFUSED, f"{archive.name} has a member outside its folder")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(z.read(member))
            destination.chmod(0o600)
            count += 1
    return count


def retrieve(aws: Aws, folder: RunFolder, bucket: Bucket, job_id: str) -> dict[str, Any]:
    root = f"transform-output/{job_id}/"
    keys = [item for item in list_keys(aws, bucket, root) if wanted(item["key"], root)]
    specs = sorted(k["key"] for k in keys if k["key"][len(root):].startswith("spec_gen/"))
    if not specs:
        raise Stop(Outcome.FAILED, "AWS wrote no requirements archive (spec_gen/*.zip) for this job")
    chosen = [k for k in keys if not k["key"][len(root):].startswith("spec_gen/") or k["key"] == specs[-1]]
    base = folder.path("outputs/aws")
    files = []
    for item in chosen:
        relative = item["key"][len(root):]
        target = base / relative
        if not (target.exists() and target.stat().st_size == item["bytes"]):
            download(aws, bucket, item["key"], target)
        files.append({"path": f"aws/{relative}", "bytes": target.stat().st_size, "sha256": _sha(target)})
    folder.event("downloaded", files=len(files), bytes=sum(f["bytes"] for f in files))
    spec_archive = base / specs[-1][len(root):]
    requirements = folder.path("outputs/requirements")
    unpacked = unpack(spec_archive, requirements)
    catalog_zip = base / "business-functions" / "delivery.zip"
    if catalog_zip.exists():
        unpack(catalog_zip, folder.path("outputs/catalog"))
    folder.save("outputs/manifest.json", {"job_output_prefix": "transform-output/<job>/", "files": files})
    summary = summarize(requirements)
    summary["unpacked_requirement_files"] = unpacked
    folder.save("outputs/summary.json", summary)
    folder.update(retrieved={"files": len(files), "functions": len(summary["functions"]), "requirements": summary["totals"]["requirements"]})
    folder.event("retrieved", functions=len(summary["functions"]), requirements=summary["totals"]["requirements"])
    return summary


def summarize(requirements: Path) -> dict[str, Any]:
    """Per function: its domain, requirement counts by type, open questions, and AWS's own rule counts."""
    spec = requirements / "spec" if (requirements / "spec").is_dir() else requirements
    functions: list[dict[str, Any]] = []
    for document in sorted(spec.rglob("requirements.md")):
        relative = document.parent.relative_to(spec).parts
        text = document.read_text(errors="replace")
        counts: dict[str, int] = {}
        for kind in REQUIREMENT.findall(text):
            counts[kind] = counts.get(kind, 0) + 1
        trace = document.parent / "traceability.yaml"
        rules = (load_traceability(trace).get("summary") or {}) if trace.exists() else {}
        functions.append({"domain": relative[0] if len(relative) > 1 else "", "function": relative[-1] if relative else "",
                          "path": "/".join(relative), "requirements": counts,
                          "open_questions": count_questions(text), "rules": rules})
    totals: dict[str, Any] = {"requirements": sum(sum(f["requirements"].values()) for f in functions),
              "by_type": {}, "rules": sum(int(f["rules"].get("total_rules", 0) or 0) for f in functions),
              "captured_rules": sum(int(f["rules"].get("captured", 0) or 0) for f in functions),
              "open_questions": sum(f["open_questions"] for f in functions)}
    for f in functions:
        for kind, count in f["requirements"].items():
            totals["by_type"][kind] = totals["by_type"].get(kind, 0) + count
    return {"functions": functions, "domains": sorted({f["domain"] for f in functions if f["domain"]}), "totals": totals}
