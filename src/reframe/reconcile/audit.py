"""Pipeline 3, part 2: the accuracy audit (docs/pipeline-3.md, section 8.1).

A sample of the recommended rules, drawn across the decision kinds, is judged against the source code by two headless
Claude Code steps that did not decide the rules and are not told how they were decided. Where the judges differ, the
owner settles the item in reconcile/audit/settle.csv. The figures go to reconcile/audit/audit.json.

    python -m reframe.reconcile.audit [--run <run>] [--size 60] [--seed 1] [--judge label=model ...]

Everything is kept in reconcile/audit/: running it again reuses the sample and every judgment already made.
A mainframe expert can check the same sample, blind: python -m reframe.reconcile.expert (section 8.13).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from reframe.core.runfolder import RunFolder, iso

FOLDER = "reconcile/audit"
JUDGES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5"}
# Items per kind in a sample of 60; None takes every rule of the kind.
QUOTA = {"agreed": 8, "merged": 20, "kept_aws": 12, "kept_claude": 8, "rewritten": 6, "claude": None, "aws": None, "rejected": None}
KINDS = {"agreed": "Agreed without an agent", "merged": "Merged", "kept_aws": "Kept, found only by AWS Transform",
         "kept_claude": "Kept, found only by Claude", "rewritten": "Rewritten from the code", "claude": "Claude's rule chosen",
         "aws": "AWS Transform's rule chosen", "rejected": "Rejected"}
BATCH = 6
PARALLEL = 4
VERDICTS = ("correct", "minor", "wrong", "unsure")
DEFECTS = ("real", "not_a_defect", "unsure")

SCHEMA: dict[str, Any] = {"type": "object", "required": ["judgments"], "properties": {"judgments": {"type": "array", "items": {
    "type": "object", "required": ["item", "verdict", "evidence", "lost", "defect", "reason", "read"],
    "properties": {
        "item": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "evidence": {"type": "string", "enum": ["supports", "partly", "no"]},
        "lost": {"type": "string"},
        "defect": {"type": "string", "enum": ["", *DEFECTS]},
        "reason": {"type": "string"},
        "read": {"type": "array", "items": {"type": "string"}}}}}}}

PROMPT = """Read {input}. It holds business rules written about a legacy system whose source is read-only under {legacy}/. Check each item against the source code as an independent reviewer: you did not write these rules, and you are not told how they were made.

For each item, "rule" is the rule to check and "cites" the source lines given for it. "stands_for" lists the rules it was made from, written by two other tools, for reference; "siblings", where present, are the other rules made from the same input rules. Read the cited lines and what is around them, and follow a PERFORM, CALL or copybook where the rule depends on it. Do not assume the cited lines are the right ones: search the source when they do not show what the rule says.

- "verdict": whether the system behaves as the rule says when it runs. "correct" if every statement in the rule is true of what the code does; "minor" if it is true in substance but a detail is wrong or missing (a value, a condition, a message); "wrong" if the code does not do this, if a condition or behaviour is materially different, or if the code the rule describes can never run (it is unreachable, or outside any job step); "unsure" only if the source cannot settle it (it depends on data, configuration or a program that is not in the source).
- "evidence": whether the cited lines support the rule: "supports", "partly" or "no".
- "lost": a true detail, stated in "stands_for" and confirmed by the code, that neither the rule nor its siblings states; empty if none.
- "defect": only for an item with a "defect_note". "real" if the code does what the note says and it looks unintended; "not_a_defect" if the code does not do it, or it is plainly intended; "unsure" otherwise. Empty for an item with no note.
- "reason": one or two sentences naming what in the code decided it.
- "read": the lines you relied on, each as "<path>:<start>-<end>" or "<path>:<line>" (one range each), with paths relative to {legacy}/ exactly as the files are named.

Answer for every item, using its id exactly. Answer only with the JSON."""

RETRY = """Your answer for some items failed Reframe's checks. Answer again for these items only, in the same JSON shape:

