"""Pipeline 4, stage D1: the screen inventory (docs/pipeline-4.md, section 4, P4-02).

Every BMS map in the snapshot is read with no model: each mapset, map and field, with its position, length,
attributes, colour, highlight, initial text and picture strings. Each named field is checked against the map's
symbolic copybook, and every field against the map's edges and the other fields. The function keys come from two
places: the keys a screen shows (its labels, such as "F3=Exit") and the keys the programs that use its mapset handle
(EIBAID against DFHPFn, DFHENTER, DFHCLEAR and DFHPAn, directly or through CardDemo-style CCARD-AID flags), each with
its line. A key shown but not handled, or handled but not shown, is listed as such, not guessed.

    python -m reframe.design.inventory [--run <run>]

writes design/screens.json and the 3270 copies (reframe.design.legacy) in outputs/design/legacy/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BMS_SUFFIXES = (".bms",)
COBOL_SUFFIXES = (".cbl", ".cob")
COPY_SUFFIXES = (".cpy", "")
LINES, COLUMNS = 24, 80


# ---- BMS statements ----------------------------------------------------------------------------------------------------

@dataclass
class Statement:
    label: str
    op: str
    operands: dict[str, Any]
    line: int  # where the statement starts, 1-based


def statements(text: str) -> list[Statement]:
    """The assembler statements of a BMS source: a label in columns 1 to 8, an operation, and operands up to the first
    blank outside quotes; a non-blank in column 72 continues the statement on the next line from column 16. Comment lines
    start with *."""
    found, pending, start = [], "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n").expandtabs()
        if not pending and (line.startswith("*") or not line.strip()):
            continue
        continued = len(line) > 71 and line[71] != " "
        body = line[:71].ljust(71) if continued else line[:71]
        if pending:
            piece = body[15:] if len(body) > 15 else ""
            pending += piece if pending.count("'") % 2 else piece.lstrip()
        else:
            pending, start = body, n
        if continued:
            if not pending.count("'") % 2:  # outside a quoted string the operand ends at its last non-blank
                pending = pending.rstrip()
            continue
        found.append(_statement(pending, start))
        pending = ""
    if pending:
        found.append(_statement(pending, start))
    return [s for s in found if s.op]


def _statement(text: str, line: int) -> Statement:
    label = text[:8].strip() if text[:1] not in (" ", "") else ""
    rest = text[8:].lstrip() if label else text.lstrip()
    op, _, rest = rest.partition(" ")
    return Statement(label, op.upper(), operands(_until_blank(rest.lstrip())), line)


def _until_blank(text: str) -> str:
    """The operand field: up to the first blank outside quotes (what follows is a remark)."""
    quoted = False
    for i, c in enumerate(text):
        if c == "'":
            quoted = not quoted
        elif c == " " and not quoted:
            return text[:i]
    return text


def _split(text: str, sep: str = ",") -> list[str]:
    parts, depth, quoted, current = [], 0, False, ""
    for c in text:
        if c == "'":
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
        if c == sep and depth == 0 and not quoted:
            parts.append(current)
            current = ""
        else:
            current += c
    if current:
        parts.append(current)
    return parts


def _value(text: str) -> Any:
    text = text.strip()
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    if text.startswith("(") and text.endswith(")"):
        return [_value(p) for p in _split(text[1:-1])]
    return text


def operands(text: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for part in _split(text):
        key, eq, value = part.partition("=")
        if eq:
            found[key.strip().upper()] = _value(value)
        elif part.strip():
            found[part.strip().upper()] = True
    return found


# ---- maps and fields ---------------------------------------------------------------------------------------------------

@dataclass
class Field:
    name: str
    line: int
    column: int
    length: int
    attributes: list[str]
    color: str = ""
    highlight: str = ""
    initial: str = ""
    picin: str = ""
    picout: str = ""
    justify: str = ""
    occurs: int = 1
    source_line: int = 0

    @property
    def kind(self) -> str:
        """input (the user types in it), output (the program writes it), label (fixed text) or stopper."""
        if "UNPROT" in self.attributes:
            return "input"
        if self.name:
            return "output"
        return "label" if self.initial else "stopper"

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "line": self.line, "column": self.column, "length": self.length,
                "attributes": self.attributes, "color": self.color, "highlight": self.highlight, "initial": self.initial,
                "picin": self.picin, "picout": self.picout, "justify": self.justify, "occurs": self.occurs, "source_line": self.source_line}


@dataclass
class Map:
    mapset: str
    name: str
    lines: int
    columns: int
    file: str
    source_line: int
    fields: list[Field] = field(default_factory=list)
    title: str = ""


def _list(value: Any) -> list[str]:
    if value in (None, True, ""):
        return []
    return [str(v).upper() for v in (value if isinstance(value, list) else [value])]


def _position(value: Any, columns: int) -> tuple[int, int]:
    if isinstance(value, list) and len(value) == 2:
        return int(value[0]), int(value[1])
    offset = int(value)  # a single number is the offset from the map's start
    return offset // columns + 1, offset % columns + 1


def header_title(text: str) -> str:
    """The title in a BMS source's opening comment, such as "CardDemo - Login Screen"."""
    for line in text.splitlines()[:12]:
        if not line.startswith("*"):
            break
        words = line.strip("* ").strip()
        if words and not set(words) <= set("*-= ") and not re.search(r"copyright|licen[cs]e|rights reserved", words, re.I):
            return re.sub(r"^CardDemo\s*-\s*", "", words).strip()
    return ""


