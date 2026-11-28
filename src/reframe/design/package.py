"""Pipeline 4, stage D7: the AI-DLC package (docs/pipeline-4.md, section 4, P4-08).

Written by Reframe from the final specifications (stage D6), with no model, in outputs/design/package/v<n>/, in three
forms, each a project root to start the forward build from:

- kiro/: `.kiro/specs/<capability>/requirements.md` (the EARS requirements with their acceptance criteria), `design.md`
  (screens, components, data contracts, rules, decision tables) and `tasks.md`; `.kiro/steering/product.md`, `tech.md`
  and `structure.md`;
- claude-code/: `CLAUDE.md`, `specs/<capability>.md`, `tasks.md`, and the prototype in `ui-reference/`;
- spec-kit/: `.specify/memory/constitution.md`, `specs/<nnn-capability>/spec.md`, `plan.md` and `tasks.md`, and a role
  file per role in `.claude/agents/`.

Every form also has `data-contracts.md` and `traceability.md`. Every function, capability, requirement, rule, acceptance
criterion and task has a short ID that stays the same from version to version (design/package-ids.json): a requirement
`OATM-REQ-F-123`, a rule `OATM-BR-004`, a criterion `OATM-C16-AC-03`, a task `OATM-C16-T2`. traceability.md takes each
back to the run's keys and the source lines. Build tasks are made per capability, each for a role and citing the
requirements and rules it implements; every requirement is in a task. Draft capabilities are marked draft everywhere.

What only the engineer's team can give (the target stack's detail, the house conventions, the stack-specific steps of
the Claude Code skills in `.claude/skills/`, each role's *On this stack*) is left as `[FILL IN: ...]` placeholders, and
START-HERE.md, in the package and in each form, says how to make it ready for forward engineering (handover.py).

A new version is written only when the package changes; CHANGES.md says what changed since the last. Reframe checks
every version: every ID of every specification is in every form, every task cites a requirement, every draft is marked.

    python -m reframe.design.package [--run <run>] [--context <folder>] [--out <empty folder> [--form kiro|claude-code|spec-kit|all]]
    python -m reframe.design.package --check <copy>

`--out` copies the latest version into an empty folder, cleaned as `reframe publish` cleans its copy. `--check` lists the
placeholders left in a copy, and its draft capabilities.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from reframe.design import golden, golden_compare, handover
from reframe.design.specs import SPECS, STACK

FOLDER = "outputs/design/package"
IDS = "design/package-ids.json"
FORMS = ("kiro", "claude-code", "spec-kit")
SMALL = {"and", "of", "the", "for", "to", "a", "an", "in", "on", "with"}
SENSITIVE = {"money": "money", "card_data": "card data", "personal_data": "personal data", "security": "security", "audit": "audit"}

ROLES = {
    "business-rules": ("Business rules", "Implements the capability's business rules and requirements exactly as specified, in the service layer.",
                       ["Implement each rule as stated; a business rule is kept exactly, a formatting rule may be restyled, error handling keeps its messages word for word.",
                        "Never carry a historical artifact without asking: say which rule and why it may no longer be wanted.",
                        "Name each rule's ID in a comment where it is implemented, and each requirement's ID in the test that checks it.",
                        "Do not decide a rule again: an open question or a decision table gap goes to the owner, not into code."]),
    "ui-modernization": ("UI modernization", "Builds the modern screens to the prototype in ui-reference/ and the screen specifications.",
                         ["Build to the prototype and the components listed; do not recreate 3270 habits (typed option codes, paging keys, one error at a time).",
                          "Every validation shows the rule's own message; every input has a visible label; meet WCAG 2.2 AA.",
                          "Keep keyboard-heavy work fast: the shortcuts the screen specification gives, and a logical tab order."]),
    "data": ("Data", "Models the data contracts on the target stack: tables, entities and their types.",
             ["Keep every field's precision: a packed decimal with 2 fraction digits is an exact decimal, never a float.",
              "Use synthetic data only; never copy production or personal data.",
              "Keep the copybook and field names in a comment or mapping, so the legacy data can be migrated and compared."]),
    "api": ("API", "Builds the services and scheduled jobs the capability runs as, from the design notes.",
            ["Follow each design note; the mainframe mechanics (VSAM, CICS, JCL, COMMAREA) are not to be copied.",
             "A capability that runs as a scheduled job becomes a job with the same inputs, outputs and failure handling.",
             "Name the rules and requirements each endpoint or job implements."]),
    "test-generation": ("Test generation", "Writes an automated test for every acceptance criterion, named by its ID.",
                        ["One test per acceptance criterion, named by its ID, asserting the Then exactly (messages word for word).",
                         "Run the capability's golden dataset (golden/<capability>.json): each case's input through its target, the answers compared with "
                         "golden/compare.py. A formatting difference is reported apart from a business one; a business difference fails.",
                         "A criterion that cannot be tested as written is reported, not changed."]),
    "compliance": ("Compliance", "Reviews capabilities that touch money, card data, personal data, security or audit.",
                   ["Check card numbers, personal data and credentials are protected in storage, logs and screens.",
                    "Check money is exact (decimal, never float) and every change to it is audited as the rules say.",
                    "Report what does not meet the rules; do not change the rules."]),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def slug(text: str, length: int = 50) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:length].strip("-") or "x"


def md(text: Any) -> str:
    """Text for a Markdown table cell or line."""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).replace("|", "\\|").strip()


# ---- inputs and IDs ----------------------------------------------------------------------------------------------------

def load_specs(folder) -> list[dict[str, Any]]:
    found = [json.loads(p.read_text()) for p in sorted(folder.path(SPECS).glob("*.json"))]
    return sorted(found, key=lambda s: (s["domain"], s["function_name"], s["number"]))


def _code(name: str, taken: set[str]) -> str:
    words = [w for w in re.findall(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])", name) if w.lower() not in SMALL]
    base = "".join(w[0] for w in words).upper()[:5] or "FN"
    if len(base) < 2:
        base = (words[0][:3] if words else "FN").upper()
    code, n = base, 2
    while code in taken:
        code, n = f"{base}{n}", n + 1
    return code


def ids(specs: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    """Function codes and rule numbers, kept from earlier versions; new ones are added after them."""
    functions = dict(registry.get("functions") or {})
    rules = dict(registry.get("rules") or {})
    for s in specs:
        if s["function"] not in functions:
            functions[s["function"]] = _code(s["function_name"], set(functions.values()))
    used: dict[str, int] = defaultdict(int)
    for rid in rules.values():
        code, _, n = rid.rpartition("-BR-")
        used[code] = max(used[code], int(n))
    for s in specs:
        code = functions[s["function"]]
        for r in s["rules"]:
            if r["key"] not in rules:
                used[code] += 1
                rules[r["key"]] = f"{code}-BR-{used[code]:03d}"
    return {"functions": functions, "rules": rules}


def label(s: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """A specification with its package IDs."""
    code = registry["functions"][s["function"]]
    cap = f"{code}-C{s['number']:02d}"
    req = {q["code"]: f"{code}-{q['code']}" for q in s["requirements"]}
    return {**s, "code": code, "id": cap, "folder": f"{code.lower()}-{s['number']:02d}-{slug(s['title'], 50)}",
            "req_ids": req, "rule_ids": {r["key"]: registry["rules"][r["key"]] for r in s["rules"]},
            "ac_ids": {x["id"]: f"{cap}-{x['id']}" for x in s["acceptance"]}}


# ---- tasks -------------------------------------------------------------------------------------------------------------

def tasks(s: dict[str, Any], dataset: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build tasks for a capability, each for a role and citing requirements and rules; every requirement is in one of
    the implementing tasks (business rules, errors and messages, services)."""
    reqs, rid = s["requirements"], s["rule_ids"]
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in reqs:
        by_class[q["class"]].append(q)
    rules_of = lambda qs: sorted({rid[k] for q in qs for k in q["rules"] if k in rid})  # noqa: E731
    found: list[dict[str, Any]] = []

    def add(role: str, title: str, qs: list[dict[str, Any]], details: list[str], depends: list[str], rules: list[str] | None = None, **extra) -> None:
        if not qs:
            return
        found.append({"id": f"{s['id']}-T{len(found) + 1}", "role": role, "title": title, "details": details,
                      "requirements": [s["req_ids"][q["code"]] for q in qs], "rules": rules if rules is not None else rules_of(qs),
                      "depends": depends, **extra})

    records = [b["copybook"] for b in s["contracts"] if b["role"] == "file record"]
    working = [b["copybook"] for b in s["contracts"] if b["role"] != "file record"]
    if s["contracts"]:
        add("data", "Model the data the capability reads and writes", reqs,
            ([f"File records: {', '.join(records)} (see data-contracts.md)."] if records else [])
            + ([f"Working records it passes or builds: {', '.join(working)}."] if working else []), [], rules=[], contracts=records + working)
    first = [found[0]["id"]] if found else []
    core = by_class["business rule"] + by_class["side effect"] + by_class["historical artifact"]
    add("business-rules", "Implement the business rules", core,
        ["Keep each business rule exactly as specified."]
        + (["Ask the owner before carrying the historical artifacts: " + ", ".join(rules_of(by_class["historical artifact"])) + "."] if by_class["historical artifact"] else []),
        first)
    add("business-rules", "Implement error handling and messages", by_class["error handling"] + by_class["formatting"],
        ["Show every message word for word; formatting rules may be restyled to the design system."], first)
    shape = {"online": "the services behind the screens", "scheduled job": "the scheduled job", "online and scheduled job": "the services and the scheduled job",
             "service": "the service"}[s["runs_as"]]
    notes = [f"{rid.get(n['rule'], n['rule'])}: {n['note']}" for n in s["design_notes"]]
    add("api", f"Build {shape}", by_class["design note"] or (reqs if s["runs_as"] != "online" and not s["screens"] else []), notes or ["Expose the rules as the service the screens call."],
        [t["id"] for t in found if t["role"] in ("data", "business-rules")])
    service = [t["id"] for t in found if t["role"] in ("api", "business-rules")]
    for x in s["screens"]:
        served = {k for c in x["components"] for k in c.get("requirements") or []}
        qs = [q for q in reqs if q["key"] in served] or [q for q in reqs if set(q["rules"]) & {k for c in x["components"] for k in c.get("rules") or []}]
        add("ui-modernization", f"Build the screen {x['title']} ({x['id']})", qs,
            [f"To the prototype: ui-reference/index.html#/{x['id']}."] + [f"{c.get('type', '')} \"{c.get('label', '')}\"" + (f": {c['validation']}" if c.get("validation") else "")
                                                                         for c in x["components"]],
            service, screen=x["id"])
    cases = (dataset or {}).get("cases") or []
    add("test-generation", "Write a test for every acceptance criterion" + (", and run the golden dataset" if cases else ""), reqs,
        [f"{len(s['acceptance'])} acceptance criteria, each a test named by its ID."]
        + ([f"Golden dataset golden/{s['id']}.json: {len(cases)} cases, {sum(1 for c in cases if c['edge'])} of them edge cases, compared with "
            "golden/compare.py. The expected outputs come from the rules until legacy outputs are captured."] if cases else []),
        [t["id"] for t in found], rules=[], criteria=[s["ac_ids"][x["id"]] for x in s["acceptance"]], golden=[c["id"] for c in cases])
    touched = [SENSITIVE[t] for t in s.get("touches") or [] if t in SENSITIVE]
    if touched:
        add("compliance", f"Review the handling of {', '.join(touched)}", reqs, ["Check storage, logs, screens and audit against the rules."],
            [t["id"] for t in found if t["role"] != "test-generation"], rules=[])
    return found


