"""Stage 9: teardown on request, with the full list and recorded consent (specification section 4, stage 9).

1. The inventory comes from the run's own records only: its receipts and Terraform state, plus any detached
   AWS Transform network interface in the run's own VPC. An existing Neptune cluster is never in it.
2. Every resource is listed at once and the person types yes.
3. The consent (who, when, the list and its hash) is recorded before anything is deleted. If the list
   changes before deletion starts, consent is asked again.
4. Deletion runs in order: job, workspace, connector check; for pipeline 2, Claude's named graph on an existing cluster
   and then the graph loader, waiting until AWS releases the loader's network interfaces (docs/pipeline-2.md); network
   interfaces AWS Transform left; project root; bootstrap root.
5. A read-only check reports anything tagged with the run that still exists.
"""

from __future__ import annotations

import getpass
import hashlib
import json
from typing import Any, Callable

from reframe.core.errors import NotSent, Outcome, Stop
from reframe.core.pipeline import Context
from reframe.core.runfolder import iso

LABELS = {"aws_s3_bucket": "S3 bucket", "aws_iam_role": "IAM role", "aws_iam_role_policy": "IAM role policy",
          "aws_neptune_cluster": "Neptune cluster", "aws_neptune_cluster_instance": "Neptune instance",
          "aws_neptune_subnet_group": "Neptune subnet group", "aws_vpc": "VPC", "aws_subnet": "Subnet",
          "aws_security_group": "Security group", "aws_vpc_security_group_ingress_rule": "Security group rule (in)",
          "aws_vpc_security_group_egress_rule": "Security group rule (out)", "aws_vpc_endpoint": "VPC endpoint (S3)",
          "aws_route_table": "Route table", "aws_route_table_association": "Route table association",
          "aws_s3_bucket_policy": "S3 bucket policy", "aws_s3_bucket_versioning": "S3 bucket versioning",
          "aws_s3_bucket_ownership_controls": "S3 bucket ownership setting",
          "aws_s3_bucket_public_access_block": "S3 bucket public access block",
          "aws_s3_bucket_server_side_encryption_configuration": "S3 bucket encryption setting",
          "aws_lambda_function": "Lambda function (Claude's graph loader)", "aws_cloudwatch_log_group": "Log group (graph loader)"}


def _name(resource: dict[str, Any]) -> str:
    """The resource's own name, or for an unnamed one (a rule, an association) the last part of its address."""
    return resource.get("name") or resource["address"].rsplit(".", 1)[-1]


def transform_ids(ctx: Context) -> dict[str, Any]:
    """The run's AWS Transform IDs from run.json, with the job ID also taken from job.json, the driver's record of
    its create_job receipt, so a job is never missed from teardown."""
    ids = dict(ctx.state.get("transform") or {})
    job = ctx.folder.load("job.json") if ctx.folder.exists("job.json") else {}
    if not ids.get("job_id") and job.get("job_id"):
        ids["job_id"] = job["job_id"]
    return ids


def inventory(ctx: Context) -> list[dict[str, Any]]:
    from reframe.infrastructure import network
    items: list[dict[str, Any]] = []
    transform = transform_ids(ctx)
    names = ctx.names()
    done = lambda step: ctx.changes.receipt(step) is not None  # noqa: E731
    if transform.get("job_id") and not done("teardown-delete-job"):
        items.append({"kind": "AWS Transform job", "name": names["prefix"], "id": transform["job_id"], "from": "create_job receipt"})
    if transform.get("workspace_id") and not done("teardown-delete-workspace"):
        items.append({"kind": "AWS Transform workspace", "name": names["workspace"], "id": transform["workspace_id"], "from": "create_workspace receipt"})
    if transform.get("connector_id") and not done("teardown-delete-connector"):
        items.append({"kind": "AWS Transform connector", "name": names["connector"], "id": transform["connector_id"], "from": "create_connector receipt"})
    tf = ctx.terraform()
    project, bootstrap = [], []
    if ctx.changes.receipt("terraform-project-apply") is not None or ctx.folder.exists("changes/terraform-project-apply.intent.json"):
        project = tf.inventory("project", ctx.backend())
    if ctx.folder.exists("terraform/bootstrap/terraform.tfstate"):
        bootstrap = tf.inventory("bootstrap")
    vpc = tf.vpc_id(project)
    if vpc:
        for interface in network.transform_interfaces(ctx.aws, vpc):
            items.append({"kind": "Network interface left by AWS Transform", "name": interface["description"], "id": interface["id"],
                          "from": "the run's own VPC"})
    for resource in project + bootstrap:
        items.append({"kind": LABELS.get(resource["type"], resource["type"]), "name": _name(resource),
                      "id": resource.get("id"), "from": f"Terraform {resource['root']} state ({resource['address']})"})
    return items