def read_bms(text: str, file: str) -> list[Map]:
    """Every map of a BMS source, with its fields."""
    maps: list[Map] = []
    mapset = ""
    for s in statements(text):
        if s.op == "DFHMSD":
            if str(s.operands.get("TYPE", "")).upper() == "FINAL":
                continue
            mapset = s.label
        elif s.op == "DFHMDI":
            size = s.operands.get("SIZE") or [LINES, COLUMNS]
            maps.append(Map(mapset, s.label, int(size[0]), int(size[1]), file, s.line))
        elif s.op == "DFHMDF" and maps:
            m = maps[-1]
            line, column = _position(s.operands.get("POS", 1), m.columns)
            occurs = int(s.operands.get("OCCURS", 1) or 1)
            m.fields.append(Field(s.label, line, column, int(s.operands.get("LENGTH", 0) or 0), _list(s.operands.get("ATTRB")),
                                  str(s.operands.get("COLOR", "") or "").upper(), str(s.operands.get("HILIGHT", "") or "").upper(),
                                  str(s.operands.get("INITIAL", "") or ""), str(s.operands.get("PICIN", "") or ""),
                                  str(s.operands.get("PICOUT", "") or ""), " ".join(_list(s.operands.get("JUSTIFY"))), occurs, s.line))
    for m in maps:
        m.title = _title(m) or header_title(text)
    return maps


def _title(m: Map) -> str:
    """The screen's own title: the longest label in its first four lines that is not a header caption."""
    labels = [f.initial.strip() for f in m.fields if f.kind == "label" and f.line <= 4 and len(f.initial.strip()) > 3
              and not f.initial.strip().endswith(":") and not set(f.initial.strip()) <= set("-=*_ ")]
    return max(labels, key=len) if labels else ""


def cells(f: Field, columns: int) -> list[tuple[tuple[int, int], bool]]:
    """The cells a field takes, each with whether it is the field's attribute byte (the cell before its data); data
    wraps at the map's width."""
    start = (f.line - 1) * columns + (f.column - 1)
    taken = []
    for occurrence in range(max(1, f.occurs)):
        first = start + occurrence * (f.length + 1)
        taken += [(divmod(first + i, columns), i < 0) for i in range(-1, f.length)]
    return [((line + 1, column + 1), attribute) for (line, column), attribute in taken]


