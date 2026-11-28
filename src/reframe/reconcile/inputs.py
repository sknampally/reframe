"""Pipeline 3, stages R0 and R1: the preflight, and the inputs (docs/pipeline-3.md, section 4).

The inputs are both tools' rules with their source citations, the comparison's pairs, both tools' open questions, and the
person's context folder: documents, review exports and answered questions. They are saved in reconcile/inputs/, and the
documents are copied into the Claude workspace (analysis/<project>/reframe/reconcile/context/) for the decision steps.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from reframe.core.errors import Outcome, Stop
from reframe.core.runfolder import RunFolder, iso

SOURCE_SUFFIXES = (".cbl", ".cob", ".jcl", ".cpy", ".bms", ".prc", ".proc", ".asm", ".pli", ".ctl")
TEXT, PDF, WORD = (".md", ".markdown", ".txt"), (".pdf",), (".docx",)
MARKS = {"Agree": "agree", "Needs change": "change", "Question": "question"}


# ---- R0: preflight ----------------------------------------------------------------------------------------------------

def preflight(folder: RunFolder, context: Path | None, auth: str = "subscription", claude_check=None) -> None:
    """Checked before anything runs; each failure stops with the fix. Pipeline 3 needs no AWS resources."""
    from reframe.claude import workspace
    from reframe.core.runfolder import claude_only
    state = folder.state
    claude = state.get("claude") or {}
    alone = claude_only(state)  # docs/release-1.md, section 2: every Claude rule is checked against the code
    if not alone and (state.get("status") != Outcome.DONE.value or not state.get("retrieved_at")):
        raise Stop(Outcome.REFUSED, "the AWS Transform assessment is not done on this run; the reconciliation reconciles both assessments")
    if claude.get("status") != Outcome.DONE.value:
        raise Stop(Outcome.REFUSED, "the Claude assessment is not done on this run; run reframe claude (or reframe resume) first")
    pairing = folder.load("outputs/comparison/ai-pairs.json") or {}
    if not alone and not pairing.get("complete"):
        raise Stop(Outcome.REFUSED, "the comparison's AI pairing of rules is not complete; reframe resume finishes it")
    places = workspace.paths(folder)
    if not places["hashes"].exists() or not places["legacy"].is_dir():
        raise Stop(Outcome.REFUSED, "the run has no Claude workspace with the snapshot; the reconciliation reads the source from it")
    changed = workspace.changed(folder)
    if changed:
        raise Stop(Outcome.FAILED, f"{len(changed)} file(s) in the snapshot differ from the source both assessments read (for example "
                                   f"{changed[0]}); the reconciliation will not decide against a different source")
    if context is not None and not context.is_dir():
        raise Stop(Outcome.REFUSED, f"the context folder {context} does not exist")
    if claude_check is None:
        from reframe.claude import preflight as claude_preflight
        claude_check = lambda: claude_preflight.check(auth, aws_identity=claude_preflight.run_identity(folder) if auth == "bedrock" else None)  # noqa: E731
    report = claude_check()
    if not report.ok:
        signin = any(not c.ok and "sign-in" in c.name for c in report.checks)
        raise Stop(Outcome.WAITING_SIGNIN if signin else Outcome.REFUSED, report.problems())


# ---- R1: the rules and their source citations -------------------------------------------------------------------------

def source_files(legacy: Path) -> dict[str, list[str]]:
    """Program name -> the snapshot's source files with that stem, relative to the snapshot."""
    found: dict[str, list[str]] = defaultdict(list)
    for f in sorted(legacy.rglob("*")):
        if f.is_file() and f.suffix.lower() in SOURCE_SUFFIXES:
            found[f.stem.upper()].append(f.relative_to(legacy).as_posix())
    return found


def aws_citations(folder: RunFolder) -> dict[str, list[tuple[str | None, list[int]]]]:
    """AWS Transform rule ID -> its traceability lines. They come in two shapes (spike P3-01, finding 5): line numbers in the
    program's own file, or {file, lines} for another file such as a JCL procedure or a copybook."""
    found: dict[str, list[tuple[str | None, list[int]]]] = {}
    for path in sorted(folder.path("outputs/requirements").rglob("traceability.yaml")):
        for rule in (yaml.safe_load(path.read_text()) or {}).get("rules") or []:
            own: list[int] = []
            other: list[tuple[str | None, list[int]]] = []
            for entry in rule.get("traceability_lines") or []:
                if isinstance(entry, dict):
                    other.append((str(entry.get("file")), [int(n) for n in entry.get("lines") or [] if str(n).isdigit()]))
                elif str(entry).isdigit():
                    own.append(int(entry))
            found[str(rule.get("rule_id"))] = ([(None, own)] if own else []) + other
    return found


