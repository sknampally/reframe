"""Pipeline 1, stage by stage (specification section 4). Every stage can be run again: what has a receipt is skipped.

`reframe run` does stages 0 and the Neptune choice in the foreground (they may ask the person), records
them, and starts the worker, which runs stages 1 to 8. `reframe resume` starts the worker again.
"""

from __future__ import annotations

import getpass
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable

from reframe.core.errors import Outcome, Stop
from reframe.core.runfolder import Changes, RunFolder, iso
from reframe.core.shell import Aws

STAGES = ("preflight", "snapshot", "infrastructure", "upload", "transform", "job", "retrieve", "report", "done")


def terraform_binary(explicit: str | None) -> str:
    from reframe.core.setup import saved_terraform
    return explicit or os.environ.get("REFRAME_TERRAFORM") or saved_terraform() or shutil.which("terraform") or "terraform"


@dataclass
class Context:
    folder: RunFolder
    aws: Aws
    changes: Changes

    @property
    def state(self) -> dict[str, Any]:
        return self.folder.state

    @classmethod
    def load(cls, folder: RunFolder) -> "Context":
        state = folder.state
        # a Claude-only run has no profile; its Aws is never called (docs/release-1.md, section 2)
        return cls(folder, Aws(state.get("profile") or "", state.get("region") or "us-east-1"), Changes(folder, f"{getpass.getuser()} (reframe)"))

    # Built when first needed, so commands that only read the run folder do not need AWS tools.
    def terraform(self):
        from reframe.infrastructure.terraform_runner import Terraform
        return Terraform(self.folder, self.aws, self.state["terraform"])

    def client(self):
        from reframe.transform.client import ServerConfig, TransformClient
        return TransformClient(ServerConfig(self.state["profile"], self.state["region"], self.folder.path("server-write-dir")))

    def api(self):
        from reframe.transform.api import TransformApi
        from reframe.transform.client import ServerConfig
        return TransformApi(ServerConfig(self.state["profile"], self.state["region"], self.folder.path("server-write-dir")))

    def names(self) -> dict[str, str]:
        from reframe.infrastructure.terraform_runner import names
        return names(self.state["project"], self.folder.root.name)

    def backend(self) -> dict[str, str]:
        state = self.state
        return {"bucket": self.names()["state_bucket"], "key": f"projects/{state['project']}/{self.folder.root.name}/terraform.tfstate",
                "region": state["region"]}

    def stage(self, name: str) -> None:
        self.folder.update(stage=name, status="RUNNING", message=None, updated_at=iso())
        self.folder.event("stage", stage=name)


# ---- the foreground part of reframe run ----------------------------------------------------------


def prepare(ctx: Context, prompt: Callable[[str], str] | None, say: Callable[[str], None]) -> None:
    """Stage 0 and the Neptune choice, before the worker starts."""
    from reframe.core import preflight
    from reframe.infrastructure import neptune

    state = ctx.state
    ctx.stage("preflight")
    client = ctx.client()
    report = preflight.check(aws=ctx.aws, terraform=state["terraform"], source=state["source"], ref=state.get("ref"),
                             transform_ready=lambda: client.call("list_resources", {"resource": "workspaces"}))
    ctx.folder.save("preflight.json", report.as_json())
    preflight.require(report)
    assert report.account  # preflight.require refuses a sign-in that names no account
    ctx.folder.update(account=report.account, terraform_version=report.terraform)
    if ctx.state.get("graph") is None:
        names = ctx.names()
        found = neptune.candidates(neptune.Reader(ctx.aws), names["bucket"], report.account) if state.get("graph_arg") != "new" else []
        if found or state.get("graph_arg") not in (None, "new"):
            ctx.folder.save("graph-candidates.json", [{"name": c.name, "usable": c.usable, "problems": list(c.problems)} for c in found])
        chosen = neptune.choose(found, state.get("graph_arg"), state["mode"], prompt, say)
        ctx.folder.update(graph={"kind": "existing" if chosen else "new", "name": chosen.name if chosen else None,
                                 "variables": neptune.terraform_variables(chosen)})
        ctx.folder.event("graph_chosen", kind="existing" if chosen else "new")


# ---- the worker ----------------------------------------------------------------------------------


def work(ctx: Context, on_resources: Callable[[], Any] | None = None) -> Outcome:
    """Stages 1 to 8. Returns DONE, or raises Stop with the outcome to record. on_resources runs once the run's AWS
    resources exist (pipeline 2 starts there, alongside this pipeline)."""
    from reframe.source import s3, snapshot

    state = ctx.state
    names = ctx.names()
    ctx.stage("snapshot")
    record = snapshot.take(ctx.folder, state["source"], state.get("ref"))

    ctx.stage("infrastructure")
    tf = ctx.terraform()
    tf.install()
    run = ctx.folder.root.name
    tf.variables("bootstrap", {"region": state["region"], "project": state["project"], "run": run, "state_bucket": names["state_bucket"]})
    tf.apply("bootstrap", ctx.changes)
    tf.variables("project", {"region": state["region"], "project": state["project"], "run": run, **(state["graph"]["variables"])})
    outputs = tf.apply("project", ctx.changes, ctx.backend())["outputs"]
    bucket = s3.Bucket(outputs["bucket"], state["account"])
    if on_resources is not None:
        on_resources()

    ctx.stage("upload")
    upload = s3.upload(ctx.aws, ctx.folder, ctx.changes, bucket, snapshot.archive_bytes(ctx.folder, record), record["sha256"])

    ctx.stage("transform")
    from reframe.transform import workspace
    client = ctx.client()
    ids = workspace.set_up(client, ctx.folder, ctx.changes, workspace_name=names["workspace"], connector_name=names["connector"],
                           connector=outputs["reimagine_connector"], account=state["account"], project=state["project"])

    ctx.stage("job")
    from reframe.transform.driver import Driver
    if not ctx.state.get("job_done"):
        Driver(client, ctx.api(), ctx.folder, ctx.changes, workspace_id=ids["workspace_id"], job_name=names["prefix"],
               source_uri=upload["uri"], mode=state["mode"]).run()
        ctx.folder.update(job_done=iso())

    ctx.stage("retrieve")
    from reframe.transform import retrieve
    if not ctx.state.get("retrieved_at"):
        retrieve.retrieve(ctx.aws, ctx.folder, bucket, ctx.folder.load("job.json")["job_id"])
        ctx.folder.update(retrieved_at=iso())

    ctx.stage("report")
    from reframe.report import build
    path = build.build(ctx.folder)
    ctx.folder.update(stage="done", status=Outcome.DONE.value, message=f"Results are ready: {path}. "
                      "The AWS resources are kept until you run reframe teardown.", finished_at=iso())
    ctx.folder.event("done")
    return Outcome.DONE


def record_stop(ctx: Context, stop: Stop) -> None:
    ctx.folder.update(status=stop.outcome.value, message=stop.message, updated_at=iso())
    ctx.folder.event("stopped", outcome=stop.outcome.value, message=stop.message[:500])