def problems(m: Map, symbolic: str | None) -> list[str]:
    """What the inventory's checks found in a map: fields outside it, fields that overlap, and named fields missing from
    the symbolic map copybook."""
    found: list[str] = []
    taken: dict[tuple[int, int], tuple[Field, bool]] = {}
    name = lambda f: f.name or (f"the label '{f.initial.strip()[:20]}'" if f.initial.strip() else "an unnamed field")  # noqa: E731
    for f in m.fields:
        where = f"{name(f)} at line {f.line}, column {f.column} (BMS line {f.source_line})"
        if not (1 <= f.line <= m.lines and 1 <= f.column <= m.columns):
            found.append(f"{where} is outside the {m.lines} × {m.columns} map")
            continue
        end = (f.line - 1) * m.columns + f.column - 1 + f.length * max(1, f.occurs) + (max(1, f.occurs) - 1)
        if end > m.lines * m.columns:
            found.append(f"{where} runs past the end of the map")
        if f.length == 0:  # a zero-length stopper only ends the field before it
            continue
        for cell, attribute in cells(f, m.columns):
            if cell in taken and taken[cell][0] is not f:
                other, other_attribute = taken[cell]
                what = ("covers the data of" if not attribute and not other_attribute
                        else "has its attribute byte on the data of" if attribute and not other_attribute
                        else "has its data on the attribute byte of" if not attribute else "shares its attribute byte with")
                found.append(f"{where} {what} {name(other)} at line {other.line}, column {other.column}")
                break
            taken[cell] = (f, attribute)
    if symbolic is not None:
        for f in m.fields:
            if f.name and not re.search(rf"\b{re.escape(f.name)}I\b", symbolic, re.I):
                found.append(f"{f.name} is not in the symbolic map copybook")
    return found


# ---- function keys -----------------------------------------------------------------------------------------------------

SHOWN = re.compile(r"\b(ENTER|CLEAR|PA[1-3]|P?F(?:[1-9]|1[0-9]|2[0-4]))\s*=\s*([A-Za-z][A-Za-z0-9 /&',.-]*?)(?=[,.]?\s{2,}|[,.]?\s*$|\s+(?:ENTER|CLEAR|PA[1-3]|P?F\d{1,2})\s*=)",
                   re.I)
AID = re.compile(r"\b(?:DFH(ENTER|CLEAR|PA[1-3]|PF(?:[1-9]|1[0-9]|2[0-4]))|CCARD-AID-(ENTER|CLEAR|PA[1-3]|PFK(?:0?[1-9]|1[0-9]|2[0-4])))\b", re.I)
CONDITION = re.compile(r"^\s*(?:WHEN|IF|OR|AND|ELSE\s+IF)\b|\bEIBAID\b\s*(?:=|IS\s+EQUAL)", re.I)


def key_name(raw: str) -> str:
    """ENTER, CLEAR, PA1 or F1 to F24, from DFHPF3, CCARD-AID-PFK03, PF3 or F3."""
    raw = raw.upper()
    m = re.fullmatch(r"P?FK?0*(\d+)", raw)
    return f"F{int(m.group(1))}" if m else raw


def shown_keys(m: Map) -> list[dict[str, Any]]:
    found = []
    for f in m.fields:  # a label, or a field the map gives initial text
        for k in SHOWN.finditer(f.initial):
            found.append({"key": key_name(k.group(1)), "says": k.group(2).strip(), "line": f.line, "source_line": f.source_line})
    return found


def handled_keys(text: str, program: str) -> list[dict[str, Any]]:
    """The keys a program tests, each with its line and the statement that follows the test."""
    lines = text.splitlines()
    found = []
    for n, raw in enumerate(lines, 1):
        code = raw[6:72] if len(raw) > 6 else raw
        if code[:1] in ("*", "/") or re.search(r"\bSET\b", code, re.I):
            continue
        if not CONDITION.search(code):
            continue
        for k in AID.finditer(code):
            key = key_name(k.group(1) or k.group(2))
            action = ""
            for nxt in lines[n:n + 6]:
                body = (nxt[6:72] if len(nxt) > 6 else nxt).strip()
                if body and not body.startswith("*") and not CONDITION.search(body) and not AID.search(body):
                    action = body
                    break
            found.append({"key": key, "program": program, "line": n, "action": action})
    return found