def ranges(numbers: list[int]) -> str:
    spans: list[list[int]] = []
    for n in sorted(set(numbers)):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ",".join(f"{a}-{b}" if a != b else str(a) for a, b in spans)


def rules(folder: RunFolder) -> dict[str, dict[str, Any]]:
    """Every rule pipeline 3 can use, by key: AWS Transform's business rules and Claude's rule cards, with their citations."""
    from reframe.claude.workspace import paths
    from reframe.report.build import collect, collect_claude
    files, traced = source_files(paths(folder)["legacy"]), aws_citations(folder)
    found: dict[str, dict[str, Any]] = {}
    for r in collect(folder)["rules"]:
        if r.get("disposition") != "captured":
            continue
        cites = []
        for file, lines in traced.get(str(r.get("id")), []):
            for f in [file] if file else files.get(str(r.get("program")).upper(), []):
                cites.append(f"{f}:{ranges(lines)}" if lines else f)
        found[r["key"]] = {"tool": "aws", "key": r["key"], "id": r.get("id"), "name": r.get("name", ""), "text": r.get("text", ""),
                           "program": str(r.get("program") or "").upper(), "function": r.get("function"),
                           "cites": cites or files.get(str(r.get("program")).upper(), [])}
    for r in collect_claude(folder)["rules"]:
        found[r["key"]] = {"tool": "claude", "key": r["key"], "id": r.get("id"), "name": r.get("name", ""), "text": r.get("text", ""),
                           "program": str(r.get("program") or "").upper(), "function": r.get("function"),
                           "cites": [r["source"]] if r.get("source") else []}
    return found


def questions(folder: RunFolder) -> list[dict[str, Any]]:
    """Both tools' open questions: AWS Transform's, and the plugin's rules marked as needing an SME. Each names the rules
    it holds up: the plugin's question its rule card; otherwise the rules behind its capability's requirements."""
    from reframe.report.build import collect, collect_claude
    found = []
    for tool, data in (("aws", collect(folder)), ("claude", collect_claude(folder))):
        backing: dict[str, list[str]] = defaultdict(list)
        for r in data.get("requirements") or []:
            backing[r["capability"]] += [k for k in r.get("rules") or [] if k not in backing[r["capability"]]]
        for q in data.get("questions") or []:
            holds = [f"claude/{q['rule']}"] if q.get("rule") else backing.get(q.get("capability"), [])
            found.append({"tool": tool, "key": q.get("key"), "code": q.get("code"), "text": q.get("text", ""), "owner": q.get("owner"),
                          "function": q.get("function"), "rules": holds})
    return found


# ---- R1: the context folder -------------------------------------------------------------------------------------------

def word_text(path: Path) -> str:
    """The text of a Word (.docx) file, paragraph by paragraph, read with the standard library."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    paragraphs = re.split(r"</w:p>", xml)
    text = [re.sub(r"<[^>]+>", "", re.sub(r"<w:tab/>", "\t", p)) for p in paragraphs]
    import html
    return "\n".join(html.unescape(t).strip() for t in text if t.strip())


def _review_export(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("marks"), list) and isinstance(data.get("source"), dict)


NO_KEY = "a review download without a key column (made before part 2): download it again from the page"


def review_file(path: Path, name: str) -> dict[str, Any] | None:
    """A review download (the JSON, or the CSV with its key, status and source_sha256 columns) as one reviewer's marks;
    None if the file is not one. A CSV without the key column comes back with "unusable" set to the reason."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if not _review_export(data):
            return None
        marks = [{"key": str(m.get("key") or ""), "status": MARKS.get(str(m.get("status"))), "comment": str(m.get("comment") or ""),
                  "at": str(m.get("marked_at") or "")} for m in data["marks"] if isinstance(m, dict)]
        signoffs = [{"function": str(x.get("function") or ""), "name": str(x.get("name") or ""), "comment": str(x.get("comment") or ""),
                     "at": str(x.get("at") or "")} for x in data.get("signoffs") or [] if isinstance(x, dict) and x.get("function")]
        return {"file": name, "format": "json", "reviewer": str(data.get("reviewer") or ""), "tool": str(data.get("tool") or ""),
                "sha256": data["source"].get("sha256"), "marks": [m for m in marks if m["key"] and m["status"]], "signoffs": signoffs}
    if path.suffix.lower() != ".csv":
        return None
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = [(c or "").strip().lower() for c in reader.fieldnames or []]
        if not {"status", "source_sha256"} <= set(columns) or "answer" in columns:
            return None
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    first = lambda column: next((r[column] for r in rows if r.get(column)), "")  # noqa: E731
    found: dict[str, Any] = {"file": name, "format": "csv", "reviewer": first("reviewer"), "tool": first("tool"), "sha256": first("source_sha256"), "marks": [], "signoffs": []}
    if "key" not in columns:
        return {**found, "unusable": NO_KEY}
    found["marks"] = [{"key": r["key"], "status": MARKS.get(r.get("status", "")), "comment": r.get("comment", ""), "at": r.get("marked_at", "")}
                      for r in rows if r.get("key") and MARKS.get(r.get("status", ""))]
    found["signoffs"] = [{"function": r["key"].split(":", 1)[1], "name": r.get("text", ""), "comment": r.get("comment", ""), "at": r.get("marked_at", "")}
                         for r in rows if r.get("type") == "sign-off" and r.get("key", "").startswith("signoff:")]
    return found