{problems}"""


# ---- the sample --------------------------------------------------------------------------------------------------

def kind_of(rule: dict[str, Any], inputs: dict[str, Any]) -> str | None:
    decision = rule["decision"]
    if decision == "kept":
        tools = {inputs[k]["tool"] for k in rule["covers"] if k in inputs}
        return "kept_aws" if tools == {"aws"} else "kept_claude" if tools == {"claude"} else None
    return decision if decision in QUOTA else None


def candidates(folder: RunFolder) -> dict[str, list[dict[str, Any]]]:
    """Every rule that could be sampled, by kind, as audit items (without ids)."""
    inputs = (folder.load("reconcile/inputs/inputs.json") or {}).get("rules") or {}
    made = (folder.load("outputs/reconcile/rules.json") or {}).get("rules") or []
    decisions = folder.load("reconcile/decisions.json") or {}
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in made:
        kind = kind_of(r, inputs)
        if kind:
            found[kind].append({"kind": kind, "key": r["key"], "unit": r["unit"], "program": r["program"], "rule": r["text"],
                                "cites": r["evidence"], "confidence": r.get("confidence", ""), "decision": r["decision"],
                                "stands_for": [{"tool": inputs[k]["tool"], "text": inputs[k].get("text", ""), "cites": inputs[k].get("cites") or []}
                                               for k in r["covers"] if k in inputs],
                                "defect_note": r.get("defect") or "", "defect_call": r.get("defect_call") or "",
                                "siblings": [x["text"] for x in made if x["unit"] == r["unit"] and x["key"] != r["key"]]})
    for unit, d in sorted(decisions.items()):
        if d.get("decision") != "rejected":
            continue
        for key in [k for k in (d.get("covers") or []) or [unit.split(":", 1)[1]] if k in inputs]:
            r = inputs[key]
            found["rejected"].append({"kind": "rejected", "key": key, "unit": unit, "program": r.get("program", ""), "rule": r.get("text", ""),
                                      "cites": r.get("cites") or [], "confidence": d.get("confidence", ""), "decision": "rejected",
                                      "stands_for": [], "defect_note": d.get("defect") or ""})
    return found


def draw(found: dict[str, list[dict[str, Any]]], size: int, seed: int) -> list[dict[str, Any]]:
    """The sample: a fixed-seed draw per kind, in a stable order, with ids A01, A02, ..."""
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    for kind, quota in QUOTA.items():
        pool = sorted(found.get(kind) or [], key=lambda x: x["key"])
        want = len(pool) if quota is None else min(len(pool), max(1, round(quota * size / 60)))
        items += rng.sample(pool, want) if want < len(pool) else pool
    for n, item in enumerate(items, 1):
        item["id"] = f"A{n:02d}"
    return items


def sample(folder: RunFolder, size: int = 60, seed: int = 1) -> list[dict[str, Any]]:
    saved = folder.load(f"{FOLDER}/sample.json")
    if saved:
        return saved["items"]
    items = draw(candidates(folder), size, seed)
    folder.save(f"{FOLDER}/sample.json", {"drawn_at": iso(), "size": size, "seed": seed, "items": items})
    return items


# ---- the judges --------------------------------------------------------------------------------------------------

def noted(items: list[dict[str, Any]]) -> set[str]:
    """The items whose defect note is judged: a note belongs to a unit, so only the first sampled item of each unit carries it."""
    seen, out = set(), set()
    for item in items:
        if item["defect_note"] and item["unit"] not in seen:
            seen.add(item["unit"])
            out.add(item["id"])
    return out


def shown(item: dict[str, Any], with_note: bool) -> dict[str, Any]:
    """What a judge sees: not the decision, reason, confidence or the other judge's answer."""
    out = {k: item[k] for k in ("id", "program", "rule", "cites", "stands_for")}
    if item.get("siblings"):
        out["siblings"] = item["siblings"]
    if with_note:
        out["defect_note"] = item["defect_note"]
    return out


def problems(item: dict[str, Any], answer: dict[str, Any] | None, legacy: Path, with_note: bool = False) -> list[str]:
    from reframe.reconcile.decide import missing_evidence
    if not answer:
        return ["no answer for this item"]
    wrong = []
    if answer.get("verdict") not in VERDICTS:
        wrong.append("verdict is not one of " + ", ".join(VERDICTS))
    if with_note and answer.get("defect") not in DEFECTS:
        wrong.append("the item has a defect note: defect must be one of " + ", ".join(DEFECTS))
    if not answer.get("read"):
        wrong.append("read is empty: give the lines you relied on")
    bad = missing_evidence(answer.get("read") or [], legacy)
    if bad:
        wrong.append("these lines do not exist in the source: " + ", ".join(bad[:5]))
    return wrong


