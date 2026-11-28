"""Stage C7: Claude's graph in the run's Neptune cluster (docs/pipeline-2.md; spike P2-02).

AWS Transform's graph is RDF (N-Quads, SPARQL), one named graph per job. Claude's goes in beside it as N-Quads in its
own named graph, urn:reframe:graph:claude:<run>, so it can be queried with AWS's (programs join by name) and dropped
on its own. The run's graph loader (a Lambda function in the run's app subnets, from the project Terraform root)
bulk-loads the file from the run's bucket and runs SPARQL, signed with IAM.
"""

from __future__ import annotations

import io
import json
import time
import urllib.parse
import zipfile
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from reframe.core.errors import NotSent, Outcome, Rejected, Stop, Uncertain
from reframe.core.runfolder import iso

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
ONTOLOGY = "urn:reframe:claude:ontology:"
AWS_ONTOLOGY = "http://aws.amazon.com/transformkg/ontology/"
EDGE = {"call": "calls", "read": "reads", "write": "writes", "dispatch": "dispatches"}


def graph_iri(run: str) -> str:
    return f"urn:reframe:graph:claude:{run}"


def _local(text: str) -> str:
    return urllib.parse.quote(str(text), safe="-._~")


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^<http://www.w3.org/2001/XMLSchema#boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<http://www.w3.org/2001/XMLSchema#integer>'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{text}"'