# ---- shared parts ------------------------------------------------------------------------------------------------------

def status_line(s: dict[str, Any]) -> str:
    if s["status"] == "final":
        who = "; ".join(f"{x.get('reviewer') or 'a reviewer'}, {str(x.get('at', ''))[:10]}" for x in s["signed_off"])
        return f"**Status: FINAL**, signed off by {who}."
    return "**Status: DRAFT**: the function is not signed off yet. Confirm with the owner before building to it."


def criterion(s: dict[str, Any], x: dict[str, Any]) -> str:
    rules = ", ".join(s["rule_ids"].get(k, k) for k in x["rules"])
    return f"GIVEN {md(x['given'])} WHEN {md(x['when'])} THEN {md(x['then'])} ({s['ac_ids'][x['id']]}{'; ' + rules if rules else ''})"


def rules_table(s: dict[str, Any]) -> str:
    rows = ["| ID | Name | Class | Rule | Confidence | Evidence |", "|---|---|---|---|---|---|"]
    rows += [f"| {s['rule_ids'][r['key']]} | {md(r['name'])} | {r['class']} | {md(r['text'])} | {r['confidence']} | {md(', '.join(r['evidence'][:4]))} |" for r in s["rules"]]
    return "\n".join(rows)


def examples(s: dict[str, Any]) -> str:
    lines = []
    for r in s["rules"]:
        for x in r["examples"]:
            given = "; ".join(f"{g.get('field', '')} = {g.get('value', '')}" for g in x.get("given") or []) or "no particular input"
            lines.append(f"- {s['rule_ids'][r['key']]}: {md(given)} → {md(x.get('then', ''))}")
    return "\n".join(lines) or "None."