def digest(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def describe(items: list[dict[str, Any]], graph: dict[str, Any] | None, claude_graph: str | None = None) -> str:
    lines = [f"Reframe will delete these {len(items)} AWS resources, all created by this run:", ""]
    width = max((len(i["kind"]) for i in items), default=10)
    for number, item in enumerate(items, 1):
        lines.append(f"  {number:>3}. {item['kind']:<{width}}  {item['name']}" + (f"  ({item['id']})" if item.get("id") and item["id"] != item["name"] else ""))
    if any(i["kind"] == "VPC" for i in items):
        lines += ["", "AWS Transform can leave a network interface in this run's VPC while the job is deleted. Reframe also deletes",
                  "any such interface it finds in that VPC (described as managed by AWS Transform), so the VPC can be deleted."]
    if any(i["kind"].startswith("Lambda function") for i in items):
        lines += ["", "Claude's graph loader is deleted first. AWS takes a while to release its network interfaces, and",
                  "Reframe waits for that before it deletes the network."]
    if graph and graph.get("kind") == "existing":
        lines += ["", f"The existing Neptune cluster {graph['name']} is not in the list: Reframe never changes or deletes it."]
    if claude_graph:
        lines += [f"Claude's named graph ({claude_graph}) is dropped from that cluster; AWS Transform's graph is not touched."]
    return "\n".join(lines) + "\n"


def ask(ctx: Context, prompt: Callable[[str], str], say: Callable[[str], None]) -> list[dict[str, Any]] | None:
    items = inventory(ctx)
    ctx.folder.save("teardown/inventory.json", items)
    if not items:
        say("This run has no AWS resources left to delete.\n")
        return None
    loaded = ((ctx.state.get("claude") or {}).get("graph") or {})
    existing = (ctx.state.get("graph") or {}).get("kind") == "existing"
    say(describe(items, ctx.state.get("graph"), loaded.get("graph") if existing and loaded.get("loaded_at") else None))
    try:
        answer = prompt(f"Type yes to delete all {len(items)} resources, or anything else to keep them: ").strip()
    except EOFError:
        answer = ""
    if answer != "yes":
        ctx.folder.event("teardown_declined")
        say("Nothing was deleted.\n")
        return None
    consent = {"at": iso(), "by": getpass.getuser(), "answer": answer, "resources": items, "sha256": digest(items)}
    consents = ctx.folder.load("teardown/consents.json", [])
    consents.append(consent)
    ctx.folder.save("teardown/consents.json", consents)
    ctx.folder.event("teardown_consent", resources=len(items), sha256=consent["sha256"][:16])
    return items


def delete(ctx: Context, consented: list[dict[str, Any]], say: Callable[[str], None]) -> dict[str, Any]:
    from reframe.infrastructure import network
    from reframe.transform import teardown as transform_teardown
    if digest(inventory(ctx)) != digest(consented):
        raise Stop(Outcome.REFUSED, "the list of resources changed after consent; run reframe teardown again to see the new list")
    state, transform = ctx.state, transform_ids(ctx)
    result: dict[str, Any] = {"at": iso(), "steps": []}

    def step(name: str, outcome: str) -> None:
        say(f"  {name}: {outcome}\n")
        result["steps"].append({"step": name, "outcome": outcome})
        ctx.folder.save("teardown/result.json", result)

    client = ctx.client() if transform else None
    if transform.get("job_id") and ctx.changes.receipt("teardown-delete-job") is None:
        step("job", transform_teardown.delete_job(client, ctx.changes, transform["workspace_id"], transform["job_id"]))
    if transform.get("workspace_id") and ctx.changes.receipt("teardown-delete-workspace") is None:
        step("workspace", transform_teardown.delete_workspace(client, ctx.changes, transform["workspace_id"]))
    if transform.get("connector_id"):
        step("connector", transform_teardown.delete_connector(ctx.api(), ctx.folder, ctx.changes, transform["connector_id"], ctx.names()["connector"]))
    tf = ctx.terraform()
    from reframe.claude import load
    dropped = load.drop_for_teardown(ctx)
    if dropped:
        step("Claude's graph on the existing cluster", dropped)
    if any(i["kind"].startswith("Lambda function") for i in consented):
        project_items = tf.inventory("project", ctx.backend())
        function = next((i["name"] for i in project_items if i["type"] == "aws_lambda_function"), None)
        deleted = tf.destroy("project", ctx.changes, ctx.backend(), targets=["module.graph_loader"], step="terraform-project-destroy-graph-loader")["deleted"]
        step("Claude's graph loader (Terraform)", f"destroyed {len(deleted)}")
        vpc = tf.vpc_id(project_items)
        if vpc and function:
            waited = network.wait_for_lambda_interfaces(ctx.aws, vpc, function)
            step("graph loader's network interfaces", f"released after {waited // 60} min {waited % 60} s")
    if any(i["from"].startswith("Terraform project") for i in consented):
        vpc = tf.vpc_id(tf.inventory("project", ctx.backend()))
        for attempt in range(1, 4):
            if vpc:
                removed = network.delete_interfaces(ctx.aws, ctx.changes, vpc)
                if removed:
                    step("network interfaces left by AWS Transform", f"deleted {len(removed)}")
            try:
                deleted = tf.destroy("project", ctx.changes, ctx.backend())["deleted"]
                step("project resources (Terraform)", f"destroyed {len(deleted)}")
                break
            except Stop as stop:
                if stop.outcome is not Outcome.UNCERTAIN or attempt == 3:
                    raise
                step("project resources (Terraform)", f"stopped part way (attempt {attempt}); planning again for what is left")
    if any(i["from"].startswith("Terraform bootstrap") for i in consented):
        deleted = tf.destroy("bootstrap", ctx.changes)["deleted"]
        step("state bucket (Terraform)", f"destroyed {len(deleted)}")
    left: dict[str, Any]
    try:
        left = network.leftovers(ctx.aws, ctx.folder.root.name, state["project"])
    except NotSent as error:
        left = {"exists": [], "stale_index": [], "unchecked": [], "error": str(error)[:300]}
    result["leftovers"] = left
    ctx.folder.save("teardown/result.json", result)
    ctx.folder.update(torn_down=iso() if not left["exists"] else None, stage="torn down" if not left["exists"] else "teardown")
    ctx.folder.event("teardown_done", exists=len(left["exists"]), stale=len(left["stale_index"]), unchecked=len(left["unchecked"]))
    return result