def batches(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_program: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_program[item["program"]].append(item)
    out: list[list[dict[str, Any]]] = []
    for program in sorted(by_program, key=lambda p: (-len(by_program[p]), p)):  # a program's items together; small ones packed
        group = by_program[program]
        for part in (group[i:i + BATCH] for i in range(0, len(group), BATCH)):
            room = next((b for b in out if len(b) + len(part) <= BATCH), None)
            if room is None:
                out.append(list(part))
            else:
                room.extend(part)
    return out


def judge(folder: RunFolder, label: str, claude, items: list[dict[str, Any]]) -> dict[str, Any]:
    """One judge over the sample, keeping each answer as it comes: a second call judges only what is missing. Stops
    on Claude's usage limit or a sign-in problem, after the batches already running."""
    from reframe.claude.steps import stop_for
    from reframe.claude.workspace import paths
    places = paths(folder)
    project = folder.state["project"]
    name = f"{FOLDER}/judge-{label}.json"
    saved = folder.load(name) or {"label": label, "answers": {}, "steps": []}
    lock = threading.Lock()
    stop_now, stopped = threading.Event(), []
    todo = [i for i in items if i["id"] not in saved["answers"]]
    with_note = noted(items)
    work = places["analysis"] / "reframe" / "audit"
    work.mkdir(parents=True, exist_ok=True)

    def one(number: int, batch: list[dict[str, Any]]) -> None:
        pending = {i["id"]: i for i in batch}
        failed: dict[str, list[str]] = {}
        for attempt in (1, 2):
            if not pending or stop_now.is_set():
                return
            step_name = f"audit-{label}-b{number}{'-retry' if attempt == 2 else ''}"
            path = work / f"{step_name}.json"
            path.write_text(json.dumps({"items": [shown(i, i["id"] in with_note) for i in pending.values()]}, indent=1))
            prompt = PROMPT.format(input=f"analysis/{project}/{path.relative_to(places['analysis']).as_posix()}", legacy=f"legacy/{project}")
            if attempt == 2:
                prompt += "\n\n" + RETRY.format(problems="\n".join(f"- {k}: {'; '.join(v)}" for k, v in failed.items()))
            step = claude.run(step_name, prompt, timeout=3600, schema=SCHEMA)
            with lock:
                saved["steps"].append({"batch": number, "attempt": attempt, **{k: v for k, v in step.as_json().items()
                                                                                  if k in ("ok", "seconds", "tokens", "cost_usd", "problem", "models")}})
            if not step.ok:
                if step.problem in ("limit", "signin"):
                    stopped.append(step)
                    stop_now.set()
                    return
                failed = {k: [f"the step did not finish ({step.problem})"] for k in pending}
                continue
            answers = {a["item"]: a for a in (claude.structured(step) or {}).get("judgments") or [] if isinstance(a, dict) and a.get("item") in pending}
            failed = {}
            for key, item in list(pending.items()):
                wrong = problems(item, answers.get(key), places["legacy"], key in with_note)
                if wrong:
                    failed[key] = wrong
                    continue
                with lock:
                    saved["answers"][key] = {**{k: answers[key].get(k) for k in SCHEMA["properties"]["judgments"]["items"]["required"] if k != "item"},
                                             "attempt": attempt}
                    folder.save(name, saved)
                del pending[key]
        with lock:  # failed twice: recorded as unsure
            for key in pending:
                saved["answers"][key] = {"verdict": "unsure", "evidence": "no", "lost": "", "defect": "unsure" if key in with_note else "",
                                         "reason": "The judge's answer failed Reframe's checks twice: " + "; ".join(failed.get(key, ["no answer"])),
                                         "read": [], "attempt": 2, "check_failed": True}
            folder.save(name, saved)

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        list(pool.map(lambda nb: one(*nb), enumerate(batches(todo), 1 + len({s["batch"] for s in saved["steps"]}))))
    folder.save(name, saved)
    if stopped:
        raise stop_for(stopped[0], f"the audit's {label} judge")
    return saved


# ---- settling and the figures ------------------------------------------------------------------------------------

SETTLE_COLUMNS = ["id", "kind", "program", "rule", "cites", "defect_note", "question"]


def disagreements(items: list[dict[str, Any]], judged: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    labels = sorted(judged)
    with_note = noted(items)
    out = []
    for item in items:
        answers = {lab: judged[lab]["answers"].get(item["id"]) or {} for lab in labels}
        verdicts = {a.get("verdict") for a in answers.values()}
        defects = {a.get("defect") or "" for a in answers.values()} if item["id"] in with_note else {""}
        if len(verdicts) > 1 or len(defects) > 1:
            out.append({"item": item, "answers": answers, "verdict_differs": len(verdicts) > 1, "defect_differs": len(defects) > 1})
    return out


def write_settle(folder: RunFolder, items: list[dict[str, Any]], judged: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """settle.csv: one row per item the judges differ on, keeping any owner answers already filled in."""
    labels = sorted(judged)
    path = folder.path(f"{FOLDER}/settle.csv")
    before = read_settled(folder)
    found = disagreements(items, judged)
    columns = SETTLE_COLUMNS + [f"{lab}_{k}" for lab in labels for k in ("verdict", "defect", "reason")] + ["owner_verdict", "owner_defect", "owner_note"]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=columns)
    w.writeheader()
    for d in found:
        item = d["item"]
        row = {"id": item["id"], "kind": KINDS[item["kind"]], "program": item["program"], "rule": item["rule"], "cites": "; ".join(item["cites"]),
               "defect_note": item["defect_note"] if item["id"] in noted(items) else "",
               "question": " and ".join(x for x, on in (("verdict", d["verdict_differs"]), ("defect", d["defect_differs"])) if on)}
        for lab in labels:
            a = d["answers"][lab]
            row.update({f"{lab}_verdict": a.get("verdict", ""), f"{lab}_defect": a.get("defect", ""), f"{lab}_reason": a.get("reason", "")})
        row.update({k: (before.get(item["id"]) or {}).get(k, "") for k in ("owner_verdict", "owner_defect", "owner_note")})
        w.writerow(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out.getvalue())
    return found


def read_settled(folder: RunFolder) -> dict[str, dict[str, str]]:
    path = folder.path(f"{FOLDER}/settle.csv")
    if not path.exists():
        return {}
    return {row["id"]: row for row in csv.DictReader(io.StringIO(path.read_text()))}


def final(item: dict[str, Any], judged: dict[str, dict[str, Any]], settled: dict[str, dict[str, str]], with_note: bool = False) -> dict[str, Any]:
    """The item's verdict and defect answer: the judges' shared answer, else the owner's, else "unsettled"."""
    answers = [judged[lab]["answers"].get(item["id"]) or {} for lab in sorted(judged)]
    owner = settled.get(item["id"]) or {}
    verdicts = {a.get("verdict") for a in answers}
    verdict = verdicts.pop() if len(verdicts) == 1 else (owner.get("owner_verdict") or "").strip() or "unsettled"
    defect: Any = ""  # a judge may leave the defect answer out
    if with_note:
        defects = {a.get("defect") for a in answers}
        defect = defects.pop() if len(defects) == 1 else (owner.get("owner_defect") or "").strip() or "unsettled"
    return {"verdict": verdict, "defect": defect}


def figures(items: list[dict[str, Any]], judged: dict[str, dict[str, Any]], settled: dict[str, dict[str, str]]) -> dict[str, Any]:
    with_note = noted(items)
    finals = {i["id"]: final(i, judged, settled, i["id"] in with_note) for i in items}
    labels = sorted(judged)

    def tally(group: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(group)
        counts = Counter(finals[i["id"]]["verdict"] for i in group)
        agreed = sum(len({(judged[lab]["answers"].get(i["id"]) or {}).get("verdict") for lab in labels}) == 1 for i in group)
        out: dict[str, Any] = {"items": n, **{v: counts.get(v, 0) for v in (*VERDICTS, "unsettled")}, "judges_agreed": agreed}
        if n:
            out["correct_share"] = round(counts.get("correct", 0) / n, 2)
            out["correct_or_minor_share"] = round((counts.get("correct", 0) + counts.get("minor", 0)) / n, 2)
            out["wrong_share"] = round(counts.get("wrong", 0) / n, 2)
        return out

    by_kind = {KINDS[k]: tally([i for i in items if i["kind"] == k]) for k in QUOTA if any(i["kind"] == k for i in items)}
    rejected = [i for i in items if i["kind"] == "rejected"]
    recommended = [i for i in items if i["kind"] != "rejected"]
    notes = [i for i in items if i["id"] in with_note]
    defect_counts = Counter(finals[i["id"]]["defect"] for i in notes)
    # A note's call (P3-13): "unintended" should be judged real, "deliberate" not a defect.
    called = [i for i in notes if i.get("defect_call") and finals[i["id"]]["defect"] in ("real", "not_a_defect")]
    calls_agreed = sum((i["defect_call"] == "unintended") == (finals[i["id"]]["defect"] == "real") for i in called)
    confidence: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for i in recommended:
        confidence[i["confidence"] or "none"][finals[i["id"]]["verdict"]] += 1
    evidence = {lab: dict(Counter((judged[lab]["answers"].get(i["id"]) or {}).get("evidence") for i in recommended)) for lab in labels}
    cost = {lab: {"steps": len(judged[lab]["steps"]), "seconds": sum(s.get("seconds") or 0 for s in judged[lab]["steps"]),
                  "cost_usd": round(sum(s.get("cost_usd") or 0 for s in judged[lab]["steps"]), 2),
                  "models": sorted({m for s in judged[lab]["steps"] for m in s.get("models") or []})} for lab in labels}
    return {
        "generated_at": iso(), "items": len(items), "judges": labels,
        "recommended": tally(recommended), "by_kind": by_kind,
        "rejections_upheld": sum(finals[i["id"]]["verdict"] in ("wrong", "unsure") for i in rejected), "rejections": len(rejected),
        "confidence": {k: dict(v) for k, v in sorted(confidence.items())},
        "defects": {"notes": len(notes), **{d: defect_counts.get(d, 0) for d in (*DEFECTS, "unsettled")}, "called": len(called), "calls_agreed": calls_agreed},
        "evidence": evidence, "cost": cost,
        "unsettled": sorted(k for k, v in finals.items() if "unsettled" in (v["verdict"], v["defect"])),
        "final": finals,
    }


def report(folder: RunFolder, items: list[dict[str, Any]], judged: dict[str, dict[str, Any]]) -> dict[str, Any]:
    write_settle(folder, items, judged)
    result = figures(items, judged, read_settled(folder))
    folder.save(f"{FOLDER}/audit.json", result)
    return result


# ---- the command -------------------------------------------------------------------------------------------------

def run(folder: RunFolder, size: int = 60, seed: int = 1, judges: dict[str, str] | None = None, make_claude=None,
        say=print) -> dict[str, Any]:
    if ((folder.state.get("reconcile") or {}).get("status")) != "DONE":
        raise SystemExit(f"run {folder.root.name}: the reconciliation is not done; reframe reconcile first")
    if make_claude is None:
        from reframe.reconcile.pipeline import claude_code
        make_claude = lambda model: claude_code(folder, "subscription", model=model)  # noqa: E731
    items = sample(folder, size, seed)
    say(f"Sample: {len(items)} items ({', '.join(f'{v} {KINDS[k].lower()}' for k, v in Counter(i['kind'] for i in items).items())})")
    judged = {}
    for label, model in (judges or JUDGES).items():
        say(f"Judge {label} ({model}) ...")
        judged[label] = judge(folder, label, make_claude(model), items)
    result = report(folder, items, judged)
    r = result["recommended"]
    say(f"Recommended rules: {r['correct']} correct, {r['minor']} minor, {r['wrong']} wrong, {r['unsure']} unsure, {r['unsettled']} unsettled "
        f"of {r['items']}; the judges agreed on {r['judges_agreed']}. Rejections upheld: {result['rejections_upheld']} of {result['rejections']}."
        + (f" Defect calls agreed: {result['defects']['calls_agreed']} of {result['defects']['called']}." if result["defects"]["called"] else ""))
    if result["unsettled"]:
        say(f"{len(result['unsettled'])} item(s) to settle in {folder.path(FOLDER + '/settle.csv')}, then run this again.")
    return result


def main(argv: list[str] | None = None) -> int:
    from reframe.core.commands import find_run
    parser = argparse.ArgumentParser(prog="python -m reframe.reconcile.audit", description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", help="the run to audit (default: the latest run)")
    parser.add_argument("--size", type=int, default=60, help="the sample size the quotas are scaled to (default 60)")
    parser.add_argument("--seed", type=int, default=1, help="the sample's seed (default 1)")
    parser.add_argument("--judge", action="append", default=[], metavar="label=model", help="a judge, instead of the defaults")
    args = parser.parse_args(argv)
    judges = dict(j.split("=", 1) for j in args.judge) or None
    run(find_run(args.run), args.size, args.seed, judges)
    return 0


if __name__ == "__main__":
    sys.exit(main())
