"""What Reframe answers when AWS asks (specification section 4, stage 6, and section 5).

Only shapes recorded from live runs are recognised; nothing here guesses an operation, component or schema.
A chat question is recognised by its closing line, because AWS puts it at the end of a longer message.

| AWS asks                               | Recorded                         | Autonomous answer                                    |
|----------------------------------------|----------------------------------|------------------------------------------------------|
| "How would you like to proceed?" menu  | 20 and 21 November 2026          | the plan "Assess and reimagine" and every setup answer |
| "Does this objective match your goals?"| 20 November 2026                 | yes, when AWS restates the chosen plan               |
| output languages                       | 20 November 2026                 | English only                                         |
| "Proceed with this plan?"              | 20 November 2026                 | yes                                                  |
| the business function catalog (SELECT) | spike 1, 21 November 2026        | every business domain and the unassigned programs    |
| "Do you want to proceed?" after it     | spike 1, 21 November 2026        | "Yes, proceed."                                      |
| the readiness menu after it            | 23 November 2026                 | its one numbered choice that starts with "Proceed"   |
| a name that is a domain and a function | 25 November 2026                 | the domain, as AWS asks for it                       |
| the source-location form               | 17, 20 and 21 November 2026      | the uploaded snapshot's S3 location                  |

Interactive mode turns each of these into a Question for reframe answer; the source-location form is
answered from the run's own record in both modes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLAN = "Assess and reimagine"
SOURCE_LOCATION_COMPONENT = "SpecifyAssetsLocation"
SOURCE_LOCATION_OPTIONAL = frozenset({"enableFeatures", "isDownloadButtonClicked", "optInChatBox"})
CATALOG_COMPONENT = "MainframeAssessmentBusinessProcessDiscoveryComponent"
"""The catalog's review form. Its submit is hidden in the web app; the selection goes to chat (spike 1)."""
PLAN_MENU = ("how would you like to proceed?",)
OBJECTIVE = ("does this objective match your goals?",)
OUTPUT_LANGUAGE = ("would you like your output translated into any of these languages, or would you prefer english only?",)
PROCEED_PLAN = ("proceed with this plan?",)
CONFIRM = ("do you want to proceed?",)
# AWS words its yes/no confirmations differently in every run (live runs, November 2026): "Reply "yes" to confirm or
# "no" to cancel.", "Reply "yes" to proceed with the reimagine workflow or "no" to cancel.", "Do you want to proceed?",
# "Please confirm: Proceed with the reimagine for these selections? (yes/no)". They are recognised by shape, not wording.
YES_NO = re.compile(r'\(\s*yes\s*/\s*no\s*\)|\breply\s+"?yes"?\b|"yes"\s+or\s+"no"|\byes\s+or\s+no\b')
CONFIRM_QUESTION = re.compile(r"\b(proceed|confirm)\b[^?]*\?")
# AWS asking again for the catalog selection it already got (live round 2, after its review form was submitted).
RESELECT = re.compile(r"\bselect the business functions?(\(s\))? to re-?imagine\b")
# Offers AWS posts after an action ("What would you like to do next?"); the job never waits on them.
OPEN_OFFER = re.compile(r"^(what would you like to do next\?|is there anything (else )?you'?d like)|\bfeel free to\b"
                        r"|\byou can (now )?monitor\b|\bask me for (status|progress)( updates)?\b|\byou'?ll be notified\b"
                        r"|\bno further action is needed\b|\byou can check on (the )?results\b")
# A confirmation worded as a statement: "Please confirm to proceed with the reimagine workflow for ..."
PLEASE_CONFIRM = re.compile(r"^please confirm\b")
# The readiness menu after the selection (live run, 23 November 2026): numbered choices ("Proceed with reimagining all
# 15 business functions and 25 unassigned programs", "Switch selection ...", "Drop unassigned programs ...") closing
# with "Please reply with your choice." It has no question mark, so it is recognised by that closing line.
REPLY_CHOICE = re.compile(r"^(please )?reply with (your|the|one) (choice|option|selection|number)\b")
# A name that is both a business domain and a business function (a live test run, 25 November 2026). AWS lists the two
# readings as options, bulleted or numbered, in its own words: "domain Web Service Interface Generation — reimagine all
# business functions in that domain" and "function Web Service Interface Generation — reimagine only that one business
# function", and asks which was meant ("Which did you intend?", "Which would you prefer?", or "Please reply with either
# "domain" or "function""). Recognised by those two options, not by the closing line.
CLASH_OPTION = re.compile(r"^\s*(?:[-*\u2022]|\d+\.)\s+(domain|function)\s+(.+?)\s*(?:\u2014|\u2013|\s-\s|:|$)", re.I | re.M)
BARE_WORD = re.compile(r'\breply with either\s+"?domain"?\s+or\s+"?function"?', re.I)