class Quads:
    def __init__(self, run: str) -> None:
        self.run, self.graph = run, graph_iri(run)
        self.lines: list[str] = []

    def iri(self, kind: str, ident: str) -> str:
        return f"urn:reframe:claude:{self.run}:{kind}:{_local(ident)}"

    def add(self, subject: str, predicate: str, obj: Any, literal: bool = True) -> None:
        if obj is None or obj == "":
            return
        o = _literal(obj) if literal else f"<{obj}>"
        p = predicate if predicate.startswith(("http", "urn:")) else ONTOLOGY + predicate
        self.lines.append(f"<{subject}> <{p}> {o} <{self.graph}> .")

    def typed(self, subject: str, cls: str) -> None:
        self.add(subject, RDF_TYPE, ONTOLOGY + cls, literal=False)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def build(run: str, sha256: str, model: dict[str, Any], generated: dict[str, Any] | None, plugin: dict[str, str]) -> Quads:
    q = Quads(run)
    g = q.graph
    q.add(g, "snapshotSha256", sha256)
    q.add(g, "pluginVersion", plugin.get("version", ""))
    q.add(g, "pluginCommit", plugin.get("commit", ""))
    q.add(g, "generatedBy", "reframe")
    q.add(g, "loadedAt", iso())
    node_iri: dict[str, str] = {}
    for domain in model.get("domains") or []:
        d = q.iri("domain", domain["id"])
        node_iri[domain["id"]] = d
        q.typed(d, "Domain")
        q.add(d, "name", domain.get("name", ""))
        q.add(d, "description", domain.get("description", ""))
    for kind, cls in (("modules", "Module"), ("jobs", "Job"), ("screens", "Screen"), ("datastores", "DataStore")):
        for item in model.get(kind) or []:
            node = q.iri(cls.lower(), item["id"])
            node_iri[item["id"]] = node
            q.typed(node, cls)
            q.add(node, "name", item.get("name", ""))
            q.add(node, "path", item.get("file", ""))
            q.add(node, "language", item.get("language", ""))
            if isinstance(item.get("loc"), int):
                q.add(node, "linesOfCode", item["loc"])
            if item.get("domain") in node_iri:
                q.add(node, "inDomain", node_iri[item["domain"]], literal=False)
    for edge in model.get("edges") or []:
        source, target = node_iri.get(edge.get("source")), node_iri.get(edge.get("target"))
        if source and target:
            q.add(source, EDGE.get(edge.get("kind"), "relatesTo"), target, literal=False)
    for ident in model.get("entry_points") or []:
        if ident in node_iri:
            q.add(node_iri[ident], "isEntryPoint", True)
    for number, flow in enumerate(model.get("flows") or [], 1):
        f = q.iri("flow", str(number))
        q.typed(f, "Flow")
        q.add(f, "name", flow.get("name", ""))
        q.add(f, "persona", flow.get("persona", ""))
        q.add(f, "description", flow.get("description", ""))
        for order, step in enumerate(flow.get("steps") or [], 1):
            s = q.iri("flowstep", f"{number}.{order}")
            q.typed(s, "FlowStep")
            q.add(f, "hasStep", s, literal=False)
            q.add(s, "order", order)
            q.add(s, "label", step.get("label", ""))
            for ident in step.get("nodes") or []:
                if ident in node_iri:
                    q.add(s, "involves", node_iri[ident], literal=False)
    modules_by_name = {m.get("name", "").upper(): node_iri[m["id"]] for m in model.get("modules") or [] if m.get("id") in node_iri}
    rule_iri = {}
    for card in model.get("rules") or []:
        r = q.iri("rule", card["id"])
        rule_iri[card["id"]] = r
        q.typed(r, "BusinessRule")
        q.add(r, "ruleId", card["id"])
        for key in ("name", "category", "priority", "confidence", "plain", "parameters", "defect"):
            q.add(r, {"plain": "plainEnglish", "defect": "suspectedDefect"}.get(key, key), card.get(key, ""))
        q.add(r, "source", card.get("source", ""))
        q.add(r, "programName", card.get("program", ""))
        q.add(r, "specification", " ".join(f"{c['clause']} {c['text']}" for c in card.get("spec") or []))
        if card.get("program", "").upper() in modules_by_name:
            q.add(r, "citesModule", modules_by_name[card["program"].upper()], literal=False)
    for question in model.get("sme_questions") or []:
        if question.get("rule") in rule_iri:
            q.add(rule_iri[question["rule"]], "smeQuestion", question.get("text", ""))
    for domain in (generated or {}).get("domains") or []:
        gd = q.iri("gdomain", domain["key"])
        q.typed(gd, "GeneratedDomain")
        q.add(gd, "name", domain.get("name", ""))
        q.add(gd, "generatedBy", "reframe from claude results")
        for function in domain.get("functions") or []:
            fn = q.iri("function", function["id"])
            q.typed(fn, "BusinessFunction")
            q.add(fn, "name", function.get("name", ""))
            q.add(fn, "inDomain", gd, literal=False)
            q.add(fn, "generatedBy", "reframe from claude results")
            for cap in function.get("capabilities") or []:
                c = q.iri("capability", cap["id"])
                q.typed(c, "Capability")
                q.add(c, "ofFunction", fn, literal=False)
                q.add(c, "title", cap.get("title", ""))
                q.add(c, "story", cap.get("story", ""))
                for req in cap.get("requirements") or []:
                    rq = q.iri("requirement", req["key"])
                    q.typed(rq, "Requirement")
                    q.add(rq, "ofCapability", c, literal=False)
                    q.add(rq, "code", req.get("code", ""))
                    q.add(rq, "kind", req.get("kind", ""))
                    q.add(rq, "earsPattern", req.get("pattern", ""))
                    q.add(rq, "text", req.get("text", ""))
                    q.add(rq, "generatedBy", "reframe from claude results")
                    for rule in req.get("rules") or []:
                        if rule in rule_iri:
                            q.add(rq, "backedBy", rule_iri[rule], literal=False)
    return q


# ---- the loader --------------------------------------------------------------------------------------


