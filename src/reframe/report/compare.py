"""Stage C8: the page comparing AWS Transform's and Claude's results (docs/pipeline-2.md).

outputs/comparison/index.html, self-contained, from the overview down: the key numbers side by side; domains aligned by
the programs they share; each program and which tool covered it; rules paired in the same program (deterministic) and,
when it ran, by a labelled AI pass with a reason and a confidence for each pair; and what only one tool found. Every
item links into both business reviews. Both results are model output, and the page says so.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from reframe.core.runfolder import RunFolder, iso

PROGRAM = re.compile(r"^[A-Z0-9][A-Z0-9#@$-]{1,15}$")


def _programs_aws(aws: dict[str, Any]) -> dict[str, set[str]]:
    """Each AWS function's programs: those in its discovery files and those its rules name."""
    out: dict[str, set[str]] = defaultdict(set)
    for f in aws["functions"]:
        for p in f.get("technical", {}).get("programs") or []:
            if p.get("name"):
                out[f["id"]].add(str(p["name"]).upper())
    for r in aws["rules"]:
        if r.get("program") and r.get("disposition") == "captured":
            out[r["function"]].add(str(r["program"]).upper())
    return out


def _links(data: dict[str, Any], base: str) -> dict[str, Any]:
    """Hash links into a review page for each rule key, requirement key and function id."""
    cap_index = {c["id"]: i for i, c in enumerate(data["capabilities"])}
    fn_index = {f["id"]: i for i, f in enumerate(data["functions"])}
    rule_cap: dict[str, Any] = {}
    for r in data["requirements"]:
        for k in r.get("rules") or []:
            rule_cap.setdefault(k, r["capability"])
    out = {}
    for r in data["rules"]:
        cap = rule_cap.get(r["key"])
        out[r["key"]] = f"{base}#/c/{cap_index[cap]}/{r['key']}" if cap in cap_index else f"{base}#/f/{fn_index.get(r['function'], 0)}"
    for f in data["functions"]:
        out[f["id"]] = f"{base}#/f/{fn_index[f['id']]}"
    for i, d in enumerate(data["domains"]):
        out["domain:" + d["key"]] = f"{base}#/d/{i}"
    return out