def screens_md(s: dict[str, Any]) -> str:
    if not s["screens"]:
        return f"No screens: it runs as {s['runs_as']}."
    out = []
    for x in s["screens"]:
        out.append(f"### {md(x['title'])} ({x['id']})\n\n{md(x['purpose'])}\n\nPrototype: `ui-reference/index.html#/{x['id']}`."
                   + (f" Replaces the legacy screens {', '.join(x['replaces'])}." if x["replaces"] else "") + "\n")
        out.append("| Component | Type | Shows | Checks | Goes to | Requirements |\n|---|---|---|---|---|---|")
        for c in x["components"]:
            reqs = ", ".join(s["req_ids"].get(k.rpartition("/")[2], "") for k in c.get("requirements") or [] if k.rpartition("/")[2] in s["req_ids"])
            out.append(f"| {md(c.get('label', ''))} | {c.get('type', '')} | {md(c.get('shows', ''))} | {md(c.get('validation', ''))} | {c.get('goes_to', '')} | {reqs} |")
        out.append("")
    return "\n".join(out)


def contracts_md(s: dict[str, Any], books: dict[str, Any]) -> str:
    out = []
    for b in s["contracts"]:
        c = books.get(b["copybook"])
        if not c:
            continue
        if b["role"] != "file record":
            out.append(f"- {b['copybook']} (working storage of {', '.join(b['programs'])}): see data-contracts.md.")
            continue
        out.append(contract_md(c, f"File record of {', '.join(b['programs'])}."))
    return "\n".join(out) or "None found in the source."


def contract_md(c: dict[str, Any], note: str = "") -> str:
    out = [f"#### {c['copybook']}\n\n{note} Source: `{c['file']}`.\n" if note else f"#### {c['copybook']}\n\nSource: `{c['file']}`.\n"]
    for r in c["records"]:
        out.append(f"Record `{r['name']}`:\n\n| Field | In | PIC | Type | Code type | Column type | Values |\n|---|---|---|---|---|---|---|")
        for f in r["fields"]:
            extra = []
            if f.get("occurs"):
                extra.append(f"occurs {f['occurs']}")
            if f.get("redefines"):
                extra.append(f"redefines {f['redefines']}")
            extra += [f"{x['name']} = {x['value']}" for x in f.get("conditions") or []]
            out.append(f"| {f['name']} | {f['group']} | {md(f['pic'] + (' ' + f['usage'] if f['usage'] else ''))} | {md(f['says'])} | "
                       f"{md((f.get('stack') or {}).get('code', ''))} | {md((f.get('stack') or {}).get('column', ''))} | {md('; '.join(extra))} |")
        out.append("")
    return "\n".join(out)


def tables_md(s: dict[str, Any]) -> str:
    out = []
    for t in s["tables"]:
        conds = list(t.get("conditions") or [])
        out.append(f"#### {md(t.get('title', ''))}\n\n| {' | '.join(md(c) for c in conds)} | {md(t.get('outcome') or 'Outcome')} | Rule |\n|{'---|' * (len(conds) + 2)}")
        for r in t.get("rows") or []:
            out.append(f"| {' | '.join(md(v) for v in r.get('values') or [])} | {md(r.get('outcome', ''))} | {s['rule_ids'].get(r.get('rule', ''), '')} |")
        for g in t.get("gaps") or []:
            out.append(f"| {' | '.join(md(v) for v in g.get('values') or [])} | **Gap:** {md(g.get('note', ''))} | |")
        out.append("")
    return "\n".join(out) or "None."


def questions_md(s: dict[str, Any]) -> str:
    return "\n".join(f"- {q['id']}: {md(q['question'])}" + (f" ({md(q['owner'])})" if q["owner"] else "")
                     + (f" On {', '.join(s['rule_ids'].get(k, k) for k in q['rules'])}." if q["rules"] else "") for q in s["questions"]) or "None."


def notes_md(s: dict[str, Any]) -> str:
    return "\n".join(f"- {s['rule_ids'].get(n['rule'], n['rule'])}: {md(n['note'])}" for n in s["design_notes"]) or "None."