def package(target: Path) -> Path:
    """The graph loader's Lambda package: the one handler file shipped with Reframe."""
    source = resources.files("reframe.infrastructure.graph_loader") / "handler.py"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("handler.py", date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        z.writestr(info, source.read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_bytes(buffer.getvalue())
    target.chmod(0o600)
    return target


class Loader:
    """Calls the run's graph loader function. Every call names the cluster endpoint, read from AWS by the cluster's ARN."""

    def __init__(self, aws, function: str, cluster_arn: str, scratch: Path) -> None:
        self.aws, self.function, self.cluster_arn, self.scratch = aws, function, cluster_arn, scratch
        self._endpoint: str | None = None

    def endpoint(self) -> str:
        if self._endpoint is None:
            clusters = self.aws.read("neptune", "describe-db-clusters", "--db-cluster-identifier", self.cluster_arn)["DBClusters"]
            self._endpoint = clusters[0]["Endpoint"]
        return self._endpoint

    def call(self, event: dict[str, Any], change: bool = False) -> Any:
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        out = self.scratch / "loader-reply.json"
        payload = json.dumps({**event, "endpoint": self.endpoint()})
        argv = ("lambda", "invoke", "--function-name", self.function, "--cli-binary-format", "raw-in-base64-out", "--payload", payload, str(out))
        reply = self.aws.write(*argv) if change else self.aws.read(*argv)
        if reply.get("FunctionError"):
            raise Rejected("graph loader", out.read_text(errors="replace")[:500])
        value = json.loads(out.read_text() or "{}")
        if value.get("status") not in (200,):
            raise Rejected("graph loader", json.dumps(value)[:500])
        return value["body"]

    def sparql(self, query: str) -> list[dict[str, Any]]:
        body = self.call({"action": "sparql", "query": query})
        return [{k: v.get("value") for k, v in row.items()} for row in body["results"]["bindings"]]

    def update(self, update: str) -> Any:
        return self.call({"action": "update", "update": update}, change=True)

    def load(self, source: str, role: str, wait: Callable[[float], None] = time.sleep, timeout: float = 1800) -> dict[str, Any]:
        started = self.call({"action": "load", "source": source, "role": role}, change=True)
        load_id = started["payload"]["loadId"]
        deadline = time.time() + timeout
        while True:
            status = self.call({"action": "status", "load_id": load_id})["payload"]
            overall = status.get("overallStatus") or {}
            state = overall.get("status", "")
            if state == "LOAD_COMPLETED":
                return {"load_id": load_id, **{k: overall.get(k) for k in ("totalRecords", "totalDuplicates", "parsingErrors", "insertErrors", "totalTimeSpent")}}
            if state.startswith("LOAD_") and state not in ("LOAD_NOT_STARTED", "LOAD_IN_PROGRESS", "LOAD_IN_QUEUE"):
                raise Stop(Outcome.FAILED, f"the Neptune bulk load ended {state}: {json.dumps(status.get('errors') or {})[:300]}")
            if time.time() > deadline:
                raise Stop(Outcome.UNCERTAIN, f"the Neptune bulk load {load_id} had not finished after {timeout // 60:.0f} minutes; reframe resume checks it again")
            wait(10)


def counts(loader: Loader, run: str) -> dict[str, int]:
    rows = loader.sparql(f"SELECT ?t (COUNT(?s) AS ?n) WHERE {{ GRAPH <{graph_iri(run)}> {{ ?s a ?t }} }} GROUP BY ?t")
    return {row["t"].replace(ONTOLOGY, ""): int(row["n"]) for row in rows}


def joined(loader: Loader, run: str) -> int:
    """How many of Claude's rules cite a module whose program AWS Transform's graph also has."""
    rows = loader.sparql(f"PREFIX c: <{ONTOLOGY}> PREFIX o: <{AWS_ONTOLOGY}> SELECT (COUNT(DISTINCT ?rule) AS ?n) WHERE {{ "
                         f"GRAPH <{graph_iri(run)}> {{ ?rule c:citesModule ?m . ?m c:name ?name }} ?p a o:Executable ; o:name ?name }}")
    return int(rows[0]["n"]) if rows else 0


def drop(loader: Loader, run: str) -> None:
    loader.update(f"DROP SILENT GRAPH <{graph_iri(run)}>")


__all__ = ["build", "package", "Loader", "counts", "joined", "drop", "graph_iri", "NotSent", "Uncertain"]
