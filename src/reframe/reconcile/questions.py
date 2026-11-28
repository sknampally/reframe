"""Pipeline 3, stage R5: one SME list (docs/pipeline-3.md, section 4).

The open questions of AWS Transform, of the plugin and of stage R4 (its needs_sme decisions) are merged into one list.
A headless step, batched by program, makes questions that ask the same thing into one and marks a question the person's
documents or answers already settle; Reframe checks that every original question is in exactly one merged question,
and that a settlement cites a document or answer it was given. The possible defects stage R4 found are listed beside
the questions, as their own kind. The person's answers (the list with its answer column filled in, put back in the
context folder) are matched to the merged questions by their original questions.

Written as reconcile/questions.csv (with an empty answer column, for a spreadsheet) and reconcile/questions.json.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from reframe.core.runfolder import RunFolder, iso
from reframe.reconcile.units import PARALLEL, named_rules

BATCH_QUESTIONS = 40
TOOLS = {"aws": "AWS Transform", "claude": "Claude's plugin", "r4": "Reframe's decisions"}
COLUMNS = ["id", "kind", "call", "question", "owner", "status", "rules", "originals", "from", "evidence", "settled_by", "settled_answer", "answer"]

SCHEMA = {"type": "object", "required": ["questions"], "properties": {"questions": {"type": "array", "items": {
    "type": "object", "required": ["text", "owner", "members", "settled_by", "settled_answer"],
    "properties": {"text": {"type": "string"}, "owner": {"type": "string"},
                   "members": {"type": "array", "items": {"type": "string"}},
                   "settled_by": {"type": "string"}, "settled_answer": {"type": "string"}}}}}}

ONE_TOOL = (('from two tools that assessed the same source ("AWS Transform" and "Claude\'s plugin") and from Reframe\'s own decisions',
             'from Claude\'s plugin, which assessed the source, and from Reframe\'s own decisions'),)

PROMPT = """Read {input}. It holds open questions for subject-matter experts about a legacy system, from two tools that assessed the same source ("AWS Transform" and "Claude's plugin") and from Reframe's own decisions. Each has an id, where it came from, its text, who should answer it, and the business rules it holds up.{context}

Make one list of questions for the experts:
- Questions that ask the same thing become one question: write it once, clearly, for a business reader, and list every id it stands for in "members". Do not merge questions that only share a topic; merge only those one answer would settle.
- Every id must be in exactly one question's "members". A question nothing else asks the same thing as stands alone, with its own id as its only member.
- "owner" is who should answer it (for example "Business analyst" or "Operations").
- If a document or an answered question you were given already settles the question, set "settled_by" to that document's file name or that answer's id, and "settled_answer" to what it says, in one or two sentences. Otherwise leave both empty. Do not settle a question from general knowledge or from the code.

Answer only with the JSON."""

RETRY = """Your answer failed Reframe's checks:

{problems}

Answer again with the whole list for these questions, in the same JSON shape."""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


# ---- the originals ----------------------------------------------------------------------------------------------------

def originals(gathered: dict[str, Any], found: list[dict[str, Any]], kept: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every original question (both tools' and R4's needs_sme), and the possible defects R4 found."""
    rules = gathered["rules"]
    asked = []
    for q in gathered.get("questions") or []:
        asked.append({"key": f"{q['tool']}:{q['key']}", "key_in_tool": q["key"], "tool": q["tool"], "code": q.get("code") or "", "text": q.get("text", ""),
                      "owner": q.get("owner") or "", "rules": list(q.get("rules") or [])})
    defects = []
    for u in found:
        d = kept.get(u["key"]) or {}
        members = [r["key"] for r in u["rules"]]
        if d.get("decision") == "needs_sme" and str(d.get("question") or "").strip():
            asked.append({"key": f"r4:{u['key']}", "tool": "r4", "code": u["key"], "text": d["question"], "owner": "Subject-matter expert",
                          "rules": members})
        if str(d.get("defect") or "").strip():
            defects.append({"key": f"defect:{u['key']}", "unit": u["key"], "text": d["defect"], "call": d.get("defect_call") or "",
                            "evidence": d.get("defect_evidence") or d.get("evidence") or [], "rules": members, "program": u["program"]})
    for q in asked:
        q["program"] = Counter(rules[k]["program"] for k in q["rules"] if k in rules and rules[k]["program"]).most_common(1)[0][0] \
            if any(k in rules and rules[k]["program"] for k in q["rules"]) else ""
    return asked, defects