def task_lines(s: dict[str, Any], ts: list[dict[str, Any]], checkbox: str = "- [ ]", numbered: bool = False) -> list[str]:
    out = []
    for i, t in enumerate(ts, 1):
        head = f"{checkbox} {i}. " if numbered else f"{checkbox} "
        out.append(f"{head}{t['id']} [{t['role']}] {md(t['title'])}")
        out += [f"  - {md(d)}" for d in t["details"]]
        out.append(f"  - _Requirements: {', '.join(t['requirements'])}_")
        if t["rules"]:
            out.append(f"  - _Rules: {', '.join(t['rules'])}_")
        if t.get("criteria"):
            out.append(f"  - _Criteria: {', '.join(t['criteria'])}_")
        if t.get("golden"):
            out.append(f"  - _Golden cases: {', '.join(t['golden'])}_")
        if t["depends"]:
            out.append(f"  - _After: {', '.join(t['depends'])}_")
    return out


def stack_md(stack: dict[str, Any] | None, architecture: str) -> str:
    return handover.stack_section(stack, architecture)


# ---- the forms ---------------------------------------------------------------------------------------------------------

def requirements_md(s: dict[str, Any]) -> str:
    out = [f"# Requirements: {s['title']} ({s['id']})", "", status_line(s), "",
           f"{s['domain']} · {s['function_name']} · runs as {s['runs_as']} · risk {s['risk'] or 'not rated'}", "",
           "## Introduction", "", md(s["summary"]) or md(s["story"]), "", f"**User story:** {md(s['story'])}", "", "## Requirements", ""]
    for i, q in enumerate(s["requirements"], 1):
        rid = s["req_ids"][q["code"]]
        out += [f"### Requirement {i}: {rid} ({q['class']})", "", "#### Acceptance criteria", "",
                f"1. {md(q['text'])} (rules {', '.join(s['rule_ids'].get(k, k) for k in q['rules'])})"]
        mine = [x for x in s["acceptance"] if q["code"] in x["requirements"]]
        out += [f"{n}. {criterion(s, x)}" for n, x in enumerate(mine, 2)]
        out.append("")
    return "\n".join(out)


def design_md(s: dict[str, Any], books: dict[str, Any]) -> str:
    return "\n".join([
        f"# Design: {s['title']} ({s['id']})", "", status_line(s), "", "## Overview", "", md(s["summary"]) or md(s["story"]), "",
        f"Runs as {s['runs_as']}." + (f" Journeys: {', '.join(s['journeys'])}." if s["journeys"] else ""), "",
        "## Screens and components", "", screens_md(s), "", "## Data models", "", contracts_md(s, books), "",
        "## Business rules", "", "Classes: a *business rule* is kept exactly; *formatting* may be restyled; *error handling* keeps its messages; "
        "a *side effect* is kept; a *historical artifact* is carried only after asking the owner; a *design note* is for the build, below.", "",
        rules_table(s), "", "### Worked examples", "", examples(s), "", "## Decision tables", "", tables_md(s), "",
        "## Design notes", "", notes_md(s), "", "## Open questions", "", questions_md(s), "",
        "## Testing", "", f"Every acceptance criterion in requirements.md is a test named by its ID ({len(s['acceptance'])} in all)."])


def tasks_md(s: dict[str, Any], ts: list[dict[str, Any]]) -> str:
    return "\n".join([f"# Implementation plan: {s['title']} ({s['id']})", "", status_line(s), ""] + task_lines(s, ts, numbered=True)) + "\n"


def spec_md(s: dict[str, Any], books: dict[str, Any]) -> str:
    """The whole specification in one file, for the Claude Code form."""
    body = requirements_md(s).split("\n", 1)[1]
    rest = design_md(s, books).split("## Screens and components", 1)[1]
    return f"# {s['title']} ({s['id']})\n{body}\n## Screens and components{rest}\n"


def speckit_spec(s: dict[str, Any]) -> str:
    out = [f"# Feature specification: {s['title']}", "", f"**Feature:** {s['id']} · {s['function_name']} · {s['domain']}", "", status_line(s), "",
           "## User scenarios and testing", "", f"### User story ({s['id']})", "", md(s["story"]), "", md(s["summary"]), "", "**Acceptance scenarios:**", ""]
    out += [f"{n}. **Given** {md(x['given'])}, **when** {md(x['when'])}, **then** {md(x['then'])} ({s['ac_ids'][x['id']]})" for n, x in enumerate(s["acceptance"], 1)]
    out += ["", "## Requirements", "", "### Functional requirements", ""]
    out += [f"- **{s['req_ids'][q['code']]}** ({q['class']}): {md(q['text'])} Rules: {', '.join(s['rule_ids'].get(k, k) for k in q['rules'])}." for q in s["requirements"]]
    out += ["", "### Business rules", "", rules_table(s), "", "### Key entities", ""]
    out += [f"- {b['copybook']} ({b['role']})" for b in s["contracts"]] or ["None found in the source."]
    out += ["", "### Needs clarification", "", questions_md(s), "", "### Decision table gaps", ""]
    out += [f"- {md(t.get('title', ''))}: {md(g.get('note', ''))}" for t in s["tables"] for g in t.get("gaps") or []] or ["None."]
    return "\n".join(out) + "\n"


def speckit_plan(s: dict[str, Any], books: dict[str, Any], stack: dict[str, Any] | None) -> str:
    return "\n".join([
        f"# Implementation plan: {s['title']} ({s['id']})", "", status_line(s), "", "## Technical context", "",
        "As in the constitution's *Technical context* (.specify/memory/constitution.md)" + (f": {stack['stack']}." if stack else "; the target stack is not given yet."), "",
        f"Runs as {s['runs_as']}.", "", "## Constitution check", "", "- The legacy source is read-only; data is synthetic only.",
        "- Every requirement, rule and criterion keeps its ID in code and tests.", f"- This capability is {s['status']}.", "",
        "## Screens", "", screens_md(s), "", "## Data model", "", contracts_md(s, books), "", "## Design notes", "", notes_md(s), "",
        "## Decision tables", "", tables_md(s)]) + "\n"


