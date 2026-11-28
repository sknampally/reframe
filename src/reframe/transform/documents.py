"""Reading AWS Transform's requirements documents and business function catalog in full (specification section 4, stages 7 and 8).

Two sources:

- AWS's business function catalog (`outputs/catalog/business_domains.json` and `business_function.csv`) gives the
  domain and function names and descriptions. The requirement folders only carry squeezed names such as
  `AccountBalanceandTransactionProcessing`.
- Each function's `requirements.md`. Its shape, from every live run so far:

      # <Function> — Requirements
      ## Global Preconditions                 notes for the whole function (also Job Dependencies, Generation Notes)
      ## 1. <Capability>
      As a <role>, I want <goal> so that <benefit>.
      **Restart/Recovery:** ...              also Schedule, Recovery, Category
      ### Requirements
      REQ-F-001: [Event-driven] When ..., the system shall ...
      ### Non-Functional Requirements
      REQ-N-001: [Ubiquitous] The system shall ...
      ### Open Questions
      OQ-001: <question> — Owner: <who>

  Requirement and question numbers are unique within a function.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

CAPABILITY = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
NOTE_SECTION = re.compile(r"^##\s+(.+?)\s*$")
TITLE = re.compile(r"^#\s+(.+?)(?:\s+[—–-]+\s+Requirements)?\s*$")
REQUIREMENT = re.compile(r"^(REQ-([A-Z]+)-\d+):\s*(?:\[([^\]]+)\]\s*)?(.*)$")
QUESTION = re.compile(r"^(OQ-\d+):\s*(.*)$")
BULLET = re.compile(r"^(?:[-*]|\d+\.)\s+(.*)$")
OWNER = re.compile(r"\s*(?:[—–-]{1,2}\s*)?\(?\bOwner:\s*([^()]+?)\)?\.?\s*$")  # "— Owner: x", "(Owner: x)" or "Owner: x."
# A requirement AWS could not back with its cited rule, moved to the open questions for a person to confirm.
UNVERIFIED = re.compile(r"^\[Unverified\b[^\]]*\]\s*")
FIELD = re.compile(r"^\*\*([^*]+?):\*\*\s*(.*)$")
TECHNICAL_NOTES = {"generation notes"}


def squeeze(name: str) -> str:
    """The key AWS's folder names keep: letters and digits only, lower case."""
    return re.sub(r"[^a-z0-9]", "", str(name).casefold())


def spaced(folder: str) -> str:
    """A readable name from a folder name, when the catalog has none: `UserAccessControl` -> `User Access Control`."""
    text = re.sub(r"[-_]+", " ", folder)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).strip()


# ---- the catalog ---------------------------------------------------------------------------------


def catalog(outputs: Path) -> dict[str, Any]:
    """Domains and functions from AWS's catalog, keyed by their squeezed names, in AWS's order."""
    domains: dict[str, dict[str, str]] = {}
    functions: dict[str, dict[str, str]] = {}
    root = outputs / "catalog"
    try:
        listed = json.loads((root / "business_domains.json").read_text())
    except (OSError, ValueError):
        listed = {}
    for item in (listed.get("domains") if isinstance(listed, dict) else listed) or []:
        if isinstance(item, dict) and item.get("name"):
            domains[squeeze(item["name"])] = {"name": str(item["name"]), "description": str(item.get("description") or "")}
    try:
        with (root / "business_function.csv").open(newline="", errors="replace") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        rows = []
    for row in rows:
        name = (row.get("bf_name") or "").strip()
        if not name or squeeze(name) in functions:
            continue
        domain = (row.get("bd_name") or "").strip()
        functions[squeeze(name)] = {"name": name, "description": (row.get("bf_description") or "").strip(), "domain": domain}
        if domain and squeeze(domain) not in domains:
            domains[squeeze(domain)] = {"name": domain, "description": (row.get("bd_description") or "").strip()}
    return {"domains": domains, "functions": functions}


# ---- one requirements document ---------------------------------------------------------------------


def parse(text: str) -> dict[str, Any]:
    """The title, the function-wide notes, and every capability with its story, fields, requirements and questions."""
    document: dict[str, Any] = {"title": "", "notes": [], "capabilities": []}
    capability: dict[str, Any] | None = None
    note: dict[str, Any] | None = None
    part = ""
    item: dict[str, Any] | None = None  # the requirement or question a wrapped line continues

    def new_capability(number: str, title: str) -> dict[str, Any]:
        return {"number": int(number), "title": title, "story": [], "fields": {}, "requirements": [], "questions": [], "other": []}

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# ") and not document["title"]:
            match = TITLE.match(line)
            document["title"] = match.group(1) if match else line[2:]
            continue
        if line.startswith("## "):
            item, part = None, ""
            match = CAPABILITY.match(line)
            if match:
                capability, note = new_capability(match.group(1), match.group(2)), None
                document["capabilities"].append(capability)
            else:
                capability = None
                section = NOTE_SECTION.match(line)
                assert section is not None  # the line starts with "## ", which NOTE_SECTION always matches
                heading = section.group(1)
                note = {"heading": heading, "markdown": [], "technical": heading.casefold() in TECHNICAL_NOTES}
                document["notes"].append(note)
            continue
        if note is not None:
            note["markdown"].append(raw)
            continue
        if capability is None:
            continue
        if line.startswith("### "):
            part, item = line[4:].strip().casefold(), None
            continue
        if not line or re.fullmatch(r"-{3,}|\*{3,}", line):
            item = None
            continue
        if not part:
            field = FIELD.match(line)
            if field:
                capability["fields"][field.group(1).strip()] = field.group(2).strip()
            else:
                capability["story"].append(line)
            continue
        requirement = REQUIREMENT.match(line)
        if requirement:
            item = {"code": requirement.group(1), "kind": requirement.group(2), "pattern": requirement.group(3) or "",
                    "text": requirement.group(4).strip()}
            capability["requirements"].append(item)
            continue
        question = QUESTION.match(line)
        bullet = BULLET.match(line) if "question" in part else None
        if question or bullet:
            if question:
                body, code = question.group(2), question.group(1)
            else:
                assert bullet is not None
                body, code = bullet.group(1), f"Q-{len(capability['questions']) + 1:03d}"
            item = {"code": code, "text": body.strip(), "owner": "", "unverified": False}
            capability["questions"].append(item)
            continue
        if item is not None:
            item["text"] += " " + line
            continue
        capability["other"].append(raw)

    for capability in document["capabilities"]:
        capability["story"] = " ".join(capability["story"])
        for question in capability["questions"]:
            owner = OWNER.search(question["text"])
            if owner:
                question["owner"] = owner.group(1)
                question["text"] = question["text"][:owner.start()].rstrip()
            unverified = UNVERIFIED.match(question["text"])
            if unverified:
                question["unverified"] = True
                question["text"] = question["text"][unverified.end():]
        capability["other"] = "\n".join(capability["other"]).strip()
    for note in document["notes"]:
        note["markdown"] = "\n".join(note["markdown"]).strip()
    return document


def count_questions(text: str) -> int:
    """Every open question in one requirements document, counted once."""
    return sum(len(capability["questions"]) for capability in parse(text)["capabilities"])