def model(folder: RunFolder) -> dict[str, Any]:
    from reframe.report.build import collect, collect_claude
    aws, claude = collect(folder), collect_claude(folder)
    plugin_model = folder.load("outputs/claude/model.json") or {}
    aws_links, claude_links = _links(aws, "../report/index.html"), _links(claude, "../claude/report/index.html")
    aws_rules = [r for r in aws["rules"] if r["disposition"] == "captured"]
    claude_rules = claude["rules"]

    # programs per side
    aws_fn_programs = _programs_aws(aws)
    claude_domain_programs: dict[str, set[str]] = defaultdict(set)
    plugin_domains = {d["id"]: d for d in plugin_model.get("domains") or []}
    for m in (plugin_model.get("modules") or []) + (plugin_model.get("jobs") or []):  # AWS counts batch jobs as programs too
        if m.get("name") and m.get("domain") in plugin_domains:
            claude_domain_programs[m["domain"]].add(str(m["name"]).upper())
    for r in claude_rules:
        card_domain = next((c.get("domain") for c in plugin_model.get("rules") or [] if c["id"] == r["id"]), None)
        if r.get("program") and card_domain:
            claude_domain_programs[card_domain].add(r["program"].upper())
    aws_programs = set().union(*aws_fn_programs.values()) if aws_fn_programs else set()
    claude_programs = set().union(*claude_domain_programs.values()) if claude_domain_programs else set()

    # domains: AWS domain -> its functions' programs; aligned with each Claude domain by shared programs
    aws_domains = []
    for d in aws["domains"]:
        programs = set().union(*(aws_fn_programs.get(f, set()) for f in d["functions"])) if d["functions"] else set()
        matches = []
        for cid, cprog in claude_domain_programs.items():
            shared = programs & cprog
            if shared:
                matches.append({"id": cid, "name": plugin_domains[cid]["name"], "shared": sorted(shared), "share": round(len(shared) / max(1, len(programs)), 2)})
        matches.sort(key=lambda m: -len(m["shared"]))
        aws_domains.append({"key": d["key"], "name": d["name"], "kind": d["kind"], "functions": len(d["functions"]), "programs": sorted(programs),
                            "link": aws_links.get("domain:" + d["key"]), "matches": matches})
    claude_domains = []
    claude_review = {d["name"]: d for d in claude["domains"]}
    for cid, d in plugin_domains.items():
        programs = claude_domain_programs.get(cid, set())
        shared_any = programs & aws_programs
        review = claude_review.get(d["name"])
        claude_domains.append({"id": cid, "name": d["name"], "programs": sorted(programs), "shared": sorted(shared_any),
                               "only_claude": sorted(programs - aws_programs),
                               "link": claude_links.get("domain:" + review["key"]) if review else None})

    # programs
    aws_by_program: dict[str, list[str]] = defaultdict(list)
    for r in aws_rules:
        aws_by_program[str(r["program"]).upper()].append(r["key"])
    claude_by_program: dict[str, list[str]] = defaultdict(list)
    for r in claude_rules:
        claude_by_program[str(r["program"]).upper()].append(r["key"])
    aws_fn_of = defaultdict(list)
    for fid, progs in aws_fn_programs.items():
        for p in progs:
            aws_fn_of[p].append(fid)
    claude_dom_of = defaultdict(list)
    for cid, progs in claude_domain_programs.items():
        for p in progs:
            claude_dom_of[p].append(cid)
    fn_name = {f["id"]: f["name"] for f in aws["functions"]}
    program_rows = []
    for p in sorted((aws_programs | claude_programs | set(aws_by_program) | set(claude_by_program)) - {""}):
        program_rows.append({"program": p, "aws_functions": [{"name": fn_name.get(f, f), "link": aws_links.get(f)} for f in sorted(aws_fn_of.get(p, []))],
                         "claude_domains": [plugin_domains[c]["name"] for c in sorted(claude_dom_of.get(p, [])) if c in plugin_domains],
                         "aws_rules": len(aws_by_program.get(p, [])), "claude_rules": len(claude_by_program.get(p, [])),
                         "in_aws": p in aws_programs or p in aws_by_program, "in_claude": p in claude_programs or p in claude_by_program})

    def brief(r: dict[str, Any], links: dict[str, str]) -> dict[str, Any]:
        return {"key": r["key"], "id": r.get("id"), "name": r.get("name", ""), "text": r.get("text", ""), "program": r.get("program", ""),
                "priority": r.get("priority", ""), "link": links.get(r["key"])}

    pairs_by_program = []
    for p in sorted(set(aws_by_program) & set(claude_by_program)):
        pairs_by_program.append({"program": p,
                                 "aws": [brief(r, aws_links) for r in aws_rules if str(r["program"]).upper() == p],
                                 "claude": [brief(r, claude_links) for r in claude_rules if str(r["program"]).upper() == p]})
    ai = folder.load("outputs/comparison/ai-pairs.json") or {}
    aws_index, claude_index = {r["key"]: brief(r, aws_links) for r in aws_rules}, {r["key"]: brief(r, claude_links) for r in claude_rules}
    ai_pairs = [{**pair, "aws_rule": aws_index.get(pair["aws"]), "claude_rule": claude_index.get(pair["claude"])}
                for pair in ai.get("pairs") or [] if pair.get("aws") in aws_index and pair.get("claude") in claude_index]
    paired_aws = {p["aws"] for p in ai_pairs}
    paired_claude = {p["claude"] for p in ai_pairs}
    snapshot = folder.load("snapshot.json") or {}
    claude_state = folder.state.get("claude") or {}
    numbers = [
        ["Business domains", len([d for d in aws["domains"] if d["kind"] == "business"]), len(plugin_domains),
         "AWS: business domains. Claude: the functional domains the plugin's assessment found."],
        ["Business functions", len(aws["functions"]), len([f for f in claude["functions"] if not f["id"].endswith("/other")]),
         "Claude's are generated by Reframe from the plugin's results."],
        ["Capabilities", len(aws["capabilities"]), len([c for c in claude["capabilities"] if not c["function"].endswith("/other")]), ""],
        ["Requirements", len(aws["requirements"]), len(claude["requirements"]), "Claude's are generated by Reframe from the plugin's results."],
        ["Business rules", len(aws_rules), len(claude_rules), "AWS: rules it kept as business rules. Claude: rule cards, each checked by a second agent."],
        ["Critical rules (P0)", None, sum(1 for r in claude_rules if r.get("priority") == "P0"), "AWS gives no priority."],
        ["Open questions", len(aws["questions"]), len(claude["questions"]), ""],
        ["Programs and jobs covered", len(aws_programs | set(aws_by_program)), len(claude_programs | set(claude_by_program)),
         "Programs and batch jobs a tool placed in a function or domain, or cited in a rule."],
    ]
    return {
        "generated_at": iso(), "run": folder.root.name, "project": folder.state.get("project"),
        "source": {"name": Path(str(snapshot.get("source") or "")).name or snapshot.get("source"), "commit": snapshot.get("commit"),
                   "sha256": snapshot.get("sha256"), "files": snapshot.get("files")},
        "aws_at": folder.state.get("retrieved_at"), "claude_at": claude_state.get("finished_at") or claude_state.get("updated_at"),
        "plugin": claude_state.get("plugin") or {}, "numbers": numbers, "aws_domains": aws_domains, "claude_domains": claude_domains,
        "programs": program_rows, "pairs_by_program": pairs_by_program,
        "ai": {"ran": bool(ai), "note": ai.get("note", ""), "pairs": ai_pairs, "at": ai.get("at")},
        "only": {"aws_programs": sorted((aws_programs | set(aws_by_program)) - (claude_programs | set(claude_by_program))),
                 "claude_programs": sorted((claude_programs | set(claude_by_program)) - (aws_programs | set(aws_by_program))),
                 "aws_rules_unpaired": [aws_index[k] for k in aws_index if k not in paired_aws and str(aws_index[k]["program"]).upper() not in claude_by_program][:2000],
                 "claude_rules_unpaired": [claude_index[k] for k in claude_index if k not in paired_claude and str(claude_index[k]["program"]).upper() not in aws_by_program][:2000]},
        "links": {"aws": "../report/index.html", "claude": "../claude/report/index.html",
                  "reconciled": "../reconcile/index.html" if folder.exists("outputs/reconcile/index.html") else None},
    }


