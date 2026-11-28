"""Stage 8: one self-contained HTML report of the run's results (specification section 4, stage 8).

outputs/report/index.html, with its data inline and no network needed to view it. It is written for business users and
functional subject-matter experts first: an overview, then each business domain, each business function, each
capability, and each requirement with the business rules behind it. Every requirement, rule and open question can be
marked Agree, Needs change or Question, and a reviewer downloads their marks. Technical detail has its own section.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from reframe.core.runfolder import RunFolder, iso
from reframe.transform import documents
from reframe.transform.traceability import load as load_traceability

UNASSIGNED = {"key": "unassigned", "name": "Programs not in a business function", "kind": "unassigned",
              "description": "Programs AWS Transform assessed that it did not place in any business function."}


# ---- reading AWS's files -------------------------------------------------------------------------


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(errors="replace")) if path.exists() else None
    except yaml.YAMLError:
        return None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(errors="replace")) if path.exists() else None
    except ValueError:
        return None


def _rules(trace: dict[str, Any], fid: str) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """The function's rules, and for each requirement code the keys of the rules that back it."""
    rules: list[dict[str, Any]] = []
    backing: dict[str, list[str]] = {}
    for item in trace.get("requirements") or []:
        if isinstance(item, dict) and item.get("req_id"):
            backing.setdefault(str(item["req_id"]), []).extend(f"{fid}/{r}" for r in item.get("rule_ids") or [])
    for rule in trace.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("rule_id"):
            continue
        key = f"{fid}/{rule['rule_id']}"
        req_ids = rule.get("req_ids") or []
        if isinstance(req_ids, str):
            req_ids = re.findall(r"REQ-[A-Z]+-\d+", req_ids)
        for code in req_ids:
            if key not in backing.setdefault(str(code), []):
                backing[str(code)].append(key)
        rules.append({"key": key, "function": fid, "id": str(rule["rule_id"]), "name": str(rule.get("rule_name") or ""),
                      "text": str(rule.get("rule_text") or ""), "program": str(rule.get("program") or ""),
                      "disposition": str(rule.get("disposition") or ""), "reason": str(rule.get("reason") or ""),
                      "requirements": [str(c) for c in req_ids]})
    return rules, backing