def speckit_tasks(s: dict[str, Any], ts: list[dict[str, Any]]) -> str:
    out = [f"# Tasks: {s['title']} ({s['id']})", "", status_line(s), "", "Each task names the role that does it; the role files are in .claude/agents/.", ""]
    for t in ts:
        out.append(f"- [ ] {t['id']} [{t['role']}] {md(t['title'])}. Requirements: {', '.join(t['requirements'])}"
                   + (f". Rules: {', '.join(t['rules'])}" if t["rules"] else "") + (f". Criteria: {', '.join(t['criteria'])}" if t.get("criteria") else "")
                   + (f". Golden cases: {', '.join(t['golden'])}" if t.get("golden") else "")
                   + (f". After: {', '.join(t['depends'])}" if t["depends"] else "") + ".")
        out += [f"  - {md(d)}" for d in t["details"]]
    return "\n".join(out) + "\n"


def role_md(role: str) -> str:
    name, what, rules = ROLES[role]
    return "\n".join(["---", f"name: {role}", f"description: {what}", "---", "", f"You are the {name.lower()} role in the forward build of a modernized mainframe application.",
                      "", what, "", "## Template", "", "For each task you take: read the task's requirements, rules and criteria by ID in the specification; "
                      "do the work; name the IDs you implemented; list what you could not do and why.", "", "## Guardrails", ""]
                     + [f"- {r}" for r in rules] + ["- The legacy source is read-only. Use synthetic data only.", "- A draft capability is confirmed with the owner before it is built.", ""]
                     + handover.role_fill(role))


def product_md(specs: list[dict[str, Any]], journeys: list[dict[str, Any]], project: str, personas: str, decisions: str) -> str:
    out = [f"# Product: {project}, modernized", "", "The modernized application, designed by Reframe from the legacy source's reconciled requirements and rules. "
           "Signed-off functions are final; the rest are draft.", "", "## Functions", ""]
    fns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in specs:
        fns[f"{s['domain']}: {s['function_name']} ({s['code']})"].append(s)
    for name, caps in fns.items():
        out.append(f"- **{name}**, {caps[0]['status']}: {len(caps)} capabilities")
    out += ["", "## Journeys", ""] + [f"- {j['id']} {md(j['name'])}: {md(j.get('goal', ''))}" for j in journeys]
    if personas.strip():
        out += ["", "## Personas", "", personas.strip()]
    if decisions.strip():
        out += ["", "## Decisions", "", decisions.strip()]
    return "\n".join(out) + "\n"


def conventions() -> list[str]:
    return ["- Keep every ID: name the requirement and rule IDs in a comment where they are implemented, and each acceptance criterion's ID in its test.",
            "- A business rule is kept exactly; formatting may be restyled; error handling keeps its messages word for word; ask before carrying a historical artifact.",
            "- The legacy source is read-only and only for reference. Use synthetic data only, never production or personal data.",
            "- Build a draft capability only after the owner confirms it; build final ones first.",
            "- Money is an exact decimal, never a float; keep each field's precision from the data contracts.",
            "- The screens follow the prototype in the Claude Code form's ui-reference/ and the screen specifications; meet WCAG 2.2 AA.",
            "- A capability is done when its acceptance criteria pass and its golden dataset in golden/ shows no business difference (golden/README.md)."]


def structure_md(specs: list[dict[str, Any]]) -> str:
    domains = sorted({s["domain"] for s in specs})
    return "\n".join(["# Structure", "", "One module per business domain, each with its services, screens and tests; shared parts (the application shell, "
                      "authentication, data access) in a common module.", ""] + [f"- `{slug(d, 40)}/`: {d}" for d in domains]
                     + ["", "Tests mirror the acceptance criteria: one test per criterion, named by its ID.", "", "## Conventions", ""] + conventions()) + "\n"


def traceability_md(specs: list[dict[str, Any]], ts: dict[str, list[dict[str, Any]]]) -> str:
    out = ["# Traceability", "", "Each ID in this package, back to the reconciled run and the legacy source.", "",
           "## Requirements", "", "| ID | Capability | Run key | Rules | Criteria | Tasks |", "|---|---|---|---|---|---|"]
    for s in specs:
        task_of: dict[str, list[str]] = defaultdict(list)
        for t in ts[s["id"]]:
            for r in t["requirements"]:
                task_of[r].append(t["id"])
        for q in s["requirements"]:
            rid = s["req_ids"][q["code"]]
            out.append(f"| {rid} | {s['id']} | `{q['key']}` | {', '.join(s['rule_ids'].get(k, k) for k in q['rules'])} | "
                       f"{', '.join(s['ac_ids'][c] for c in q['criteria'])} | {', '.join(task_of[rid])} |")
    out += ["", "## Rules", "", "| ID | Program | Run key | Evidence |", "|---|---|---|---|"]
    seen = set()
    for s in specs:
        for r in s["rules"]:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            out.append(f"| {s['rule_ids'][r['key']]} | {r['program']} | `{r['key']}` | {md(', '.join(r['evidence']))} |")
    out += ["", "## Capabilities", "", "| ID | Title | Status | Run key |", "|---|---|---|---|"]
    out += [f"| {s['id']} | {md(s['title'])} | {s['status']} | `{s['key']}` |" for s in specs]
    tests = [(c, t["id"]) for s in specs for t in ts[s["id"]] for c in t.get("golden") or []]
    if tests:
        out += ["", "## Golden cases", "", "Each case is in `golden/<capability>.json`, with its input, expected output and legacy source.", "",
                "| ID | Run by task |", "|---|---|"] + [f"| {c} | {t} |" for c, t in tests]
    return "\n".join(out) + "\n"