# AWS spelling out the reply it wants ("To move forward, please reply: **Yes, proceed**"); sent as given when affirmative.
INSTRUCTED = re.compile(r'\b(?:please\s+)?reply(?:\s+with)?\s*:\s*["“]?([^"”\n]{2,40}?)["”]?\s*[.!]?\s*$', re.I)
AFFIRMATIVE = re.compile(r"^(yes|proceed|confirm)\b", re.I)


def instructed_reply(text: str) -> str | None:
    lines = [line for line in text.replace("**", "").replace("__", "").strip().splitlines() if line.strip()]
    found = INSTRUCTED.search(lines[-1].strip()) if lines else None
    reply = found.group(1).strip() if found else None
    return reply if reply and AFFIRMATIVE.match(reply) else None


def is_confirmation(closing: str) -> bool:
    return bool(YES_NO.search(closing) or CONFIRM_QUESTION.search(closing) or PLEASE_CONFIRM.search(closing))
MENU_OPTION = re.compile(r"^\s*(\d+)\.\s+([^:\n]+?)\s*(?::|$)", re.M)
EVERYTHING = "Everything: every business domain in the catalog and the unassigned programs"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").replace("__", "").strip().casefold())


def closing_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return normalize(lines[-1]) if lines else ""


def needs_answer(text: str) -> bool:
    return "please wait" not in normalize(text) and "?" in text


def menu_options(text: str) -> tuple[tuple[str, str], ...]:
    options = tuple((number, label.strip()) for number, label in MENU_OPTION.findall(text))
    return options if len(options) >= 2 else ()


def plain(text: str) -> str:
    return text.replace("**", "").replace("__", "")


def offers_choices(text: str) -> bool:
    """AWS offers numbered choices or asks for a reply: the job waits on it. A notice does neither."""
    return bool(menu_options(plain(text))) or bool(re.search(r"\breply\b", closing_line(text)))


def name_clashes(text: str) -> list[str]:
    """The names AWS offers both as "domain <name>" and as "function <name>", in its order."""
    readings: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for reading, name in CLASH_OPTION.findall(plain(text)):
        key = normalize(name).strip('"\u201c\u201d')
        readings.setdefault(key, set()).add(reading.casefold())
        names.setdefault(key, name.strip().strip('"\u201c\u201d'))
    return [names[key] for key, seen in readings.items() if seen == {"domain", "function"}]


def setup_message(choice: str) -> str:
    """One reply with every setup answer, so AWS has nothing left to ask before it builds the plan. It names no
    workspace, job, connector or archive: AWS refuses identifiers in its chat."""
    return " ".join([f"{choice}.", "Output: English only, no translations.", "No collaborators are needed.",
                     "The Amazon Neptune cluster is already configured in this account, and this workspace already has an active "
                     "reimagine connector to it.",
                     "The source code archive is already uploaded; its location is given in the resource-location task.",
                     "These are all my choices: please generate the job plan now and proceed with it."])


@dataclass(frozen=True)
class Answer:
    """What to send: text, and the interaction it answers when it is a structured reply."""

    text: str
    reason: str
    interaction_origin_id: str | None = None


@dataclass(frozen=True)
class Question:
    """A question for reframe answer: AWS's text and the options Reframe can send."""

    id: str
    kind: str
    """plan, catalog, reselect, confirm, choice, clash, select, chat or form"""
    text: str
    options: tuple[str, ...] = ()
    replies: tuple[str, ...] = ()
    """The text sent for each option, in the same order."""
    interaction_origin_id: str | None = None
    schema: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "text": self.text, "options": list(self.options), "replies": list(self.replies),
                "interaction_origin_id": self.interaction_origin_id, "schema": self.schema}


# ---- chat ----------------------------------------------------------------------------------------


def select_interaction(message: dict[str, Any]) -> dict[str, Any] | None:
    for interaction in message.get("interactions") or []:
        if interaction.get("actionType") == "SELECT":
            return interaction
    return None


def select_options(interaction: dict[str, Any]) -> list[dict[str, Any]]:
    data = interaction.get("data") or {}
    return list((data.get("selectInteractionData") or data).get("options") or [])


