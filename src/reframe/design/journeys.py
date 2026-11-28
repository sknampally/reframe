"""Pipeline 4, stage D3: user journeys (docs/pipeline-4.md, section 4, P4-04).

One headless Claude step per domain groups the screens into the journeys people do: each with a name, the role who does
it, its goal, its steps in order (screen and action) and the capabilities and requirements it serves. A screen belongs
to the domain of the business functions whose rules its program carries. Each step is given its domain's screens (fields,
keys), capabilities and requirements, the whole screen flow from stage D2, and personas.md when the context has one.

Reframe checks every answer: every screen named exists; each step follows a link in the flow from the step before (or
stays on its screen, or goes back the way it came); every capability and requirement key exists, and a requirement
belongs to one of the journey's capabilities; and every screen of the domain is in a journey or listed as unused with a
reason. An answer that fails is asked once more with the reasons; what still fails is kept and listed. A domain whose
input is unchanged keeps its journeys.

    python -m reframe.design.journeys [--run <run>] [--context <folder>]

writes design/journeys.json and outputs/design/journeys.html.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

FILE = "design/journeys.json"
METHOD = "journeys-1"
PARALLEL = 4

SCHEMA = {"type": "object", "required": ["journeys", "unused"], "properties": {
    "journeys": {"type": "array", "items": {"type": "object", "required": ["name", "role", "goal", "steps", "capabilities", "requirements"], "properties": {
        "name": {"type": "string"}, "role": {"type": "string"}, "goal": {"type": "string"},
        "steps": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["screen", "action"], "properties": {
            "screen": {"type": "string"}, "action": {"type": "string"}}}},
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}}}}},
    "unused": {"type": "array", "items": {"type": "object", "required": ["screen", "reason"], "properties": {
        "screen": {"type": "string"}, "reason": {"type": "string"}}}}}}

PROMPT = """Read {input}. It holds one business domain of a legacy mainframe application that is being modernized: its business functions, their capabilities and recommended requirements (each with a key), the domain's 3270 screens (fields and function keys), and the whole application's screen flow (which screen leads to which, by key, menu option or transfer). The legacy source is under {legacy}/ if you need it; do not change anything.