def collect(folder: RunFolder) -> dict[str, Any]:
    outputs = folder.path("outputs")
    spec = outputs / "requirements" / "spec"
    if not spec.is_dir():
        spec = outputs / "requirements"
    catalog = documents.catalog(outputs)
    domains: dict[str, dict[str, Any]] = {}
    functions, capabilities, requirements, rules, questions = [], [], [], [], []
    for document in sorted(spec.rglob("requirements.md")):
        parts = document.parent.relative_to(spec).parts
        if not parts:
            continue
        fid = "/".join(parts)
        parsed = documents.parse(document.read_text(errors="replace"))
        listed = catalog["functions"].get(documents.squeeze(parts[-1]))
        if len(parts) == 1:
            domain: dict[str, Any] = dict(UNASSIGNED)
        else:
            known = catalog["domains"].get(documents.squeeze(parts[0])) or (
                catalog["domains"].get(documents.squeeze(listed["domain"])) if listed else None)
            name = known["name"] if known else documents.spaced(parts[0])
            domain = {"key": documents.squeeze(name), "name": name, "description": known["description"] if known else "",
                      "kind": "technical" if documents.squeeze(name) == "technical" else "business"}
        domain = domains.setdefault(domain["key"], {**domain, "functions": []})
        domain["functions"].append(fid)

        trace_file = document.parent / "traceability.yaml"
        trace = load_traceability(trace_file) if trace_file.exists() else {}  # a function may come without one
        function_rules, backing = _rules(trace, fid)
        rules += function_rules
        rule_programs = {r["key"]: r["program"] for r in function_rules}
        used: set[str] = set()
        function: dict[str, Any] = {"id": fid, "domain": domain["key"], "name": listed["name"] if listed else (parsed["title"] or documents.spaced(parts[-1])),
                    "description": listed["description"] if listed else "", "capabilities": [],
                    "notes": [{"heading": n["heading"], "html": render_markdown(n["markdown"]), "technical": n["technical"]} for n in parsed["notes"]],
                    "summary": trace.get("summary") or {}}
        for found in parsed["capabilities"]:
            cid = f"{fid}#{found['number']}"
            capability = {"id": cid, "function": fid, "number": found["number"], "title": found["title"], "story": found["story"],
                          "fields": found["fields"], "other": render_markdown(found["other"]) if found["other"] else "",
                          "requirements": [], "questions": []}
            for item in found["requirements"]:
                key = f"{fid}/{item['code']}"
                backed = backing.get(item["code"], [])
                used.update(backed)
                requirements.append({"key": key, "code": item["code"], "kind": item["kind"], "pattern": item["pattern"], "text": item["text"],
                                     "capability": cid, "function": fid, "rules": backed,
                                     "programs": sorted({rule_programs.get(r, "") for r in backed} - {""})})
                capability["requirements"].append(key)
            for item in found["questions"]:
                key = f"{fid}/{item['code']}"
                questions.append({"key": key, "code": item["code"], "text": item["text"], "owner": item["owner"],
                                  "unverified": item["unverified"], "capability": cid, "function": fid})
                capability["questions"].append(key)
            capabilities.append(capability)
            function["capabilities"].append(cid)
        function["other_rules"] = [r["key"] for r in function_rules if r["disposition"] == "captured" and r["key"] not in used]
        function["set_aside"] = [r["key"] for r in function_rules if r["disposition"] != "captured" and r["key"] not in used]
        discovery = document.parent / "discovery"
        programs = (_yaml(discovery / "programs.yaml") or {}).get("programs") or []
        jobs = (_yaml(discovery / "batch-jobs.yaml") or {}).get("jobs") or {}
        screens = (_yaml(discovery / "screens.yaml") or {}).get("screens") or []
        stores = (_yaml(discovery / "data-stores.yaml") or {}).get("data_stores") or []
        job_list = [j for group in (jobs.values() if isinstance(jobs, dict) else [jobs]) for j in (group or []) if isinstance(j, dict)]
        function["technical"] = {"programs": [{"name": p.get("name"), "type": p.get("type"), "execution": p.get("execution")}
                                              for p in programs if isinstance(p, dict)],
                                 "jobs": [j.get("name") for j in job_list], "screens": [s.get("name") for s in screens if isinstance(s, dict)],
                                 "data_stores": [s.get("name") for s in stores if isinstance(s, dict)]}
        functions.append(function)

    order = {**{key: i for i, key in enumerate(catalog["domains"])}}
    ranked = sorted(domains.values(), key=lambda d: ({"business": 0, "technical": 1, "unassigned": 2}[d["kind"]], order.get(d["key"], 99), d["name"]))
    data_model = spec / "data-model.md"
    results = outputs / "aws" / "results"
    classification = next((_json(p) for p in sorted(results.glob("classification_*.json"))), None) or []
    issues = next((_json(p) for p in sorted(results.glob("codebase_issues_*.json"))), None) or []
    state = folder.state
    snapshot = folder.load("snapshot.json") or {}
    return {
        "generated_at": iso(),
        "run": folder.root.name,
        "project": state.get("project"),
        "source": {"form": snapshot.get("form"), "commit": snapshot.get("commit"), "files": snapshot.get("files"),
                   "sha256": snapshot.get("sha256"), "name": Path(str(snapshot.get("source") or "")).name or snapshot.get("source")},
        "started_at": state.get("started_at"), "retrieved_at": state.get("retrieved_at"),
        "graph": (state.get("graph") or {}).get("kind"),
        "domains": ranked, "functions": functions, "capabilities": capabilities,
        "requirements": requirements, "rules": rules, "questions": questions,
        "data_model": render_markdown(data_model.read_text(errors="replace")) if data_model.exists() else "",
        "classification": Counter(str(item.get("fileType") or "Unknown") for item in classification if isinstance(item, dict)).most_common(),
        "unclassified": [{"path": i.get("path"), "reason": i.get("reason")} for i in classification
                         if isinstance(i, dict) and str(i.get("fileType")) == "Unknown"][:500],
        "issues": [{"severity": (i.get("item") or {}).get("severity"), "name": (i.get("item") or {}).get("name"),
                    "description": (i.get("item") or {}).get("description"), "action": (i.get("item") or {}).get("action"),
                    "count": len(i.get("occurrences") or i.get("files") or []) or None}
                   for i in issues if isinstance(i, dict)],
        "decisions": folder.load("decisions.json", []),
        "links": {"coverage": _link(outputs / "requirements" / "spec" / "coverage-summary" / "coverage-report.html", outputs),
                  "catalog": _link(outputs / "catalog" / "index.html", outputs)},
    }