def signoffs(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every sign-off in the review downloads read: which page, which function, who and when (section 8.11)."""
    return [{"page": r["tool"], "function": x["function"], "name": x["name"], "reviewer": r["reviewer"], "at": x["at"], "comment": x["comment"],
             "file": r["file"]} for r in reviews for x in r.get("signoffs") or []]


PRECEDENCE = {"agree": 0, "question": 1, "change": 2}  # Needs change and Question take precedence over Agree (section 8.11)


def combine(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Every reviewer's marks as one mark per key: the status that takes precedence (Needs change, then Question, then
    Agree), every reviewer's comment kept with their name, and each reviewer's own mark under "by"."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        for m in review["marks"]:
            by_key.setdefault(m["key"], []).append({"reviewer": review["reviewer"], "status": m["status"], "comment": m["comment"], "at": m.get("at", ""),
                                                    "file": review["file"]})
    marks: dict[str, Any] = {}
    for key, given in by_key.items():
        status = max((g["status"] for g in given), key=lambda st: PRECEDENCE.get(st, 0))
        comments = [f"{g['reviewer'] or 'A reviewer'}: {g['comment']}" for g in given if g["comment"].strip()]
        marks[key] = {"status": status, "comment": "\n".join(comments), "reviewer": ", ".join(dict.fromkeys(g["reviewer"] for g in given if g["reviewer"])),
                      "file": ", ".join(dict.fromkeys(g["file"] for g in given)), "by": given}
    return marks


def _answer_rows(path: Path) -> list[dict[str, str]] | None:
    """Answered SME questions: a CSV with an answer column, or JSON with a questions list. None if the file is not one."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "answer" not in [c.strip().lower() for c in reader.fieldnames]:
                return None
            rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    else:
        data = json.loads(path.read_text())
        if not (isinstance(data, dict) and isinstance(data.get("questions"), list)):
            return None
        rows = [{k: str(v or "").strip() for k, v in q.items()} for q in data["questions"] if isinstance(q, dict)]
    return [r for r in rows if r.get("answer")]


def context(folder: RunFolder, source: Path | None, target: Path) -> dict[str, Any]:
    """Read the context folder by kind. Documents are copied (Word as text) to `target`, inside the Claude workspace."""
    found: dict[str, Any] = {"folder": str(source) if source else None, "documents": [], "marks": {}, "answers": [], "ignored": [], "reviews": [],
                             "signoffs": []}
    if source is None:
        return found
    sha = (folder.load("snapshot.json") or {}).get("sha256")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(p for p in source.rglob("*") if p.is_file() and not p.name.startswith(".")):
        name, suffix = path.relative_to(source).as_posix(), path.suffix.lower()
        try:
            review = review_file(path, name) if suffix in (".json", ".csv") else None
            if review is not None:
                if review.get("unusable"):
                    found["ignored"].append({"file": name, "why": review["unusable"]})
                elif review["sha256"] != sha:
                    found["ignored"].append({"file": name, "why": "a review of another snapshot"})
                else:
                    found["reviews"].append(review)
                continue
            if suffix in (".csv", ".json"):
                rows = _answer_rows(path)
                if rows is not None:
                    found["answers"] += [{**r, "file": name} for r in rows]
                    continue
            if suffix in TEXT + PDF:
                copy = target / name
            elif suffix in WORD:
                copy = (target / name).with_suffix(".txt")
            else:
                found["ignored"].append({"file": name, "why": "not a document, review export or answered questions file Reframe reads"})
                continue
            copy.parent.mkdir(parents=True, exist_ok=True)
            if suffix in WORD:
                copy.write_text(word_text(path))
            else:
                shutil.copyfile(path, copy)
            found["documents"].append({"file": name, "kind": suffix.lstrip("."), "bytes": path.stat().st_size,
                                       "read_as": copy.relative_to(target.parent.parent.parent).as_posix()})
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, UnicodeDecodeError, csv.Error) as error:
            found["ignored"].append({"file": name, "why": f"could not be read ({type(error).__name__})"})
    from reframe.reconcile.reviews import as_run_keys
    found["reviews"] = as_run_keys(folder, found["reviews"])  # a review of a published copy names items by cleaned keys
    found["marks"] = combine(found["reviews"])
    found["signoffs"] = signoffs(found["reviews"])
    return found