def catalog_domains(text: str) -> list[str]:
    """The business domains in the catalog's table (its second column), in AWS's order and wording."""
    rows = [line.strip().strip("|").split("|") for line in text.splitlines()
            if line.startswith("|") and not line.startswith("|---") and not line.startswith("| #")]
    return list(dict.fromkeys(cells[1].strip() for cells in rows if len(cells) > 2 and cells[1].strip()))


def everything_reply(text: str) -> str | None:
    domains = catalog_domains(text)
    if not domains:
        return None
    reply = "Reimagine " + ", ".join(f'business domain "{name}"' for name in domains)
    if "unassigned programs" in normalize(text):
        reply += ", and unassigned programs"
    return reply


def _combine(values: list[str]) -> str:
    """Several of AWS's options as one reply, the way spike 1 sent them: 'Reimagine A, B and C'."""
    parts = [re.sub(r"^reimagine\s+", "", value.strip(), flags=re.I) for value in values]
    return "Reimagine " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])


def chat_question(message: dict[str, Any], awaiting: bool = False) -> Question | None:
    """The question in one of AWS's chat messages, with Reframe's options for it; None when it is only a notice.
    While AWS says the job awaits input, its latest message is treated as a question even without a question mark."""
    text = str(message.get("text") or "")
    message_id = str(message.get("messageId"))
    closing = closing_line(text)
    interaction = select_interaction(message)
    if interaction is not None:
        options = select_options(interaction)
        labels = [str(o.get("label") or o.get("value")) for o in options]
        replies = [str(o.get("value") or o.get("label")) for o in options]
        everything = everything_reply(text)
        kind = "catalog" if everything else "select"
        if everything:
            labels, replies = [EVERYTHING, *labels], [everything, *replies]
        return Question(message_id, kind, text, tuple(labels), tuple(replies), interaction_origin_id=message_id)
    clashes = name_clashes(text)
    if clashes:
        bare = BARE_WORD.search(plain(text)) and len(clashes) == 1
        choices = ("domain", "function") if bare else tuple(", ".join(f"{reading} {name}" for name in clashes) for reading in ("domain", "function"))
        listed = ", ".join(f'"{name}"' for name in clashes)
        return Question(message_id, "clash", text, (f"The business domain {listed}: every business function in it",
                                                    f"The business function {listed} only"), choices)
    if closing in PLAN_MENU:
        numbered = menu_options(text)
        return Question(message_id, "plan", text, tuple(label for _, label in numbered),
                        tuple(setup_message(f"{number}. {label}") for number, label in numbered))
    if closing in OBJECTIVE:
        return Question(message_id, "confirm", text, ("Yes", "No"), ("Yes, this objective matches my goals.", "No."))
    if closing in OUTPUT_LANGUAGE:
        return Question(message_id, "confirm", text, ("English only",), ("English only, no translations.",))
    if closing in PROCEED_PLAN:
        return Question(message_id, "confirm", text, ("Yes", "No"), ("Yes, proceed with this plan.", "No."))
    if REPLY_CHOICE.search(closing):
        numbered = menu_options(plain(text))
        if numbered:
            return Question(message_id, "choice", text, tuple(label for _, label in numbered),
                            tuple(f"{number}. {label}" for number, label in numbered))
    if RESELECT.search(normalize(text)):
        return Question(message_id, "reselect", text)
    instructed = instructed_reply(text)
    if instructed:
        return Question(message_id, "confirm", text, (instructed, "No"), (instructed, "No."))
    if YES_NO.search(closing):
        return Question(message_id, "confirm", text, ("Yes", "No"), ("yes", "no"))
    if closing in CONFIRM or CONFIRM_QUESTION.search(closing) or PLEASE_CONFIRM.search(closing):
        return Question(message_id, "confirm", text, ("Yes, proceed", "No"), ("Yes, proceed.", "No."))
    if OPEN_OFFER.search(closing):
        return None
    if needs_answer(text) or (awaiting and "please wait" not in normalize(text)):
        return Question(message_id, "chat", text)
    return None


def autonomous(question: Question) -> Answer | None:
    """Reframe's own answer in autonomous mode, or None when the question waits for reframe answer."""
    if question.kind == "plan":
        chosen = [reply for label, reply in zip(question.options, question.replies) if normalize(label) == normalize(PLAN)]
        return Answer(chosen[0], f"the plan menu, answered with the plan {PLAN!r} and every setup answer") if len(chosen) == 1 else None
    if question.kind == "catalog":
        return Answer(question.replies[0], "the business function catalog: every business domain and the unassigned programs, "
                                           "so the full source is assessed", question.interaction_origin_id)
    if question.kind == "confirm":
        if "objective" in closing_line(question.text) and PLAN.casefold() not in normalize(question.text):
            return None
        return Answer(question.replies[0], f"AWS asked to confirm ({closing_line(question.text)!r}); Reframe confirmed")
    if question.kind == "choice":
        proceed = [reply for label, reply in zip(question.options, question.replies) if normalize(label).startswith("proceed")]
        if len(proceed) == 1:
            return Answer(proceed[0], "AWS's readiness menu after the selection: the one choice that proceeds with everything selected, "
                                      "so the full source is assessed")
        return None
    if question.kind == "clash":
        return Answer(question.replies[0], "a name that is both a business domain and a business function: Reframe's selection names "
                                           "business domains only, so it meant the domain")
    if question.kind == "select":
        return None  # AWS marks no default on the SELECT interactions recorded so far
    return None