def _link(path: Path, outputs: Path) -> str | None:
    return "../" + path.relative_to(outputs).as_posix() if path.exists() else None


# ---- a small Markdown renderer for AWS's documents -------------------------------------------------


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    return text


def render_markdown(text: str) -> str:
    out: list[str] = []
    paragraph: list[str] = []
    listing: tuple[str, list[str]] | None = None
    table: list[str] = []

    def flush():
        nonlocal paragraph, listing, table
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph = []
        if listing:
            out.append(f"<{listing[0]}>" + "".join(f"<li>{_inline(item)}</li>" for item in listing[1]) + f"</{listing[0]}>")
            listing = None
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?[\s:|-]+\|?", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            if cells:
                head, body = cells[0], cells[1:]
                out.append("<div class='scroll'><table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                           + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body) + "</tbody></table></div>")
            table = []

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif stripped.startswith("|"):
            if paragraph or listing:
                saved = table
                table = []
                flush()
                table = saved
            table.append(stripped)
        elif re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            if table or paragraph:
                flush()
            kind = "ol" if stripped[0].isdigit() else "ul"
            if listing is None or listing[0] != kind:
                flush()
                listing = (kind, [])
            listing[1].append(re.sub(r"^([-*]|\d+\.)\s+", "", stripped))
        elif not stripped:
            flush()
        else:
            if table or listing:
                flush()
            paragraph.append(stripped)
    flush()
    return "\n".join(out)


# ---- the page ------------------------------------------------------------------------------------


def build(folder: RunFolder) -> Path:
    data = collect(folder)
    if folder.exists("outputs/claude/report/index.html"):
        data["nav"] = [["Claude's review", "../claude/report/index.html"], ["AWS Transform and Claude compared", "../comparison/index.html"]]
    if folder.exists("outputs/reconcile/index.html"):
        data.setdefault("nav", []).append(["Reconciled review", "../reconcile/index.html"])
    target = folder.path("outputs/report/index.html")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    page = TEMPLATE.replace("/*DATA*/", payload).replace("{{title}}", html.escape(f"Business review: {data['project'] or 'run'}"))
    target.write_text(page)
    target.chmod(0o600)
    folder.update(report="outputs/report/index.html")
    folder.event("report_built", functions=len(data["functions"]), requirements=len(data["requirements"]),
                 rules=sum(r["disposition"] == "captured" for r in data["rules"]), questions=len(data["questions"]))
    return target


# ---- pipeline 2: Claude's results in the same review --------------------------------------------------------

CLAUDE: dict[str, Any] = {"key": "claude", "name": "Claude (code-modernization plugin)", "short": "Claude", "generated": True,
          "generated_note": "The business functions, capabilities and requirements on this page were generated by Reframe from Claude's "
                            "results, outside the plugin. The rule cards and the open questions are as the plugin wrote them."}
EARS = {"ubiquitous": "Ubiquitous", "event-driven": "Event-driven", "state-driven": "State-driven", "unwanted behaviour": "Unwanted",
        "optional feature": "Optional", "complex": "Complex"}


def collect_claude(folder: RunFolder) -> dict[str, Any]:
    model = folder.load("outputs/claude/model.json") or {}
    generated = folder.load("outputs/claude/requirements.json") or {}
    project = folder.state.get("project")
    cards = {c["id"]: c for c in model.get("rules") or []}
    rule_key = lambda rid: f"claude/{rid}"  # noqa: E731
    domains, functions, capabilities, requirements, questions = [], [], [], [], []
    rule_function: dict[str, str] = {}
    rule_requirements: dict[str, list[str]] = {}
    by_domain_name = {d.get("name"): d for d in model.get("domains") or []}
    for gdomain in generated.get("domains") or []:
        pdomain = by_domain_name.get(gdomain["name"]) or {}
        domain = {"key": gdomain["key"], "name": gdomain["name"], "description": gdomain.get("description") or pdomain.get("description", ""),
                  "kind": "business", "functions": []}
        modules = [m for m in model.get("modules") or [] if m.get("domain") == pdomain.get("id")]
        technical = {"programs": [{"name": m.get("name"), "type": m.get("language"), "execution": None} for m in modules],
                     "jobs": [j.get("name") for j in model.get("jobs") or [] if j.get("domain") == pdomain.get("id")],
                     "screens": [x.get("name") for x in model.get("screens") or [] if x.get("domain") == pdomain.get("id")],
                     "data_stores": []}
        cited_here: set[str] = set()
        carried: set[str] = set()
        for function in gdomain.get("functions") or []:
            fid = function["id"]
            entry = {"id": fid, "domain": domain["key"], "name": function["name"], "description": function.get("description", ""),
                     "capabilities": [], "notes": [], "summary": {}, "other_rules": [], "set_aside": [], "technical": technical}
            for cap in function.get("capabilities") or []:
                capability = {"id": cap["id"], "function": fid, "number": cap["number"], "title": cap["title"], "story": cap.get("story", ""),
                              "fields": {}, "other": "", "requirements": [], "questions": []}
                for req in cap.get("requirements") or []:
                    keys = [rule_key(r) for r in req.get("rules") or [] if r in cards]
                    for r in req.get("rules") or []:
                        cited_here.add(r)
                        rule_function.setdefault(r, fid)
                        rule_requirements.setdefault(r, []).append(req["code"])
                    requirements.append({"key": req["key"], "code": req["code"], "kind": "N" if req.get("kind") == "non-functional" else "F",
                                         "pattern": EARS.get(req.get("pattern", ""), req.get("pattern", "")), "text": req.get("text", ""),
                                         "capability": cap["id"], "function": fid, "rules": keys, "sources": req.get("sources") or [],
                                         "programs": sorted({cards[r]["program"] for r in req.get("rules") or [] if r in cards and cards[r].get("program")})})
                    capability["requirements"].append(req["key"])
                for q in cap.get("questions") or []:
                    if q.get("rule"):
                        carried.add(q["rule"])
                    questions.append({"key": q["key"], "code": q["code"], "text": q.get("text", ""), "owner": q.get("owner", ""),
                                      "unverified": False, "capability": cap["id"], "function": fid, "rule": q.get("rule", "")})
                    capability["questions"].append(q["key"])
                capabilities.append(capability)
                entry["capabilities"].append(cap["id"])
            functions.append(entry)
            domain["functions"].append(fid)
        names = {m.get("name", "").upper() for m in modules}
        uncited = [c["id"] for c in model.get("rules") or [] if (c.get("domain") == pdomain.get("id") or c.get("program", "").upper() in names)
                   and c["id"] not in cited_here and c["id"] not in rule_function]
        open_qs = [q for q in model.get("sme_questions") or [] if q.get("rule") in cards and q["rule"] not in carried
                   and (cards[q["rule"]].get("domain") == pdomain.get("id") or cards[q["rule"]].get("program", "").upper() in names)]
        if uncited or open_qs:
            fid = f"claude/{domain['key']}/other"
            cid = f"{fid}#1"
            capability = {"id": cid, "function": fid, "number": 1, "title": "The plugin's open questions for subject-matter experts",
                          "story": "", "fields": {}, "other": "", "requirements": [], "questions": []}
            for n, q in enumerate(open_qs, 1):
                key = f"{fid}/SME-{n:03d}"
                questions.append({"key": key, "code": f"{q['rule']}", "text": q.get("text", ""), "owner": "Subject-matter expert",
                                  "unverified": False, "capability": cid, "function": fid, "rule": q["rule"]})
                capability["questions"].append(key)
            for rid in uncited:
                rule_function[rid] = fid
            capabilities.append(capability)
            functions.append({"id": fid, "domain": domain["key"], "name": "Other rule cards and questions",
                              "description": "Rule cards the plugin wrote for this domain that no generated requirement cites, and the plugin's open "
                                             "questions that no capability carries. Nothing the plugin wrote is left out of the review.",
                              "capabilities": [cid] if capability["questions"] else [], "notes": [], "summary": {},
                              "other_rules": [rule_key(r) for r in uncited], "set_aside": [], "technical": technical})
            domain["functions"].append(fid)
        domains.append(domain)
    rules = []
    for card in model.get("rules") or []:
        fid = rule_function.get(card["id"])
        if not fid:
            continue
        text = "\n".join(f"{c['clause']} {c['text']}" for c in card.get("spec") or []) or card.get("plain", "")
        rules.append({"key": rule_key(card["id"]), "function": fid, "id": card["id"], "name": card.get("name", ""), "text": text,
                      "program": card.get("program", ""), "disposition": "captured", "reason": "", "requirements": rule_requirements.get(card["id"], []),
                      "priority": card.get("priority", ""), "category": card.get("category", ""), "confidence": card.get("confidence", ""),
                      "source": card.get("source", ""), "plain": card.get("plain", ""), "defect": card.get("defect", "")})
    for function in functions:  # the rules each function cites, but no requirement in it backs: none by construction; keep the shape
        function.setdefault("other_rules", [])
    snapshot = folder.load("snapshot.json") or {}
    sections = [{"title": "Assessment: " + (s["title"] or "summary"), "html": render_markdown(s["markdown"]), "open": i == 0}
                for i, s in enumerate(model.get("assessment") or []) if s.get("markdown")]
    flows = model.get("flows") or []
    if flows:
        sections.append({"title": "Business flows", "html": "".join(
            f"<h3>{html.escape(f.get('name', ''))}</h3><p><b>{html.escape(f.get('persona', ''))}</b>: {html.escape(f.get('description', ''))}</p><ol>"
            + "".join(f"<li>{html.escape(st.get('label', ''))}</li>" for st in f.get("steps") or []) + "</ol>" for f in flows)})
    if model.get("observations"):
        sections.append({"title": "The map's observations", "html": "<ul>" + "".join(f"<li>{html.escape(str(o))}</li>" for o in model["observations"]) + "</ul>"})
    sections += [{"title": "Preflight: " + (s["title"] or "summary"), "html": render_markdown(s["markdown"])} for s in model.get("preflight") or [] if s.get("markdown")]
    sections.append({"title": "How the requirements were generated", "html": (
        f"<p>{html.escape(CLAUDE['generated_note'])}</p><p>Reframe asked Claude once per domain, with only the plugin's results for that domain. "
        f"Every requirement cites a rule card or a file and line that exist; {generated.get('dropped', 0)} proposed requirements cited neither and were dropped. "
        f"{generated.get('rules_cited', 0)} of {generated.get('rules_total', 0)} rule cards are cited by a requirement.</p>")})
    data_objects = "".join(f"<h3>{html.escape(s['title'])}</h3>" + render_markdown(s["markdown"]) for s in model.get("data_objects") or [] if s.get("title"))
    up = "../../../"
    pages = [["The plugin's own report", up + model["links"]["REPORT.html"], "REPORT.html"]] if (model.get("links") or {}).get("REPORT.html") else []
    if (model.get("links") or {}).get("TOPOLOGY.html"):
        pages.append(["The plugin's interactive map", up + model["links"]["TOPOLOGY.html"], "TOPOLOGY.html"])
    decisions = [d for d in folder.load("decisions.json", []) if str(d.get("question", "")).startswith("claude")]
    return {
        "generated_at": iso(), "run": folder.root.name, "project": project, "tool": CLAUDE,
        "source": {"form": snapshot.get("form"), "commit": snapshot.get("commit"), "files": snapshot.get("files"), "sha256": snapshot.get("sha256"),
                   "name": Path(str(snapshot.get("source") or "")).name or snapshot.get("source")},
        "started_at": (folder.state.get("claude") or {}).get("started_at"), "retrieved_at": model.get("read_at"),
        "domains": domains, "functions": functions, "capabilities": capabilities, "requirements": requirements, "rules": rules, "questions": questions,
        "data_model": data_objects, "sections": sections, "classification": [], "unclassified": [], "issues": [], "decisions": decisions,
        "links": {"pages": pages},
        "nav": ([] if folder.state.get("tools") == "claude" else  # a Claude-only run has no AWS review and no comparison
                [["AWS Transform's review", "../../report/index.html"], ["AWS Transform and Claude compared", "../../comparison/index.html"]])
               + ([["Reconciled review", "../../reconcile/index.html"]] if folder.exists("outputs/reconcile/index.html") else []),
    }


def build_claude(folder: RunFolder) -> Path:
    data = collect_claude(folder)
    target = folder.path("outputs/claude/report/index.html")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    page = TEMPLATE.replace("/*DATA*/", payload).replace("{{title}}", html.escape(f"Business review (Claude): {data['project'] or 'run'}"))
    target.write_text(page)
    target.chmod(0o600)
    folder.event("claude_report_built", functions=len(data["functions"]), requirements=len(data["requirements"]), rules=len(data["rules"]),
                 questions=len(data["questions"]))
    return target


TEMPLATE = (Path(__file__).with_name("template.html")).read_text() if (Path(__file__).with_name("template.html")).exists() else ""