def write(root: Path, specs: list[dict[str, Any]], books: dict[str, Any], stack: dict[str, Any] | None, journeys: list[dict[str, Any]],
          prototype: Path | None, project: str, context: dict[str, str], programs: dict[str, list[str]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Every form of the package under root. Returns the tasks by capability."""
    datasets = {s["id"]: golden.dataset(s) for s in specs}
    ts = {s["id"]: tasks(s, datasets[s["id"]]) for s in specs}
    contracts = "# Data contracts\n\nThe copybooks the capabilities' programs copy, as typed records, read from the legacy source.\n\n" + \
                "\n".join(contract_md(c) for c in books.values())
    trace = traceability_md(specs, ts)
    product = product_md(specs, journeys, project, context.get("personas", ""), context.get("decisions", ""))
    tech = "# Tech\n\n## Target stack\n\n" + stack_md(stack, context.get("architecture", "")) + "\n"
    files: dict[str, str] = {}
    known = handover.facts(specs, journeys, programs or {})
    files["START-HERE.md"] = handover.start_here(known, project, stack, None)
    kept = [d for d in datasets.values() if d["cases"] or d["uncovered"]]
    comparer = Path(golden_compare.__file__).read_text()
    for form in FORMS:
        files[f"{form}/START-HERE.md"] = handover.start_here(known, project, stack, form)
        files[f"{form}/data-contracts.md"] = contracts
        files[f"{form}/traceability.md"] = trace
        files[f"{form}/golden/README.md"] = golden.readme(kept)
        files[f"{form}/golden/compare.py"] = comparer
        for d in kept:
            files[f"{form}/golden/{d['capability']}.json"] = json.dumps(d, indent=1) + "\n"
    house = "# House conventions\n\nWhat your team builds to. Reframe cannot know these; fill them in.\n\n" + handover.house(context.get("style", "")) + "\n\n## From the design\n\n" + \
        "\n".join(conventions()) + "\n"
    for name in handover.SKILLS:
        files[f"claude-code/.claude/skills/{name}/SKILL.md"] = handover.skill(name, "specs/<capability>.md", "tasks.md")
        files[f"spec-kit/.claude/skills/{name}/SKILL.md"] = handover.skill(name, "specs/<nnn-capability>/spec.md and plan.md", "specs/<nnn-capability>/tasks.md")
    # Kiro
    steer = "---\ninclusion: always\n---\n\n"
    files["kiro/.kiro/steering/product.md"] = steer + product
    files["kiro/.kiro/steering/tech.md"] = steer + tech
    files["kiro/.kiro/steering/structure.md"] = steer + structure_md(specs)
    files["kiro/.kiro/steering/house-conventions.md"] = steer + house
    for s in specs:
        base = f"kiro/.kiro/specs/{s['folder']}"
        files[f"{base}/requirements.md"] = requirements_md(s)
        files[f"{base}/design.md"] = design_md(s, books)
        files[f"{base}/tasks.md"] = tasks_md(s, ts[s["id"]])
    # Claude Code
    index = "\n".join(f"- [{s['id']} {md(s['title'])}](specs/{s['folder']}.md): {s['status']}, {s['runs_as']}" for s in specs)
    files["claude-code/CLAUDE.md"] = "\n".join([
        f"# {project}, modernized", "", "Build the modernized application from the specifications here. Each capability's specification is in `specs/`; "
        "the build tasks, each for a role, are in `tasks.md`; the prototype in `ui-reference/index.html` is the UI to build to; the data contracts are in "
        "`data-contracts.md`; `traceability.md` takes every ID back to the legacy source.", "", "## Target stack", "", stack_md(stack, context.get("architecture", "")), "",
        "## Conventions", ""] + conventions() + ["", "## House conventions", "", handover.house(context.get("style", "")), "",
        "## Skills", "", "In `.claude/skills/`: " + ", ".join(f"`{n}`" for n in handover.SKILLS) + ". Fill in their placeholders first; START-HERE.md says how.", "",
        "## Capabilities", "", index, ""])
    files["claude-code/tasks.md"] = "# Tasks\n\nBuild final capabilities first. Each task names its role and the IDs it implements.\n\n" + "\n\n".join(
        f"## {s['id']} {md(s['title'])} ({s['status']})\n\n" + "\n".join(task_lines(s, ts[s["id"]])) for s in specs) + "\n"
    for s in specs:
        files[f"claude-code/specs/{s['folder']}.md"] = spec_md(s, books)
    files["claude-code/ui-reference/README.md"] = ("# UI reference\n\n`index.html` is Reframe's clickable prototype of the modern screens: open it in a browser. "
                                                   "Each screen is a route (`#/<screen id>`); *Show the legacy screen* shows the 3270 screen it replaces.\n")
    # Spec Kit
    files["spec-kit/.specify/memory/constitution.md"] = "\n".join([
        f"# {project} modernization constitution", "", "## Non-negotiables", "",
        "1. The legacy source is read-only; it is the reference, never changed.",
        "2. Data is synthetic only; no production or personal data in code, tests or logs.",
        "3. Every requirement, rule, acceptance criterion and task keeps its ID, so the build stays traceable to the legacy source (traceability.md).",
        "4. The target stack is the engineer's: " + (f"{stack['stack']}." if stack else "not given yet; nothing that depends on it is built until it is."),
        "5. Final capabilities (signed off) are built to as written; draft ones are confirmed with the owner first.",
        "6. Order of replacement: final capabilities first; the risk-ranked order (P4-12) will replace this when it exists.",
        "7. A business rule is kept exactly; a historical artifact is carried only after asking.", "", "## Roles", ""]
        + [f"- `{r}`: {ROLES[r][1]}" for r in ROLES] + ["", "## Technical context", "", handover.technical_context(stack), "",
                                                         "## House conventions", "", handover.house(context.get("style", "")), "", "## Conventions", ""] + conventions()) + "\n"
    for role in ROLES:
        files[f"spec-kit/.claude/agents/{role}.md"] = role_md(role)
    for n, s in enumerate(specs, 1):
        base = f"spec-kit/specs/{n:03d}-{s['folder']}"
        files[f"{base}/spec.md"] = speckit_spec(s)
        files[f"{base}/plan.md"] = speckit_plan(s, books, stack)
        files[f"{base}/tasks.md"] = speckit_tasks(s, ts[s["id"]])
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    if prototype and prototype.is_file():
        shutil.copyfile(prototype, root / "claude-code/ui-reference/index.html")
    return ts


# ---- checks, versions --------------------------------------------------------------------------------------------------

def check(root: Path, specs: list[dict[str, Any]], ts: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Every ID of every specification is in every form's own files; every task cites a requirement; every requirement is in an
    implementing task; every draft is marked."""
    found = []
    token = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+")
    shared = ("traceability.md", "data-contracts.md")  # they name every ID; each must also be in the form's own files
    seen = {form: {t for p in (root / form).rglob("*.md") if p.name not in shared for t in token.findall(p.read_text())} for form in FORMS}
    for s in specs:
        cases = [c for t in ts[s["id"]] for c in t.get("golden") or []]
        wanted = list(s["req_ids"].values()) + list(s["rule_ids"].values()) + list(s["ac_ids"].values()) + [t["id"] for t in ts[s["id"]]] + cases
        for form in FORMS:
            missing = [i for i in wanted if i not in seen[form]]
            if missing:
                found.append(f"{form}: {s['id']} is missing {', '.join(missing[:6])}" + (f" and {len(missing) - 6} more" if len(missing) > 6 else ""))
            if cases:  # the golden dataset holds exactly the cases its task runs
                path = root / form / "golden" / f"{s['id']}.json"
                held = [c["id"] for c in json.loads(path.read_text())["cases"]] if path.is_file() else None
                if held != cases:
                    found.append(f"{form}: golden/{s['id']}.json " + ("is missing" if held is None else "does not hold the cases its task runs"))
        implemented = {r for t in ts[s["id"]] if t["role"] in ("business-rules", "api", "ui-modernization") for r in t["requirements"]}
        found += [f"{s['id']}: requirement {r} is in no implementing task" for r in s["req_ids"].values() if r not in implemented]
        found += [f"{t['id']}: cites no requirement" for t in ts[s["id"]] if not t["requirements"]]
        if s["status"] == "draft":
            for path in (root / "kiro/.kiro/specs" / s["folder"]).glob("*.md"):
                if "Status: DRAFT" not in path.read_text():
                    found.append(f"kiro: {s['id']} {path.name} is not marked draft")
    return found


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file() and x.name not in ("manifest.json", "CHANGES.md")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def manifest(specs: list[dict[str, Any]], ts: dict[str, list[dict[str, Any]]], stack: dict[str, Any] | None) -> dict[str, Any]:
    return {"stack": (stack or {}).get("stack"), "capabilities": {s["id"]: {
        "title": s["title"], "status": s["status"],
        "requirements": {s["req_ids"][q["code"]]: _hash([q["text"], q["rules"]]) for q in s["requirements"]},
        "rules": {s["rule_ids"][r["key"]]: _hash([r["text"], r["class"]]) for r in s["rules"]},
        "criteria": {s["ac_ids"][x["id"]]: _hash([x["given"], x["when"], x["then"]]) for x in s["acceptance"]},
        "golden": {c["id"]: _hash([c["input"], c["expected"], c["target"]]) for c in golden.dataset(s)["cases"]},
        "tasks": [t["id"] for t in ts[s["id"]]], "hash": _hash(s)} for s in specs}}


def changes(before: dict[str, Any] | None, now: dict[str, Any], version: int, notes: str = "") -> str:
    """What changed since the last version, by ID; `notes` is the design round's own account, added after it."""
    tail = f"\n## The design round\n\n{notes.strip()}\n" if notes.strip() else ""
    if not before:
        return f"# Changes in v{version}\n\nThe first version: {len(now['capabilities'])} capabilities.\n" + tail
    out = [f"# Changes in v{version}", "", f"Since v{before.get('version')}.", ""]
    if before.get("stack") != now.get("stack"):
        out.append(f"- The target stack: {before.get('stack') or 'not given'} → {now.get('stack') or 'not given'}.")
    old, new = before.get("capabilities") or {}, now["capabilities"]
    had_golden = any("golden" in c for c in old.values())  # a version from before P4-11 has none: say so once, not per capability
    if not had_golden and any(c.get("golden") for c in new.values()):
        out.append(f"- Golden datasets added (P4-11): {sum(len(c.get('golden') or {}) for c in new.values())} cases for "
                   f"{sum(1 for c in new.values() if c.get('golden'))} capabilities, in golden/.")
    out += [f"- Added {k}: {new[k]['title']} ({new[k]['status']})." for k in new if k not in old]
    out += [f"- Removed {k}: {old[k]['title']}." for k in old if k not in new]
    for k in new:
        if k not in old or (old[k].get("hash") == new[k]["hash"] and (not had_golden or old[k].get("golden") == new[k].get("golden"))):
            continue
        line = []
        if old[k]["status"] != new[k]["status"]:
            line.append(f"now {new[k]['status']} (was {old[k]['status']})")
        for part in ("requirements", "rules", "criteria", "golden") if had_golden else ("requirements", "rules", "criteria"):
            a, b = old[k].get(part) or {}, new[k].get(part) or {}
            added, removed = [i for i in b if i not in a], [i for i in a if i not in b]
            changed = [i for i in b if i in a and a[i] != b[i]]
            for word, items in (("added", added), ("removed", removed), ("changed", changed)):
                if items:
                    line.append(f"{part} {word}: {', '.join(items[:12])}" + (f" and {len(items) - 12} more" if len(items) > 12 else ""))
        out.append(f"- {k} {new[k]['title']}: " + ("; ".join(line) or "screens, contracts, notes or questions changed") + ".")
    if len(out) == 4:
        out.append("- Only the wording or layout of the files changed.")
    return "\n".join(out) + "\n" + tail


def versions(folder) -> list[int]:
    base = folder.path(FOLDER)
    return sorted(int(p.name[1:]) for p in base.glob("v*") if p.is_dir() and p.name[1:].isdigit()) if base.is_dir() else []


def read_context(context: Path | None) -> dict[str, str]:
    found = {}
    for key, name in (("architecture", "target-architecture.md"), ("personas", "personas.md"), ("decisions", "decisions.md")):
        path = context / name if context else None
        found[key] = path.read_text() if path and path.is_file() else ""
    style = context / "style-reference" if context else None
    found["style"] = ", ".join(p.relative_to(style).as_posix() for p in sorted(style.rglob("*")) if p.is_file()) if style and style.is_dir() else ""
    return found


def run(folder, context: Path | None = None, notes: str = "") -> dict[str, Any]:
    from reframe.core.runfolder import iso
    raw = load_specs(folder)
    if not raw:
        raise ValueError("write the final specifications first (python -m reframe.design.specs)")
    registry = ids(raw, folder.load(IDS, {}) or {})
    folder.save(IDS, registry)
    specs = [label(s, registry) for s in raw]
    books = (folder.load("design/contracts.json") or {}).get("contracts") or {}
    stack = folder.load(STACK) or None
    journeys = (folder.load("design/journeys.json") or {}).get("journeys") or []
    prototype = folder.path("outputs/design/prototype/index.html")
    project = folder.state.get("project") or "the application"
    have = versions(folder)
    latest = folder.path(f"{FOLDER}/v{have[-1]}") if have else None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "package"
        inventory = (folder.load("design/screens.json") or {}).get("screens") or []
        programs = {s["map"]: [p["name"] for p in s.get("programs") or []] for s in inventory}
        ts = write(root, specs, books, stack, journeys, prototype, project, read_context(context), programs)
        problems = check(root, specs, ts)
        made = manifest(specs, ts, stack)
        same = latest is not None and (latest / "manifest.json").is_file() and json.loads((latest / "manifest.json").read_text()).get("digest") == digest(root)
        if same and latest is not None:  # same already needs latest
            version, target = have[-1], latest
        else:
            version = (have[-1] + 1) if have else 1
            before = json.loads((latest / "manifest.json").read_text()) if latest and (latest / "manifest.json").is_file() else None
            (root / "CHANGES.md").write_text(changes(before, made, version, notes))
            (root / "manifest.json").write_text(json.dumps({**made, "version": version, "generated_at": iso(), "digest": digest(root),
                                                            "problems": problems}, indent=1))
            target = folder.path(f"{FOLDER}/v{version}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root, target)
    files = [p for p in target.rglob("*") if p.is_file()]
    return {"version": version, "new": not same, "path": str(target), "files": len(files), "problems": problems,
            "counts": {"capabilities": len(specs), "final": sum(1 for s in specs if s["status"] == "final"),
                       "tasks": sum(len(t) for t in ts.values()), "roles": {r: sum(1 for t in ts.values() for x in t if x["role"] == r) for r in ROLES},
                       "requirements": sum(len(s["req_ids"]) for s in specs), "rules": len(registry["rules"]),
                       "golden": {"cases": sum(len(t.get("golden") or []) for v in ts.values() for t in v),
                                  "datasets": sum(1 for v in ts.values() if any(t.get("golden") for t in v))}}}


def copy_out(folder, target: Path, form: str = "all") -> dict[str, Any]:
    """The latest version, cleaned as `reframe publish` cleans its copy, into target. Nothing is written if the cleaner
    leaves anything behind."""
    from reframe.report.publish import Cleaner
    have = versions(folder)
    if not have:
        raise ValueError("write the package first")
    source = folder.path(f"{FOLDER}/v{have[-1]}")
    if target.exists() and any(target.iterdir()):
        return {"written": 0, "left": [f"{target} is not empty: copy into an empty folder, so no filled-in file is overwritten"]}
    cleaner = Cleaner(folder)
    chosen = FORMS if form == "all" else (form,)
    planned, left = {}, []
    for p in sorted(source.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(source)
        if rel.parts[0] in FORMS and rel.parts[0] not in chosen:
            continue
        text = cleaner.clean(p.read_text(errors="replace"))
        left += [f"{rel}: {x}" for x in cleaner.left(text)]
        planned[rel] = text
    if left:
        return {"written": 0, "left": left}
    for rel, text in planned.items():
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return {"written": len(planned), "left": [], "version": have[-1], "path": str(target)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    from reframe.core.runfolder import find_run
    parser = argparse.ArgumentParser(prog="python -m reframe.design.package", description="The AI-DLC package (P4-08)")
    parser.add_argument("--run", help="the run (default: the latest run)")
    parser.add_argument("--context", help="a folder with target-architecture.md, personas.md and decisions.md")
    parser.add_argument("--out", help="copy the latest version, cleaned, into this folder")
    parser.add_argument("--form", choices=FORMS + ("all",), default="all")
    parser.add_argument("--check", help="list the placeholders left in a copy of the package, and its draft capabilities")
    args = parser.parse_args(argv)
    if args.check:
        found = handover.readiness(Path(args.check).expanduser())
        print(json.dumps({**found, "files": {k: v[:5] for k, v in list(found["files"].items())[:40]}}, indent=1))
        return 0 if found["ready"] else 1
    folder = find_run(args.run)
    value = run(folder, Path(args.context).expanduser() if args.context else None)
    if args.out:
        value["copy"] = copy_out(folder, Path(args.out).expanduser(), args.form)
    print(json.dumps({**value, "problems": value["problems"][:20], "problem_count": len(value["problems"])}, indent=1))
    return 0 if not value["problems"] and not (value.get("copy") or {}).get("left") else 1


if __name__ == "__main__":
    raise SystemExit(main())