def recommended_covers(folder: RunFolder) -> dict[str, list[str]]:
    """Each recommended rule's key on the reconciliation page (rec/<unit>/<n>) -> the input rules it covers, from the
    last round's decisions and agreed rules."""
    from reframe.reconcile.units import reconciled_key
    found = {reconciled_key(r["key"], 1): list(r.get("covers") or []) for r in folder.load("reconcile/agreed.json", []) or []}
    for unit, d in (folder.load("reconcile/decisions.json", {}) or {}).items():
        for n, r in enumerate(d.get("rules") or [], 1):
            found[reconciled_key(unit, n)] = list(r.get("covers") or [])
    return found


def agreed_marks(marks: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Items marked Agree on the reconciled review (rules and requirements), with who agreed and when (section 8.11)."""
    return {k: [{"reviewer": b["reviewer"], "at": b.get("at", "")} for b in m.get("by") or [m] if b.get("status", m["status"]) == "agree"]
            for k, m in marks.items() if m["status"] == "agree" and k.startswith("rec/")}


def expand_marks(marks: dict[str, Any], covers: dict[str, list[str]]) -> dict[str, Any]:
    """A Needs change or Question mark made on the reconciliation page is on a recommended rule; it goes to every input
    rule that rule covers, so the next round decides that unit again. An Agree there keeps the unit (section 8.11), so it
    is not passed on. A mark made in a tool's own review stays on its rule."""
    marks = {k: v for k, v in marks.items() if not (k in covers and v["status"] == "agree")}
    found = {k: v for k, v in marks.items() if k not in covers}
    for key, mark in marks.items():
        for rule in covers.get(key, []):
            if rule in found and found[rule]["status"] != "agree":
                found[rule] = {**found[rule], "comment": (found[rule].get("comment", "") + "\n" + mark.get("comment", "")).strip()}
            else:
                found[rule] = {**mark, "on": key}
    return found


# ---- R1: everything, saved ---------------------------------------------------------------------------------------------

def gather(folder: RunFolder, context_folder: Path | None) -> dict[str, Any]:
    from reframe.claude.workspace import paths
    places = paths(folder)
    pairing = folder.load("outputs/comparison/ai-pairs.json") or {}
    all_rules = rules(folder)
    pairs = [p for p in pairing.get("pairs") or [] if p.get("aws") in all_rules and p.get("claude") in all_rules]
    read = context(folder, context_folder, places["analysis"] / "reframe" / "reconcile" / "context")
    read["agreed"] = agreed_marks(read["marks"])
    read["marks"] = expand_marks(read["marks"], recommended_covers(folder))
    asked = [{**q, "rules": [k for k in q.get("rules") or [] if k in all_rules]} for q in questions(folder)]
    gathered = {"at": iso(), "rules": all_rules, "pairs": pairs, "questions": asked, "context": read,
                "source_sha256": (folder.load("snapshot.json") or {}).get("sha256")}
    gathered["digest"] = hashlib.sha256(json.dumps({k: v for k, v in gathered.items() if k != "at"}, sort_keys=True).encode()).hexdigest()
    folder.save("reconcile/inputs/inputs.json", gathered)
    return gathered