def batches(asked: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Questions batched by the program of the rules they hold up, so questions about the same code meet: up to
    BATCH_QUESTIONS a batch, a program's questions kept together where they fit."""
    by_program: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in asked:
        by_program[q["program"]].append(q)
    made: list[list[dict[str, Any]]] = [[]]
    for program in sorted(by_program):
        members = sorted(by_program[program], key=lambda q: q["key"])
        if made[-1] and len(made[-1]) + len(members) > BATCH_QUESTIONS:
            made.append([])
        for q in members:
            if len(made[-1]) >= BATCH_QUESTIONS:
                made.append([])
            made[-1].append(q)
    return [b for b in made if b]


# ---- checking a merge -------------------------------------------------------------------------------------------------

def problems(ids: list[str], merged: list[dict[str, Any]], sources: set[str]) -> list[str]:
    found, seen = [], Counter(m for q in merged for m in q.get("members") or [])
    unknown = sorted(set(seen) - set(ids))
    if unknown:
        found.append(f"members that are not ids you were given: {', '.join(unknown)}")
    twice = sorted(k for k, n in seen.items() if n > 1 and k in ids)
    if twice:
        found.append(f"ids in more than one question: {', '.join(twice)}")
    missing = [k for k in ids if k not in seen]
    if missing:
        found.append(f"ids in no question: {', '.join(missing)}")
    wrong = sorted({q["settled_by"] for q in merged if q.get("settled_by") and q["settled_by"] not in sources})
    if wrong:
        found.append(f"settled_by names that are not a document or answer you were given: {', '.join(wrong)}")
    return found


def repair(ids: list[str], merged: list[dict[str, Any]], sources: set[str], texts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """After a second failed answer: unknown ids dropped, an id in two questions kept in the first, an id in none made a
    question of its own, a settlement citing nothing given removed. Every original is then in exactly one question."""
    placed: set[str] = set()
    fixed = []
    for q in merged:
        members = []
        for m in q.get("members") or []:
            if m in ids and m not in placed:
                members.append(m)
                placed.add(m)
        if not members:
            continue
        settled = q.get("settled_by") in sources
        fixed.append({**q, "members": members, "settled_by": q.get("settled_by") if settled else "", "settled_answer": q.get("settled_answer") if settled else ""})
    for k in ids:
        if k not in placed:
            fixed.append({"text": texts[k]["text"], "owner": texts[k]["owner"], "members": [k], "settled_by": "", "settled_answer": ""})
    return fixed


# ---- the step ----------------------------------------------------------------------------------------------------------

def _input(batch: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, Any]:
    def describe(k: str) -> str:
        r = rules.get(k) or {}
        return f"{r.get('program', '')} {r.get('name') or k}".strip()
    return {"questions": [{"id": f"Q{n}", "from": f"{TOOLS[q['tool']]} {q['code']}".strip(), "text": q["text"], "owner": q["owner"],
                           "holds_up": [describe(k) for k in q["rules"][:12]]} for n, q in enumerate(batch, 1)]}


def merge_batch(folder: RunFolder, claude, places: dict[str, Any], name: str, batch: list[dict[str, Any]], rules: dict[str, Any],
                documents: list[dict[str, Any]], answers: list[dict[str, Any]], log: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Any]:
    """One batch merged and checked: (merged questions with original keys as members, a StepResult to stop for or None)."""
    project = folder.state["project"]
    folder_in = places["analysis"] / "reframe" / "reconcile"
    folder_in.mkdir(parents=True, exist_ok=True)
    data = _input(batch, rules)
    ids = [q["id"] for q in data["questions"]]
    back = {f"Q{n}": q["key"] for n, q in enumerate(batch, 1)}
    texts = {q["id"]: q for q in data["questions"]}
    sources = {d["file"] for d in documents} | {a["id"] for a in answers if a.get("id")}
    context = ""
    if documents or answers:
        data["answered"] = [{"id": a.get("id", ""), "question": a.get("question", ""), "answer": a.get("answer", "")} for a in answers]
        context = (" The person gave context: " + (f"documents {', '.join(f'analysis/{project}/' + d['read_as'] for d in documents)} (named in settled_by by these file names: "
                                                  f"{', '.join(d['file'] for d in documents)})" if documents else "")
                   + ("; " if documents and answers else "") + ("answered questions, in the file's \"answered\" list" if answers else "") + ".")
    path = folder_in / f"{name}.json"
    path.write_text(json.dumps(data, indent=1))
    from reframe.reconcile.units import one_tool
    prompt = one_tool(folder, PROMPT.format(input=f"analysis/{project}/{path.relative_to(places['analysis']).as_posix()}", context=context), ONE_TOOL)
    merged: list[dict[str, Any]] = []
    for attempt in (1, 2):
        step = claude.run(f"{name}{'-retry' if attempt == 2 else ''}", prompt, timeout=3600, schema=SCHEMA)
        log.append({"step": name, "attempt": attempt, **{k: v for k, v in step.as_json().items() if k in ("ok", "seconds", "tokens", "cost_usd", "problem")}})
        if not step.ok:
            if step.problem in ("limit", "signin"):
                return [], step
            wrong = [f"the step did not finish ({step.problem})"]
        else:
            merged = [q for q in (claude.structured(step) or {}).get("questions") or [] if isinstance(q, dict)]
            wrong = problems(ids, merged, sources)
            if not wrong:
                break
        prompt = one_tool(folder, PROMPT.format(input=f"analysis/{project}/{path.relative_to(places['analysis']).as_posix()}", context=context), ONE_TOOL) + "\n\n" + \
            RETRY.format(problems="\n".join(f"- {w}" for w in wrong))
    merged = repair(ids, merged, sources, texts)
    return [{**q, "members": [back[m] for m in q["members"]]} for q in merged], None


# ---- the person's answers ---------------------------------------------------------------------------------------------

def answered_by(question: dict[str, Any], answers: list[dict[str, Any]], previous: dict[str, list[str]],
                originals: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The person's answer to a merged question: an answered row naming one of its originals (the list's originals
    column), whose id was a question of the previous list with one of its originals, or whose id is one original's own
    key, or its code (OQ-008) together with one of the rules it holds up: a tool's codes repeat from one business
    function to the next, so a code alone does not say which question it is."""
    mine = set(question["originals"])
    originals = originals or {}
    for a in answers:
        named = set(str(a.get("originals") or "").split()) | set(previous.get(str(a.get("id") or ""), []))
        if named & mine:
            return a
        given, rules = str(a.get("id") or "").strip(), set(named_rules(a))
        for key in mine:
            q = originals.get(key) or {}
            if given and (given in (key, q.get("key_in_tool")) or (given == q.get("code") and rules & set(q.get("rules") or []))):
                return a
    return None


# ---- R5 ----------------------------------------------------------------------------------------------------------------

def build(folder: RunFolder, claude, gathered: dict[str, Any], found: list[dict[str, Any]], kept: dict[str, Any]) -> dict[str, Any]:
    """The SME list. A batch whose questions and context are unchanged keeps its merge from before."""
    from reframe.claude.steps import stop_for
    from reframe.claude.workspace import paths
    places = paths(folder)
    rules, ctx = gathered["rules"], gathered["context"]
    asked, defects = originals(gathered, found, kept)
    known = set(rules)
    cache = folder.load("reconcile/sme-merges.json", {}) or {}
    log = folder.load("reconcile/sme-steps.json", []) or []
    lock = threading.Lock()
    stopped: list[Any] = []
    planned = []
    for n, batch in enumerate(batches(asked), 1):
        members = {k for q in batch for k in q["rules"]}
        answers = [a for a in ctx.get("answers") or [] if not (set(named_rules(a)) & known) or set(named_rules(a)) & members]
        planned.append((n, batch, answers, _hash([[{k: q[k] for k in ("key", "text", "owner", "rules")} for q in batch], ctx["documents"], answers])))

    def one(item) -> None:
        n, batch, answers, digest = item
        if digest in cache or stopped:
            return
        merged, stop = merge_batch(folder, claude, places, f"sme-b{n}", batch, rules, ctx["documents"], answers, log)
        with lock:
            folder.save("reconcile/sme-steps.json", log)
            if stop is not None:
                stopped.append(stop)
                return
            cache[digest] = merged
            folder.save("reconcile/sme-merges.json", cache)

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        list(pool.map(one, planned))
    if stopped:
        raise stop_for(stopped[0], "the reconciliation's SME list")
    folder.save("reconcile/sme-merges.json", {d: cache[d] for _, _, _, d in planned})  # forget merges of batches that no longer exist

    by_key = {q["key"]: q for q in asked}
    previous = {q["id"]: q["originals"] for q in (folder.load("reconcile/questions.json") or {}).get("questions") or []}
    listed = []
    for _, _, _, digest in planned:
        for q in cache[digest]:
            group = [by_key[k] for k in q["members"]]
            listed.append({"kind": "question", "question": q["text"], "owner": q.get("owner") or group[0]["owner"],
                           "rules": sorted({k for m in group for k in m["rules"]}), "originals": [m["key"] for m in group],
                           "from": [f"{TOOLS[m['tool']]} {m['code']}".strip() for m in group], "evidence": [],
                           "settled_by": q.get("settled_by") or "", "settled_answer": q.get("settled_answer") or "", "program": group[0]["program"]})
    for d in defects:
        ask = "Confirm this is deliberate:" if d["call"] == "deliberate" else "Is this intended?"
        listed.append({"kind": "defect", "question": f"{ask} {d['text']}", "owner": "Subject-matter expert", "rules": d["rules"], "call": d["call"],
                       "originals": [d["key"]], "from": [f"Reframe's decision on {d['unit']}"], "evidence": d["evidence"], "settled_by": "",
                       "settled_answer": "", "program": d["program"]})
    numbers: Counter[str] = Counter()
    answers = ctx.get("answers") or []
    for q in listed:
        numbers[q["kind"]] += 1
        q["id"] = f"{'SME' if q['kind'] == 'question' else 'DEF'}-{numbers[q['kind']]:03d}"
        given = answered_by(q, answers, previous, by_key)
        q["answer"] = (given or {}).get("answer", "")
        q["answered_in"] = (given or {}).get("file", "")
        q["status"] = "answered" if given else "settled" if q["settled_by"] else "open"
    once = Counter(k for q in listed if q["kind"] == "question" for k in q["originals"])
    if sorted(once) != sorted(by_key) or any(n != 1 for n in once.values()):
        raise RuntimeError("the SME list does not hold every original question exactly once")  # repair() makes this impossible
    value = {"generated_at": iso(), "originals": len(asked), "questions": listed,
             "counts": {"questions": numbers["question"], "defects": numbers["defect"],
                        **Counter(q["status"] for q in listed)}}
    folder.save("reconcile/questions.json", value)
    folder.path("reconcile/questions.csv").write_text(to_csv(listed))
    return value


def to_csv(listed: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for q in listed:
        writer.writerow([q["id"], q["kind"], q.get("call", ""), q["question"], q["owner"], q["status"], " ".join(q["rules"]), " ".join(q["originals"]),
                         "; ".join(q["from"]), " ".join(q["evidence"]), q["settled_by"], q["settled_answer"], q["answer"]])
    return out.getvalue()