# ---- the whole snapshot ------------------------------------------------------------------------------------------------

def _files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def code_only(text: str) -> str:
    """A fixed-format COBOL source with its comment lines (* or / in column 7) blanked."""
    return "\n".join("" if len(line) > 6 and line[6] in "*/" else line for line in text.splitlines())


def _copies(text: str) -> set[str]:
    return {m.group(1).upper() for m in re.finditer(r"\bCOPY\s+'?([A-Z0-9#@$-]+)'?", code_only(text), re.I)}


def inventory(legacy: Path) -> dict[str, Any]:
    """Every map in the snapshot with its fields, problems and function keys; the programs that use each mapset."""
    rel = lambda p: p.relative_to(legacy).as_posix()  # noqa: E731
    symbolic = {p.stem.upper(): p for p in _files(legacy, (".cpy",)) if "bms" in p.parent.name.lower()}
    programs = {}
    for p in _files(legacy, COBOL_SUFFIXES):
        text = p.read_text(errors="replace")
        programs[p.stem.upper()] = {"file": rel(p), "copies": _copies(text), "text": text}
    screens: list[dict[str, Any]] = []
    for p in _files(legacy, BMS_SUFFIXES):
        for m in read_bms(p.read_text(errors="replace"), rel(p)):
            copybook = symbolic.get(m.mapset.upper())
            users = sorted(name for name, prog in programs.items() if m.mapset.upper() in prog["copies"])
            handled = [k for name in users for k in handled_keys(programs[name]["text"], name)]
            shown = shown_keys(m)
            shown_set, handled_set = {k["key"] for k in shown}, {k["key"] for k in handled}
            screens.append({
                "mapset": m.mapset, "map": m.name, "title": m.title, "file": m.file, "source_line": m.source_line, "size": [m.lines, m.columns],
                "symbolic": rel(copybook) if copybook else "", "programs": [{"name": u, "file": programs[u]["file"]} for u in users],
                "fields": [f.as_json() for f in m.fields],
                "counts": {kind: sum(1 for f in m.fields if f.kind == kind) for kind in ("input", "output", "label", "stopper")},
                "keys": {"shown": shown, "handled": handled,
                         "shown_not_handled": sorted(shown_set - handled_set, key=_key_order) if users else [],
                         "handled_not_shown": sorted(handled_set - shown_set, key=_key_order)},
                "problems": problems(m, copybook.read_text(errors="replace") if copybook else None)
                + ([] if copybook else ["no symbolic map copybook found"]) + ([] if users else ["no program copies this mapset"]),
            })
    return {"screens": screens, "maps": len(screens), "mapsets": len({s["mapset"] for s in screens}),
            "fields": sum(len(s["fields"]) for s in screens), "problems": sum(len(s["problems"]) for s in screens)}


def _key_order(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"F(\d+)", key)
    return (1, int(m.group(1))) if m else (0 if key == "ENTER" else 2, 0)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from reframe.claude.workspace import paths
    from reframe.core.runfolder import find_run
    from reframe.design import legacy as drawing
    parser = argparse.ArgumentParser(prog="python -m reframe.design.inventory", description="The screen inventory of a run's snapshot (P4-02)")
    parser.add_argument("--run", help="the run (default: the latest run)")
    args = parser.parse_args(argv)
    folder = find_run(args.run)
    found = inventory(paths(folder)["legacy"])
    folder.save("design/screens.json", found)
    pages = drawing.draw_all(folder, found)
    print(json.dumps({"maps": found["maps"], "mapsets": found["mapsets"], "fields": found["fields"], "problems": found["problems"],
                      "pages": str(pages)}, indent=1))
    for s in found["screens"]:
        keys = s["keys"]
        print(f"{s['map']:8} {s['title'][:40]:40} fields {len(s['fields']):3}  programs {','.join(p['name'] for p in s['programs']) or '-':10}"
              f"  shown-not-handled {','.join(keys['shown_not_handled']) or '-':12} handled-not-shown {','.join(keys['handled_not_shown']) or '-'}")
        for p in s["problems"]:
            print("    problem:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