def interactive(question: Question, choices: list[int], text: str | None) -> Answer:
    """The person's answer through reframe answer: option numbers (1-based) or free text."""
    if text:
        return Answer(text, "answered by the person with reframe answer --text", question.interaction_origin_id)
    if not choices:
        raise ValueError("give option numbers or --text")
    if any(not 1 <= c <= len(question.options) for c in choices):
        raise ValueError(f"options are 1 to {len(question.options)}")
    if len(choices) > 1 and question.kind not in ("catalog", "select"):
        raise ValueError("this question takes one option")
    replies = [question.replies[c - 1] for c in choices]
    reply = replies[0] if len(replies) == 1 else _combine(replies)
    return Answer(reply, "answered by the person with reframe answer " + " ".join(map(str, choices)), question.interaction_origin_id)


# ---- forms ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormDecision:
    action: str
    """answer, approve, ignore or ask"""
    reason: str
    content: dict[str, Any] | None = None


def form_decision(component: str | None, schema: dict[str, Any], blocking: str | None, source_uri: str | None) -> FormDecision:
    schema = schema or {}
    if schema.get("displayOnly") is True:
        return FormDecision("ignore", "display-only; AWS submits it itself")
    if component == SOURCE_LOCATION_COMPONENT:
        properties = schema.get("properties") or {}
        exact = (schema.get("required") == ["assetLocation"] and "assetLocation" in properties
                 and set(properties) - {"assetLocation"} <= SOURCE_LOCATION_OPTIONAL
                 and (properties["assetLocation"] or {}).get("type") == "string")
        if not exact or not source_uri:
            return FormDecision("ask", "the source-location form has a shape Reframe has not seen")
        return FormDecision("answer", "the source location: the uploaded snapshot's S3 location", {"assetLocation": source_uri})
    if component == CATALOG_COMPONENT:
        return FormDecision("ignore", "the catalog's review form; the selection is sent in chat, as its own hint says")
    if blocking == "NON_BLOCKING":
        return FormDecision("ignore", "non-blocking; the job does not wait on it")
    required = schema.get("required") or []
    if schema.get("type") == "object" and not required and schema.get("additionalProperties") is not True:
        return FormDecision("approve", "an approval with nothing to fill in")
    return FormDecision("ask", "a form Reframe cannot answer from the run's records")


def catalog_form_content(task: dict[str, Any], source_uri: str | None) -> dict[str, Any] | None:
    """AWS's own response template for the catalog's review form, when it has exactly the fields AWS names and points at
    the business-functions delivery in the run's own bucket; None otherwise (the person is asked)."""
    template = task.get("_responseTemplate")
    bucket = (source_uri or "").removeprefix("s3://").split("/", 1)[0]
    if not isinstance(template, dict) or set(template) != {"isBPDEnabled", "s3Uri"} or not bucket:
        return None
    uri = template["s3Uri"]
    if template["isBPDEnabled"] is not True or not isinstance(uri, str) or not uri.startswith(f"s3://{bucket}/transform-output/") \
            or not uri.endswith("/business-functions/delivery.zip"):
        return None
    return dict(template)


def fits_schema(schema: Any, value: Any) -> bool:
    types: dict[str, type | tuple[type, ...]] = {"string": str, "boolean": bool, "array": list, "object": dict, "integer": int, "number": (int, float)}
    if not isinstance(schema, dict):
        return True
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if isinstance(kind, str) and kind in types:
        if kind in ("integer", "number") and isinstance(value, bool):
            return False
        if not isinstance(value, types[kind]):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        if not set(schema.get("required") or []) <= set(value):
            return False
        if properties and schema.get("additionalProperties") is not True and not set(value) <= set(properties):
            return False
        return all(fits_schema(properties.get(key), item) for key, item in value.items())
    if isinstance(value, list):
        return all(fits_schema(schema.get("items"), item) for item in value)
    return True
