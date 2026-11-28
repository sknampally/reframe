"""Stage C7, end to end: decide, create the graph loader, upload, load, check (docs/pipeline-2.md).

Every AWS change goes through the run's intent and receipt records: the loader's Terraform apply (its own receipt,
apart from pipeline 1's), the upload of graph.nq to the run's bucket, and the bulk load.
"""

from __future__ import annotations

import hashlib
from typing import Any

from reframe.claude import graph as rdf
from reframe.claude import plugin
from reframe.core.errors import Outcome, Stop
from reframe.core.pipeline import Context
from reframe.core.runfolder import iso

KEY = "reframe/claude-graph/{run}.nq"


def should_load(ctx: Context) -> tuple[bool, str]:
    from reframe.claude.pipeline import state
    settings = state(ctx)
    if ctx.state.get("tools") == "claude":
        return False, "a Claude-only run has no Neptune graph; Claude's graph is kept as a file, outputs/claude/graph.nq"
    kind = (ctx.state.get("graph") or {}).get("kind", "new")
    choice = settings.get("graph_load")
    if kind == "new":
        if choice == "no":
            return False, "the person chose not to load Claude's graph"
        return True, "the run created this graph"
    if choice == "yes":
        return True, "the person said yes to loading Claude's graph into the existing cluster"
    if settings.get("mode") == "interactive" and choice is None:
        from reframe.claude.pipeline import ask
        reply = ask(ctx)("claude-graph-load", f"Load Claude's graph into the existing Neptune cluster {(ctx.state.get('graph') or {}).get('name')}? "
                         "It goes into its own named graph, which reframe teardown drops again; AWS Transform's graph is not touched.",
                         ["Yes, load it", "No, skip the load"])
        return reply.startswith("Yes"), "the person answered: " + reply
    return False, "an existing cluster is loaded only on the person's yes (--graph-load yes); skipped"


def loader(ctx: Context, create: bool) -> rdf.Loader | None:
    """The run's graph loader: created by the project Terraform root (its own receipt) when create is true."""
    tf = ctx.terraform()
    step = "terraform-project-apply-graph-loader"
    if ctx.changes.receipt(step) is None:
        if not create:
            return None
        zip_path = rdf.package(ctx.folder.path("terraform/graph-loader.zip"))
        tf.install(refresh=True)
        values = ctx.folder.load("terraform/project/reframe.auto.tfvars.json") or {}
        tf.variables("project", {**values, "graph_loader": True, "graph_loader_zip": str(zip_path)})
    receipt = tf.apply("project", ctx.changes, ctx.backend(), step=step)
    outputs = receipt["outputs"]
    function = outputs.get("graph_loader")
    if not function:
        raise Stop(Outcome.FAILED, "the project root did not report the graph loader function")
    cluster = outputs["reimagine_connector"]["configuration"]["neptuneClusterArn"]
    return rdf.Loader(ctx.aws, function, cluster, ctx.folder.path("claude-logs"))


def graph(ctx: Context, model: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    from reframe.claude.pipeline import state, update
    done = state(ctx).get("graph")
    if done and done.get("loaded_at") or done and done.get("skipped"):
        return done
    yes, reason = should_load(ctx)
    ctx.folder.decide("claude-graph-load", "load" if yes else "skip", reason, step="claude graph")
    run = ctx.folder.root.name
    if not yes and ctx.state.get("tools") != "claude":
        return update(ctx, graph={"skipped": iso(), "reason": reason})["graph"]
    sha = state(ctx).get("sha256") or (ctx.folder.load("snapshot.json") or {}).get("sha256", "")
    quads = rdf.build(run, sha, model, generated, {"version": plugin.VERSION, "commit": plugin.COMMIT})
    path = ctx.folder.path("outputs/claude/graph.nq")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(quads.text())
    if not yes:
        return update(ctx, graph={"skipped": iso(), "reason": reason, "file": "outputs/claude/graph.nq"})["graph"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    fn = loader(ctx, create=True)
    assert fn is not None  # with create, loader returns the loader or stops
    outputs = ctx.changes.receipt("terraform-project-apply-graph-loader")["outputs"]
    bucket, key = outputs["bucket"], KEY.format(run=run)

    def upload():
        return ctx.aws.write("s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(path), "--checksum-algorithm", "SHA256")
    ctx.changes.apply("claude-graph-upload", "s3api put-object", {"bucket": bucket, "key": key, "sha256": digest}, upload)
    role = outputs["reimagine_connector"]["configuration"]["neptuneBulkUploadRoleArn"]

    def settle():
        found = rdf.counts(fn, run)
        return {"settled_from": "the named graph already holds Claude's resources", "counts": found} if found else None
    loaded = ctx.changes.apply("claude-graph-load", "neptune bulk load", {"source": f"s3://{bucket}/{key}", "graph": rdf.graph_iri(run)},
                               lambda: fn.load(f"s3://{bucket}/{key}", role), settle)
    counts = rdf.counts(fn, run)
    joined = rdf.joined(fn, run)
    value = {"loaded_at": iso(), "graph": rdf.graph_iri(run), "quads": len(quads.lines), "sha256": digest, "load": loaded,
             "counts": counts, "rules_joined_to_aws_programs": joined, "cluster": (ctx.state.get("graph") or {}).get("kind", "new")}
    ctx.folder.event("claude_graph_loaded", quads=len(quads.lines), joined=joined)
    return update(ctx, graph=value)["graph"]


def drop_for_teardown(ctx: Context) -> str | None:
    """On an existing cluster, drop Claude's named graph before the loader is removed. Returns what was done."""
    loaded = (ctx.state.get("claude") or {}).get("graph") or {}
    if not loaded.get("loaded_at") or (ctx.state.get("graph") or {}).get("kind") != "existing":
        return None
    fn = loader(ctx, create=False)
    if fn is None:
        return None
    ctx.changes.apply("teardown-drop-claude-graph", "SPARQL DROP GRAPH", {"graph": loaded["graph"]}, lambda: rdf.drop(fn, ctx.folder.root.name))
    return f"dropped {loaded['graph']}"