Group this domain's screens into user journeys: the tasks people do with them, from where they start to where they finish (for example "Sign in", "Find and view an account", "Update a card"). For each journey give:
- "name": a short task name a business user would say;
- "role": who does it (from "personas" if given, otherwise from the capabilities' user stories);
- "goal": one sentence on what the person gets done;
- "steps": the screens in order, each with "screen" (the map name, exactly as listed, such as COSGN0A) and "action" (what the person does there, in plain words). Start where the person really starts (a journey may begin at the sign-on or a menu screen of another domain) and follow only links listed in "flow"; a step may stay on the same screen (paging, correcting an error) or go back the way it came;
- "capabilities": the keys of the capabilities the journey serves, exactly as listed;
- "requirements": the keys of the requirements the journey exercises, each belonging to one of its capabilities.

If the file lists "decisions", the owner made them: follow each one that applies to this domain.

Every screen of this domain ("screens") must be in at least one journey, or be listed in "unused" with the reason (for example, no link reaches it). Keep journeys few and meaningful: one per task, not one per screen. Answer only with the JSON."""

RETRY = """Your answer failed Reframe's checks. Answer again with the whole JSON, fixing these:

{problems}"""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


# ---- inputs ------------------------------------------------------------------------------------------------------------

def domains_of_screens(screens: dict[str, Any], rules: list[dict[str, Any]], requirements: dict[str, Any]) -> dict[str, list[str]]:
    """Each domain's screens: a screen belongs to the domain of the business function holding most of its programs'
    recommended rules. A screen whose programs carry no rule goes to "No domain"."""
    domain_of = {f: d["key"] for d in requirements.get("domains") or [] for f in d["functions"]}
    by_program: dict[str, Counter] = defaultdict(Counter)
    for r in rules:
        by_program[str(r.get("program", "")).upper()][r.get("function", "")] += 1
    found: dict[str, list[str]] = defaultdict(list)
    for s in screens["screens"]:
        counts: Counter[str] = Counter()
        for p in s["programs"]:
            counts.update(by_program.get(p["name"].upper(), Counter()))
        function = counts.most_common(1)[0][0] if counts else None
        found[domain_of.get(function, "") if function is not None else ""].append(s["map"])
    return dict(found)


def screen_brief(s: dict[str, Any], full: bool) -> dict[str, Any]:
    brief = {"screen": s["map"], "title": s["title"], "programs": [p["name"] for p in s["programs"]]}
    if full:
        labels = {(f["line"], f["column"] + f["length"] + 1): f["initial"].strip() for f in s["fields"] if f["kind"] == "label"}
        def label_for(f):
            near = [t for (line, end), t in labels.items() if line == f["line"] and end <= f["column"] + 2 and t]
            return near[-1] if near else ""
        brief["inputs"] = [{"field": f["name"], "label": label_for(f), "length": f["length"]} for f in s["fields"] if f["kind"] == "input"]
        brief["shows"] = [f["name"] for f in s["fields"] if f["kind"] == "output"][:60]
        brief["keys"] = [{"key": k["key"], "says": k["says"]} for k in s["keys"]["shown"]]
    return brief


def flow_brief(flow: dict[str, Any]) -> list[dict[str, str]]:
    """Screen-to-screen links: from, to, and by (a key, a menu option, or a transfer)."""
    from reframe.design.flow import CALLER
    screens_of = flow["screens_of_programs"]
    found, seen = [], set()
    for link in flow["links"]:
        if link["kind"] == "transaction":
            continue
        values = [str(v) for v in (link.get("option") or {}).values()]
        by = link.get("key") or ("option " + " ".join(sorted(values, key=lambda v: (not v.isdigit(), v))) if values else link["kind"])
        for a in screens_of.get(link["from"], []):
            targets = ["(back to the screen it came from)"] if link["to"] == CALLER else screens_of.get(link["to"], [])
            for b in targets:
                mark = (a, b, by)
                if a != b and mark not in seen:
                    seen.add(mark)
                    found.append({"from": a, "to": b, "by": by})
    return found


def domain_input(domain: dict[str, Any], screens: dict[str, Any], mine: list[str], flow: dict[str, Any], requirements: dict[str, Any],
                 personas: str) -> dict[str, Any]:
    names = requirements.get("names") or {}
    capabilities = []
    for fid in domain["functions"]:
        body = (requirements.get("functions") or {}).get(fid) or {}
        page = "rec/" + (fid or "unassigned")
        for cap in body.get("capabilities") or []:
            capabilities.append({"key": f"{page}#{cap['number']}", "function": (names.get(fid) or {}).get("name", fid), "title": cap["title"],
                                 "story": cap.get("story", ""),
                                 "requirements": [{"key": f"{page}/{r['code']}", "text": r["text"][:220]} for r in cap.get("requirements") or []]})
    data = {"domain": {"name": domain["name"], "description": domain.get("description", ""),
                       "functions": [{"name": (names.get(f) or {}).get("name", f), "description": (names.get(f) or {}).get("description", "")}
                                     for f in domain["functions"]]},
            "capabilities": capabilities,
            "screens": [screen_brief(s, True) for s in screens["screens"] if s["map"] in mine],
            "other_screens": [screen_brief(s, False) for s in screens["screens"] if s["map"] not in mine],
            "flow": flow_brief(flow)}
    if personas:
        data["personas"] = personas
    return data


# ---- checks ------------------------------------------------------------------------------------------------------------

def problems(answer: dict[str, Any] | None, data: dict[str, Any]) -> list[str]:
    if not isinstance(answer, dict):
        return ["no answer"]
    found = []
    all_screens = {s["screen"] for s in data["screens"] + data["other_screens"]}
    mine = {s["screen"] for s in data["screens"]}
    links = {(l["from"], l["to"]) for l in data["flow"]}
    came_back = {l["from"] for l in data["flow"] if l["to"].startswith("(back")}
    caps = {c["key"]: {r["key"] for r in c["requirements"]} for c in data["capabilities"]}
    covered = set()
    for j in answer.get("journeys") or []:
        name = j.get("name") or "a journey"
        steps = [s.get("screen", "") for s in j.get("steps") or []]
        for screen in steps:
            if screen not in all_screens:
                found.append(f'{name}: "{screen}" is not a screen; use the map names listed')
        for a, b in zip(steps, steps[1:]):
            if a in all_screens and b in all_screens and a != b and (a, b) not in links and (b, a) not in links and not (a in came_back and b in steps[:steps.index(a)]):
                found.append(f"{name}: no link in the flow goes from {a} to {b}")
        covered.update(steps)
        for key in j.get("capabilities") or []:
            if key not in caps:
                found.append(f'{name}: capability "{key}" is not listed')
        allowed = set().union(*(caps.get(k, set()) for k in j.get("capabilities") or [])) if j.get("capabilities") else set()
        for key in j.get("requirements") or []:
            if key not in allowed:
                found.append(f'{name}: requirement "{key}" is not one of the journey\'s capabilities\' requirements')
        if not j.get("capabilities"):
            found.append(f"{name}: no capability")
    unused = {u.get("screen") for u in answer.get("unused") or []}
    for screen in sorted(mine - covered - unused):
        found.append(f"screen {screen} is in no journey and not listed as unused")
    return found


# ---- run ---------------------------------------------------------------------------------------------------------------

def run(folder, claude, context: Path | None = None, more=None, plan_only: bool = False) -> dict[str, Any]:
    """D3 for every domain with screens whose input changed. Stops, after the steps already running, on Claude's usage
    limit or a sign-in problem; running it again continues with what is left. `more(key, data)` gives a domain's extra
    input for a round (the decisions that name it); `plan_only` says what would be redone, and why, without a step."""
    from reframe.design import rounds
    from reframe.claude.steps import stop_for
    from reframe.claude.workspace import paths
    places = paths(folder)
    project = folder.state["project"]
    screens = folder.load("design/screens.json")
    flow = folder.load("design/flow.json")
    requirements = folder.load("outputs/reconcile/requirements.json") or {}
    rules = (folder.load("outputs/reconcile/rules.json") or {}).get("rules") or []
    if not screens or not flow:
        raise ValueError("run the screen inventory and the screen flow first (python -m reframe.design.inventory, then reframe.design.flow)")
    personas = (context / "personas.md").read_text() if context and (context / "personas.md").is_file() else ""
    by_domain = domains_of_screens(screens, rules, requirements)
    domains = {d["key"]: d for d in requirements.get("domains") or []}
    domains[""] = domains.get("") or {"key": "", "name": "No domain", "description": "Screens whose programs carry no recommended rule.", "functions": []}
    saved = folder.load(FILE, {}) or {}
    kept = saved.get("domains") or {}
    log = saved.get("log") or []
    folder_in = places["analysis"] / "reframe" / "design"
    folder_in.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    stopped: list[Any] = []
    todo, why = [], []
    for key, mine in sorted(by_domain.items()):
        data = domain_input(domains.get(key) or {"name": key, "functions": []}, screens, mine, flow, requirements, personas)
        data.update(more(key, data) if more else {})
        digest = _hash([data, METHOD])
        before = kept.get(key) or {}
        if before.get("hash") != digest or before.get("problems"):
            todo.append((key, data, digest))
            why.append((key, rounds.changed(before or None, rounds.parts(data, METHOD)) if before.get("hash") != digest else ["problems"]))
        elif before and not before.get("parts") and not plan_only:  # kept, but made before rounds recorded its pieces (P4-10)
            before["parts"] = rounds.parts(data, METHOD)
    plan = rounds.summary(why, [k for k in by_domain if k not in {t[0] for t in todo}])
    if plan_only:
        return {"round": plan}

    def one(item) -> None:
        key, data, digest = item
        if stopped:
            return
        safe = "".join(c if c.isalnum() else "-" for c in (key or "no-domain")).strip("-")[:50]
        path = folder_in / f"journeys-{safe}.json"
        path.write_text(json.dumps(data, indent=1))
        prompt = PROMPT.format(input=f"analysis/{project}/{path.relative_to(places['analysis']).as_posix()}", legacy=f"legacy/{project}")
        answer, wrong, attempts = None, ["no answer"], 0
        for attempt in (1, 2):
            attempts = attempt
            step = claude.run(f"journeys-{safe}{'-retry' if attempt == 2 else ''}", prompt if attempt == 1 else prompt + "\n\n" + RETRY.format(
                problems="\n".join(f"- {p}" for p in wrong)), timeout=3600, schema=SCHEMA)
            with lock:
                log.append({"domain": key, "attempt": attempt, **{k: v for k, v in step.as_json().items() if k in ("ok", "seconds", "tokens", "cost_usd", "problem")}})
            if not step.ok:
                if step.problem in ("limit", "signin"):
                    with lock:
                        stopped.append(step)
                    return
                wrong = [f"the step did not finish ({step.problem})"]
                continue
            answer = claude.structured(step)
            wrong = problems(answer, data)
            if not wrong:
                break
        with lock:
            kept[key] = {"hash": digest, "parts": rounds.parts(data, METHOD), "name": data["domain"]["name"], "screens": [s["screen"] for s in data["screens"]], "attempts": attempts,
                         "journeys": (answer or {}).get("journeys") or [], "unused": (answer or {}).get("unused") or [], "problems": wrong}
            folder.save(FILE, {"domains": kept, "log": log})

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        list(pool.map(one, todo))
    folder.save(FILE, {"domains": kept, "log": log})
    if stopped:
        raise stop_for(stopped[0], "the journeys")
    journeys = [{**j, "domain": key, "domain_name": d["name"], "id": f"J{n:02d}"}
                for n, (key, d, j) in enumerate(((k, d, j) for k, d in sorted(kept.items()) for j in d["journeys"]), 1)]
    value = {"domains": kept, "log": log, "journeys": journeys,
             "unused": [{**u, "domain": k} for k, d in kept.items() for u in d["unused"]],
             "problems": [f"{d['name']}: {p}" for d in kept.values() for p in d["problems"]],
             "steps": len(todo), "cost_usd": round(sum(x.get("cost_usd") or 0 for x in log), 2)}
    folder.save(FILE, value)
    return {**value, "round": plan}


def main(argv: list[str] | None = None) -> int:
    import argparse
    from reframe.core.errors import Stop
    from reframe.core.runfolder import find_run
    from reframe.design import journeypage
    from reframe.reconcile.pipeline import claude_code
    parser = argparse.ArgumentParser(prog="python -m reframe.design.journeys", description="User journeys from the screens (P4-04)")
    parser.add_argument("--run", help="the run (default: the latest run)")
    parser.add_argument("--context", help="a folder with personas.md")
    parser.add_argument("--auth", choices=("subscription", "api-key", "bedrock"), default="subscription")
    args = parser.parse_args(argv)
    folder = find_run(args.run)
    if (folder.state.get("reconcile") or {}).get("status") != "DONE":
        print("The reconciliation is not done on this run; the journeys use its requirements.")
        return 2
    from reframe.claude import preflight
    report = preflight.check(args.auth)
    if not report.ok:
        print(report.problems())
        return 2
    try:
        value = run(folder, claude_code(folder, args.auth), Path(args.context).expanduser() if args.context else None)
    except Stop as stop:
        print(stop.message)
        return 3
    page = journeypage.draw(folder, value)
    print(json.dumps({"journeys": len(value["journeys"]), "unused": len(value["unused"]), "problems": value["problems"], "steps": value["steps"],
                      "cost_usd": value["cost_usd"], "page": str(page)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
