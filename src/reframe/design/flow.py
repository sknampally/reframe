"""Pipeline 4, stage D2: how the screens connect (docs/pipeline-4.md, section 4, P4-03).

Read from the source with no model, each link citing its lines:

- transaction to program, from the CSD (DEFINE TRANSACTION ... PROGRAM);
- program to program, from EXEC CICS XCTL and LINK, and RETURN TRANSID to another transaction;
- a target held in a variable is resolved from the program's MOVEs into it and the VALUE clauses of the program and the
  copybooks it copies; a target taken from a table, such as a menu's option table, is resolved to every program in the
  table, each with the option it belongs to;
- function keys to targets: what the statements a key test leads to (and one paragraph they perform) move into a
  transfer's variable, or transfer to;
- program to screen, from the inventory (stage D1).

What the source cannot settle is kept as unresolved, with what was looked for; stage D2's model step reads only those.

    python -m reframe.design.flow [--run <run>]

writes design/flow.json and outputs/design/flow.html.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reframe.design.inventory import code_only

NAME = r"[A-Z0-9][A-Z0-9-]*"
CALLER = "(the screen it came from)"
LITERAL = re.compile(r"""^(['"])(.*)\1$""")
REF = re.compile(rf"^({NAME})(?:\s*\((.*)\))?$", re.I)


# ---- COBOL, enough of it -----------------------------------------------------------------------------------------------

def lines_of(text: str) -> list[str]:
    """The code area (columns 8 to 72) of each line of a fixed-format source, comments blanked; numbering kept."""
    out = []
    for line in code_only(text).splitlines():
        out.append(line[7:72] if len(line) > 7 else "")
    return out


@dataclass
class Item:
    level: int
    name: str
    pic: str
    value: str | None
    redefines: str
    occurs: int
    line: int
    file: str
    usage: str = ""


def data_items(text: str, file: str) -> list[Item]:
    """The data description entries of a source or copybook: level, name, PIC, VALUE (a literal), REDEFINES, OCCURS, USAGE."""
    entries, current, start = [], "", 0
    for n, body in enumerate(lines_of(text), 1):
        stripped = body.strip()
        if not stripped:
            continue
        if re.match(r"^\d{1,2}\s", stripped) and not current:
            current, start = stripped, n
        elif current:
            current += " " + stripped
        else:
            continue
        if current.rstrip().endswith("."):
            entries.append((current.rstrip(" ."), start))
            current = ""
    items = []
    for entry, line in entries:
        m = re.match(rf"^(\d{{1,2}})\s+({NAME})?(.*)$", entry, re.I)
        if not m:
            continue
        rest = m.group(3)
        value = re.search(r"\bVALUES?\s+(?:IS\s+|ARE\s+)?('(?:[^']|'')*'|\"[^\"]*\"|[+-]?\d+(?:\.\d+)?|ZEROS?|ZEROES|SPACES?)", rest, re.I)
        val = value.group(1) if value else None
        if val and val[0] in "'\"":
            val = val[1:-1].replace("''", "'")
        items.append(Item(int(m.group(1)), (m.group(2) or "FILLER").upper(), _first(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", rest),
                          val, _first(rf"\bREDEFINES\s+({NAME})", rest).upper(),
                          int(_first(r"\bOCCURS\s+(\d+)", rest, "0")), line, file,
                          _first(r"\b(COMP(?:UTATIONAL)?(?:-[1-5])?|BINARY|PACKED-DECIMAL|DISPLAY)\b", rest).upper()))
    return items


def tables(items: list[Item]) -> dict[str, list[dict[str, Any]]]:
    """Tables filled by VALUE clauses: a group of FILLERs with values, redefined as an OCCURS table. Returns, for each
    field of the table, the records in order, each record {field: value} with the line its values start on."""
    found: dict[str, list[dict[str, Any]]] = {}
    for i, item in enumerate(items):
        if not item.redefines:
            continue
        base = next((j for j, x in enumerate(items[:i]) if x.name == item.redefines), None)
        if base is None:
            continue
        leaves = [x for x in items[base + 1:i] if x.level > items[base].level and x.value is not None]
        fields, occurs = [], item.occurs
        for x in items[i + 1:]:
            if x.level <= item.level:
                break
            occurs = occurs or x.occurs
            if x.pic and x.name != "FILLER":
                fields.append(x.name)
        if not fields or not leaves or len(leaves) % len(fields):
            continue
        records = []
        for k in range(0, len(leaves), len(fields)):
            chunk = leaves[k:k + len(fields)]
            records.append({**{f: str(v.value).strip() for f, v in zip(fields, chunk)}, "_line": chunk[0].line, "_file": chunk[0].file})
        for f in fields:
            found[f] = records
    return found


@dataclass
class Program:
    name: str
    file: str
    lines: list[str]
    constants: dict[str, tuple[str, str, int]]  # name -> (value, file, line)
    tables: dict[str, list[dict[str, Any]]]
    paragraphs: dict[str, tuple[int, int]]  # name -> (first line, last line)


def _first(pattern: str, text: str, default: str = "") -> str:
    """Group 1 of the pattern's first match in the text, ignoring case, or the default when it does not match."""
    m = re.search(pattern, text, re.I)
    return m.group(1) if m else default


def _copybooks(legacy: Path) -> dict[str, Path]:
    return {p.stem.upper(): p for p in legacy.rglob("*") if p.is_file() and p.suffix.lower() in (".cpy", "")
            and p.parent.name.lower().startswith("cpy")}


def read_program(path: Path, legacy: Path, copybooks: dict[str, Path]) -> Program:
    text = path.read_text(errors="replace")
    rel = path.relative_to(legacy).as_posix()
    items = data_items(text, rel)
    for name in re.findall(rf"\bCOPY\s+'?({NAME})'?", code_only(text), re.I):
        book = copybooks.get(name.upper())
        if book:
            items += data_items(book.read_text(errors="replace"), book.relative_to(legacy).as_posix())
    constants = {x.name: (str(x.value), x.file, x.line) for x in items if x.value is not None and x.name != "FILLER"}
    body = lines_of(text)
    paragraphs: dict[str, tuple[int, int]] = {}
    start, name = 0, None  # start is set with name
    procedure = next((i for i, b in enumerate(body) if re.search(r"\bPROCEDURE\s+DIVISION\b", b, re.I)), len(body))
    for i in range(procedure + 1, len(body)):
        m = re.match(rf"^({NAME})\.\s*$", body[i][:4].strip() and body[i] or "", re.I)  # a paragraph name, starting in area A
        if m and not re.match(r"^(END-|EXIT$)", m.group(1), re.I):
            if name:
                paragraphs[name] = (start, i)
            name, start = m.group(1).upper(), i + 1
    if name:
        paragraphs[name] = (start, len(body))
    return Program(path.stem.upper(), rel, body, constants, tables(items), paragraphs)


def statements_with(program: Program, keyword: str) -> list[tuple[int, str]]:
    """EXEC CICS <keyword> blocks, joined to one line each, with the line they start on (1-based)."""
    found, lines = [], program.lines
    for i, body in enumerate(lines):
        if re.search(rf"\bEXEC\s+CICS\s+{keyword}\b", body, re.I) or (re.search(rf"^\s*{keyword}\b", body, re.I) and i and re.search(r"\bEXEC\s+CICS\s*$", lines[i - 1], re.I)):
            block, j = body, i
            while not re.search(r"\bEND-EXEC\b", block, re.I) and j + 1 < len(lines) and j - i < 12:
                j += 1
                block += " " + lines[j]
            found.append((i + 1 if re.search(r"\bEXEC\s+CICS\b", body, re.I) else i, re.sub(r"\s+", " ", block)))
    return found


def option(block: str, name: str) -> str | None:
    m = re.search(rf"\b{name}\s*\(\s*((?:[^()]|\([^()]*\))+?)\s*\)", block, re.I)
    return m.group(1).strip() if m else None


def moves_into(program: Program, var: str, within: tuple[int, int] | None = None) -> list[tuple[str, int]]:
    """(source, line) of every MOVE whose targets include `var`, optionally only within lines [first, last] (1-based)."""
    found = []
    first, last = within or (1, len(program.lines))
    for i in range(first - 1, min(last, len(program.lines))):
        body = program.lines[i]
        for m in re.finditer(rf"\bMOVE\s+('(?:[^']|'')*'|\"[^\"]*\"|{NAME}(?:\s*\([^)]*\))?)\s+TO\s+((?:{NAME}(?:\s*\([^)]*\))?\s*)+)", body, re.I):
            targets = [t.upper() for t in re.findall(NAME, m.group(2), re.I)]
            if var.upper() in targets:
                found.append((m.group(1), i + 1))
    return found


def resolve(program: Program, expr: str, depth: int = 0) -> list[dict[str, Any]]:
    """The values a program-name expression can have: a literal, a constant, a table field (every record), or a variable
    resolved through the MOVEs into it. Each value says how it was found and the line that shows it."""
    expr = expr.strip()
    lit = LITERAL.match(expr)
    if lit:
        return [{"value": lit.group(2).strip(), "how": "literal", "lines": []}]
    m = REF.match(expr)
    if not m or depth > 3:
        return []
    name = m.group(1).upper()
    if m.group(2) and name in program.tables:  # a table field: every record of the table
        return [{"value": r[name], "how": "table", "option": {k: v for k, v in r.items() if not k.startswith("_") and k != name},
                 "lines": [f"{r['_file']}:{r['_line']}"]} for r in program.tables[name] if r.get(name) and r[name].upper() != "DUMMY"]
    found = []
    if name in program.constants and not moves_into(program, name):
        value, file, line = program.constants[name]
        return [{"value": value.strip(), "how": "constant", "lines": [f"{file}:{line}"]}] if value.strip() else []
    for source, line in moves_into(program, name):
        values = resolve(program, source, depth + 1)
        if depth == 0 and (ref := REF.match(source)) and values and all(v["value"].upper() == program.name for v in values):
            # a variable the program only ever sets to itself, before transferring away: whoever comes next sends it back
            found.append({"value": program.name, "how": "caller", "lines": [f"{program.file}:{line}"], "via": ref.group(1).upper()})
            continue
        for v in values:
            found.append({**v, "how": v["how"] if v["how"] != "literal" else "moved", "lines": v["lines"] + [f"{program.file}:{line}"]})
    if not found and name in program.constants:
        value, file, line = program.constants[name]
        if value.strip():
            found.append({"value": value.strip(), "how": "constant", "lines": [f"{file}:{line}"]})
    return found


# ---- the flow ----------------------------------------------------------------------------------------------------------

def transactions(legacy: Path) -> list[dict[str, Any]]:
    found = []
    for path in sorted(p for p in legacy.rglob("*") if p.is_file() and p.suffix.lower() == ".csd"):
        text = path.read_text(errors="replace")
        for m in re.finditer(r"DEFINE\s+TRANSACTION\((\w+)\)(.*?)(?=\bDEFINE\b|\Z)", text, re.S | re.I):
            program = re.search(r"\bPROGRAM\((\w+)\)", m.group(2), re.I)
            if program:
                found.append({"transaction": m.group(1).upper(), "program": program.group(1).upper(),
                              "lines": [f"{path.relative_to(legacy).as_posix()}:{text[:m.start()].count(chr(10)) + 1}"]})
    return found


def fixed(program: Program, expr: str) -> bool:
    """A program name the program never changes: a literal, or a VALUE constant nothing moves into."""
    ref = REF.match(expr.strip())
    return bool(LITERAL.match(expr.strip()) or (ref and not ref.group(2) and ref.group(1).upper() in program.constants and not moves_into(program, ref.group(1))))


def _branch(program: Program, line: int) -> tuple[int, int]:
    """The lines a key test leads to: from the test to the WHEN, ELSE, END-EVALUATE or END-IF that closes it, counting
    the IFs and EVALUATEs nested inside; a period at the outer level also ends it."""
    end, depth, started = line, 0, False
    for i in range(line, min(len(program.lines), line + 80)):
        body = program.lines[i].strip()
        word = _first(r"^([A-Z-]+)", body).upper()
        if not started and depth == 0 and (word in ("WHEN", "AND", "OR", "") or body.startswith("(")):
            end = i + 1  # more of the condition, or a WHEN stacked on this one: they share the statements that follow
            continue
        started = True
        if depth == 0 and word in ("WHEN", "ELSE", "END-EVALUATE", "END-IF"):
            break
        if word in ("IF", "EVALUATE"):
            depth += 1
        elif word in ("END-IF", "END-EVALUATE"):
            depth -= 1
        end = i + 1
        if depth == 0 and body.endswith("."):
            break
    return line + 1, end


def flow(legacy: Path, screens: dict[str, Any]) -> dict[str, Any]:
    copybooks = _copybooks(legacy)
    programs = {p.stem.upper(): read_program(p, legacy, copybooks) for p in sorted(legacy.rglob("*"))
                if p.is_file() and p.suffix.lower() in (".cbl", ".cob")}
    by_tran: dict[str, dict[str, Any]] = {}
    trans = transactions(legacy)
    for t in trans:
        by_tran.setdefault(t["transaction"], t)
    links, unresolved = [], []

    def add(kind: str, source: str, target: str, lines: list[str], **extra: Any) -> None:
        if target and target != source:
            links.append({"kind": kind, "from": source, "to": target, "lines": lines, **extra})

    transfer_vars: dict[str, set[str]] = defaultdict(set)
    for name, prog in programs.items():
        for kind in ("XCTL", "LINK"):
            for line, block in statements_with(prog, kind):
                expr = option(block, "PROGRAM")
                if not expr:
                    continue
                where = f"{prog.file}:{line}"
                ref = REF.match(expr.strip())
                if ref and not LITERAL.match(expr.strip()):
                    transfer_vars[name].add(ref.group(1).upper())
                values = resolve(prog, expr)
                for v in values:
                    target = CALLER if v["how"] == "caller" else v["value"].upper()
                    add(kind.lower(), name, target, v["lines"] + [where], how=v["how"], **({"option": v["option"]} if v.get("option") else {}))
                if not values:
                    unresolved.append({"program": name, "kind": kind.lower(), "expression": expr, "lines": [where],
                                       "looked_for": "a literal, a VALUE constant, MOVEs into it, or a VALUE table"})
        for line, block in statements_with(prog, "RETURN"):
            expr = option(block, "TRANSID")
            if not expr:
                continue
            for v in resolve(prog, expr):
                target = by_tran.get(v["value"].upper())
                if target and target["program"] != name:
                    add("return", name, target["program"], v["lines"] + [f"{prog.file}:{line}"] + target["lines"], transaction=v["value"].upper())

    keys = []
    for s in screens.get("screens") or []:
        for k in s["keys"]["handled"]:
            handler = programs.get(k["program"])
            if not handler:
                continue
            prog = handler
            first, last = _branch(prog, k["line"])
            targets = []
            for var in transfer_vars.get(prog.name, set()):
                for source, line in moves_into(prog, var, (first, last)):
                    targets += [{**v, "lines": v["lines"] + [f"{prog.file}:{line}"]} for v in resolve(prog, source)]
            for ln, b in statements_with(prog, "XCTL"):  # a transfer in the branch to a name the program never changes
                if first <= ln <= last and fixed(prog, option(b, "PROGRAM") or ""):
                    targets += [{**v, "lines": v["lines"] + [f"{prog.file}:{ln}"]} for v in resolve(prog, option(b, "PROGRAM") or "")]
            if not targets:  # one level down: the paragraphs the branch performs
                for i in range(first - 1, last):
                    for para in re.findall(rf"\bPERFORM\s+({NAME})", prog.lines[i], re.I):
                        span = prog.paragraphs.get(para.upper())
                        if not span:
                            continue
                        for var in transfer_vars.get(prog.name, set()):
                            for source, line in moves_into(prog, var, span):
                                targets += [{**v, "lines": v["lines"] + [f"{prog.file}:{line}"]} for v in resolve(prog, source)]
                        for ln, b in statements_with(prog, "XCTL"):
                            if span[0] <= ln <= span[1] and fixed(prog, option(b, "PROGRAM") or ""):
                                targets += [{**v, "lines": v["lines"] + [f"{prog.file}:{ln}"]} for v in resolve(prog, option(b, "PROGRAM") or "")]
            seen = set()
            for t in targets:
                value = t["value"].upper()
                if value == prog.name and t.get("how") == "caller":
                    value = CALLER
                if value in seen or value == prog.name:
                    continue
                seen.add(value)
                add("key", prog.name, value, [f"{prog.file}:{k['line']}"] + t["lines"], key=k["key"], screen=s["map"])
            keys.append({"screen": s["map"], "program": prog.name, "key": k["key"], "line": k["line"], "to": sorted(seen)})

    for t in trans:
        add("transaction", t["transaction"], t["program"], t["lines"])

    unique, seen_links = [], set()
    for link in links:
        mark = (link["kind"], link["from"], link["to"], link.get("key"), tuple(sorted((link.get("option") or {}).items())))
        if mark not in seen_links:
            seen_links.add(mark)
            unique.append(link)

    screen_of = defaultdict(list)
    for s in screens.get("screens") or []:
        for p in s["programs"]:
            screen_of[p["name"]].append(s["map"])
    navigated = {l["to"] for l in unique if l["kind"] in ("xctl", "link", "key", "return")}
    started = {l["to"] for l in unique if l["kind"] == "transaction"}
    reach = []
    for s in screens.get("screens") or []:
        progs = [p["name"] for p in s["programs"]]
        how = ("navigated" if any(p in navigated for p in progs) else "transaction only" if any(p in started for p in progs) else "not reached")
        reach.append({"screen": s["map"], "title": s["title"], "programs": progs, "reached": how,
                      "transactions": sorted(t["transaction"] for t in trans if t["program"] in progs)})
    return {"links": unique, "unresolved": unresolved, "keys": keys, "reach": reach, "screens_of_programs": dict(screen_of),
            "transactions": trans, "counts": {k: sum(1 for l in unique if l["kind"] == k) for k in ("transaction", "xctl", "link", "return", "key")}}


def check(found: dict[str, Any], legacy: Path) -> list[str]:
    """Every cited line exists in the snapshot."""
    problems = []
    for link in found["links"]:
        for cite in link["lines"]:
            path, _, line = cite.rpartition(":")
            p = legacy / path
            if not p.is_file() or not (1 <= int(line) <= len(p.read_text(errors="replace").splitlines())):
                problems.append(f"{cite} does not exist")
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from reframe.claude.workspace import paths
    from reframe.core.runfolder import find_run
    from reframe.design import flowpage
    parser = argparse.ArgumentParser(prog="python -m reframe.design.flow", description="How a run's screens connect (P4-03)")
    parser.add_argument("--run", help="the run (default: the latest run)")
    args = parser.parse_args(argv)
    folder = find_run(args.run)
    legacy = paths(folder)["legacy"]
    screens = folder.load("design/screens.json")
    if not screens:
        from reframe.design.inventory import inventory
        screens = inventory(legacy)
        folder.save("design/screens.json", screens)
    found = flow(legacy, screens)
    found["problems"] = check(found, legacy)
    folder.save("design/flow.json", found)
    page = flowpage.draw(folder, screens, found)
    print(json.dumps({"counts": found["counts"], "unresolved": len(found["unresolved"]), "problems": len(found["problems"]),
                      "reach": {k: sum(1 for r in found["reach"] if r["reached"] == k) for k in ("navigated", "transaction only", "not reached")},
                      "page": str(page)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
