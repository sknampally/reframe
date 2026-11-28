"""S3 through the AWS CLI with the owner's sign-in: the source upload (stage 3) and the outputs (stage 7).

Every call names the expected bucket owner, so a bucket of the same name in another account is never read
or written. The upload is checked with S3's own SHA-256 checksum.
"""

from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reframe.core.errors import NotSent, Outcome, Stop
from reframe.core.runfolder import Changes, RunFolder
from reframe.core.shell import Aws


@dataclass(frozen=True)
class Bucket:
    name: str
    owner: str


def head(aws: Aws, bucket: Bucket, key: str) -> dict[str, Any] | None:
    try:
        return aws.read("s3api", "head-object", "--bucket", bucket.name, "--key", key, "--expected-bucket-owner", bucket.owner,
                        "--checksum-mode", "ENABLED")
    except NotSent as error:
        if "(404)" in str(error) or "Not Found" in str(error) or "NoSuchKey" in str(error):
            return None
        raise


def _b64(digest_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(digest_hex)).decode()


def upload(aws: Aws, folder: RunFolder, changes: Changes, bucket: Bucket, data: bytes, digest_hex: str) -> dict[str, Any]:
    """Stage 3: the snapshot, written once to source/<sha>.zip, and checked."""
    key = f"source/{digest_hex[:16]}.zip"

    def matches(found: dict[str, Any] | None) -> bool:
        if not found:
            return False
        return (found.get("ChecksumSHA256") == _b64(digest_hex)
                or (found.get("Metadata") or {}).get("reframe-sha256") == digest_hex)

    def settle():
        found = head(aws, bucket, key)
        if found is None:
            return None
        if not matches(found):
            raise Stop(Outcome.REFUSED, "an object with different content is already at the source key; nothing was overwritten")
        return {"key": key, "version_id": found.get("VersionId"), "settled_from": "head-object"}

    def send():
        with tempfile.NamedTemporaryFile(prefix="reframe-source-", suffix=".zip", delete=False) as body:
            body.write(data)
        try:
            reply = aws.write("s3api", "put-object", "--bucket", bucket.name, "--key", key, "--body", body.name,
                              "--expected-bucket-owner", bucket.owner, "--checksum-sha256", _b64(digest_hex),
                              "--metadata", f"reframe-sha256={digest_hex}")
        finally:
            os.unlink(body.name)
        return {"key": key, "version_id": reply.get("VersionId"), "checksum": reply.get("ChecksumSHA256")}

    receipt = changes.apply("source-upload", "s3 put-object", {"bucket": bucket.name, "key": key, "bytes": len(data)}, send, settle)
    found = head(aws, bucket, key)
    if not matches(found):
        raise Stop(Outcome.FAILED, "the uploaded source does not match the snapshot's SHA-256")
    record = {"uri": f"s3://{bucket.name}/{key}", "key": key, "sha256": digest_hex, "version_id": receipt.get("version_id")}
    folder.update(upload=record)
    folder.event("uploaded", key=key, bytes=len(data))
    return record


def list_keys(aws: Aws, bucket: Bucket, prefix: str) -> list[dict[str, Any]]:
    items, token = [], None
    while True:
        arguments = ["s3api", "list-objects-v2", "--bucket", bucket.name, "--prefix", prefix, "--expected-bucket-owner", bucket.owner]
        if token:
            arguments += ["--continuation-token", token]
        page = aws.read(*arguments)
        items += [{"key": o["Key"], "bytes": int(o.get("Size", 0))} for o in page.get("Contents") or []]
        token = page.get("NextContinuationToken")
        if not token:
            return items


def download(aws: Aws, bucket: Bucket, key: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    aws.read("s3api", "get-object", "--bucket", bucket.name, "--key", key, "--expected-bucket-owner", bucket.owner, str(target),
             timeout=1800)
    target.chmod(0o600)