# ---- the AI pairing (labelled as AI output) ------------------------------------------------------------------

PAIR_SCHEMA = {"type": "object", "required": ["pairs"], "properties": {"pairs": {"type": "array", "items": {
    "type": "object", "required": ["aws", "claude", "relation", "confidence", "reason"],
    "properties": {"aws": {"type": "string"}, "claude": {"type": "string"},
                   "relation": {"type": "string", "enum": ["same", "overlapping", "conflicting"]},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}}}}}}

PAIR_PROMPT = """Read {input}. It holds business rules that two tools wrote about the same programs of one legacy system: "aws" (AWS Transform) and "claude" (Claude's code-modernization plugin), grouped by program.

For each group, pair each AWS rule with the Claude rule that describes the same business behaviour, if there is one. A pair's relation is "same" (the same rule), "overlapping" (they share part of the behaviour) or "conflicting" (they describe the same behaviour differently, for example different values or conditions). Give a confidence and a one-sentence reason that names what matches or differs. Leave a rule unpaired when nothing on the other side describes it. Use the keys exactly as given. Answer only with the JSON."""


def pair_with_ai(folder: RunFolder, claude_code, chunk: int = 12) -> dict[str, Any]:
    saved = folder.load("outputs/comparison/ai-pairs.json")
    if saved and saved.get("complete"):
        return saved
    from reframe.claude.workspace import paths
    data = model(folder)
    groups = [{"program": g["program"], "aws": [{"key": r["key"], "name": r["name"], "text": r["text"]} for r in g["aws"]],
               "claude": [{"key": r["key"], "name": r["name"], "text": r["text"]} for r in g["claude"]]} for g in data["pairs_by_program"]]
    folder_in = paths(folder)["analysis"] / "reframe"
    folder_in.mkdir(parents=True, exist_ok=True)
    pairs, steps = list((saved or {}).get("pairs") or []), list((saved or {}).get("steps") or [])
    done = {s["part"] for s in steps if s.get("ok")}
    for part, start in enumerate(range(0, len(groups), chunk), 1):
        if part in done:
            continue
        path = folder_in / f"compare-input-{part}.json"
        path.write_text(json.dumps(groups[start:start + chunk], indent=1))
        result = claude_code.run(f"compare-{part}", PAIR_PROMPT.format(input=f"analysis/{folder.state['project']}/reframe/{path.name}"),
                                 timeout=3600, schema=PAIR_SCHEMA)
        steps.append({"part": part, **{k: v for k, v in result.as_json().items() if k in ("ok", "seconds", "tokens", "cost_usd", "problem")}})
        if not result.ok:
            folder.save("outputs/comparison/ai-pairs.json", {"complete": False, "pairs": pairs, "steps": steps})
            from reframe.claude.steps import stop_for
            raise stop_for(result, "the AI pairing of rules")
        known = {(r["key"], g["program"]) for g in groups[start:start + chunk] for r in g["aws"] + g["claude"]}
        keys = {k for k, _ in known}
        for pair in (claude_code.structured(result) or {}).get("pairs") or []:
            if pair.get("aws") in keys and pair.get("claude") in keys:
                pairs.append(pair)
        folder.save("outputs/comparison/ai-pairs.json", {"complete": False, "pairs": pairs, "steps": steps})
    value = {"complete": True, "at": iso(), "pairs": pairs, "steps": steps,
             "note": "AI output: Claude read both tools' rules for each program both cover and proposed these pairs. Check each one."}
    folder.save("outputs/comparison/ai-pairs.json", value)
    folder.event("comparison_ai_pairs", pairs=len(pairs))
    return value


def build(folder: RunFolder) -> Path:
    data = model(folder)
    target = folder.path("outputs/comparison/index.html")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    page = TEMPLATE.replace("/*DATA*/", payload).replace("{{title}}", html.escape(f"AWS Transform and Claude compared: {data['project'] or 'run'}"))
    target.write_text(page)
    target.chmod(0o600)
    folder.event("comparison_built", programs=len(data["programs"]), program_groups=len(data["pairs_by_program"]), ai_pairs=len(data["ai"]["pairs"]))
    return target


TEMPLATE = (Path(__file__).with_name("compare.html")).read_text() if (Path(__file__).with_name("compare.html")).exists() else ""
