"""Stages C2 to C4: the plugin's preflight, assess, map and rule extraction, each run headless (docs/pipeline-2.md).

Each step leaves a receipt in claude-steps/<step>.json with its outcome, time, turns, tokens and cost, so a resumed
run skips what is done. A step that ends on Claude's usage limit or a sign-in problem stops the run; `reframe resume`
continues it (rule extraction resumes its own session, so finished agents are not paid for twice).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from reframe.claude.runner import ClaudeCode, StepResult
from reframe.core.errors import Outcome, Stop
from reframe.core.runfolder import RunFolder, iso

PREFIX = "/code-modernization:modernize-"
EXPECTED = {"preflight": "PREFLIGHT.md", "assess": "ASSESSMENT.md", "map": "topology.json", "extract-rules": "BUSINESS_RULES.md"}

# The plugin's five preflight questions (commands/modernize-preflight.md, Check 0), with Reframe's answers for autonomous mode.
QUESTIONS = [
    ("Scope", "Is this code the complete system, or one slice of a larger codebase?",
     "The complete source as Reframe snapshotted it (SHA-256 {sha256}); nothing outside it is in scope for this assessment."),
    ("Build & test locally", "Can this environment restore, build, and run the tests? Roughly how long does the full CI pipeline take?",
     "No. This pass is analysis only: nothing is built or run."),
    ("Bespoke build infrastructure", "Is there organization-specific build or dependency machinery a newcomer would not guess?",
     "Don't know."),
    ("Prior attempts", "Has anyone tried to modernize this before? What went wrong?",
     "An AWS Transform assessment of the same snapshot was made; its results are kept apart and are not given to this assessment."),
    ("Off limits", "Is anything in this code not allowed to change in this pass?",
     "Everything: this pass is analysis only, and nothing in the source may change."),
]

STEP_HOURS = {"preflight": 0.5, "assess": 1, "map": 1.5, "extract-rules": 4}


def receipts(folder: RunFolder) -> dict[str, Any]:
    out = {}
    for path in sorted(folder.root.glob("claude-steps/*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def _save(folder: RunFolder, step: str, value: dict[str, Any]) -> None:
    folder.save(f"claude-steps/{step}.json", value)


def stop_for(result: StepResult, step: str) -> Stop:
    if result.problem == "limit":
        return Stop(Outcome.WAITING_HUMAN, f"Claude's usage limit was reached during {step}. When it resets, run reframe resume; "
                                           "the step continues where it stopped")
    if result.problem == "signin":
        return Stop(Outcome.WAITING_SIGNIN, f"Claude could not sign in during {step}: {result.text[:200]}. "
                                            "Sign in (claude auth login, or the AWS sign-in for Bedrock), then run reframe resume")
    return Stop(Outcome.FAILED, f"the plugin's {step} step did not finish: {result.text[-300:]}")


MAX_FOLLOW_UPS = 3


def extraction_gaps(analysis: Path) -> dict[str, Any] | None:
    """What the plugin's own result says is missing after rule extraction: modules that failed or must run again (for
    example cut off by Claude's usage limit), and phases it skipped. None when nothing is missing (spike P2-01, f6bb30)."""
    path = analysis / "rules_result.json"
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    rerun = [m.get("name") if isinstance(m, dict) else str(m) for m in result.get("rerunModules") or []]
    failed = [str(m) for m in stats.get("failedModules") or []]
    skipped = [str(m) for m in stats.get("skippedPhases") or []]
    if not rerun and not failed:
        return None
    return {"rerun": rerun, "failed": failed, "skipped_phases": skipped, "confirmed": stats.get("confirmed") or result.get("confirmedRules")}


def follow_up_prompt(system: str) -> str:
    return (f"The last {PREFIX}extract-rules {system} run ended with modules to run again (rerunModules in "
            f"analysis/{system}/rules_result.json). Run the follow-up the command describes: invoke the extract-rules workflow by name for "
            f"the same system with args.modules set to exactly those modules (taken from analysis/{system}/extract-rules.modules.json) and "
            f"no resumeFromRunId. Merge its confirmedRules, dataObjects and stats into analysis/{system}/rules_result.json, de-duplicating "
            "by source and name; leave in rerunModules and failedModules only what is still missing; then render BUSINESS_RULES.md and "
            "DATA_OBJECTS.md again with the plugin's render_rules.py. The question pop-up is not available here; do not ask.")


def run_step(folder: RunFolder, claude: ClaudeCode, analysis: Path, step: str, prompt: str, resume_prompt: str | None = None,
             system: str | None = None) -> dict[str, Any]:
    done = folder.load(f"claude-steps/{step}.json")
    if done and done.get("ok"):
        return done
    attempt = (done or {}).get("attempt", 0) + 1
    resume = (done or {}).get("session_id") if done and done.get("problem") == "limit" and resume_prompt else None
    follow_up = bool(done and done.get("problem") == "incomplete" and system)
    if follow_up and (done or {}).get("follow_ups", 0) >= MAX_FOLLOW_UPS:
        raise Stop(Outcome.FAILED, f"rule extraction still has modules missing after {MAX_FOLLOW_UPS} follow-up runs; see claude-steps/{step}.json")
    folder.event("claude_step", step=step, attempt=attempt, resume=bool(resume), follow_up=follow_up)
    _save(folder, step, {"ok": False, "attempt": attempt, "started_at": iso(), "session_id": (done or {}).get("session_id"),
                         "follow_ups": (done or {}).get("follow_ups", 0) + (1 if follow_up else 0)})
    if follow_up and system:  # follow_up needs system
        text = follow_up_prompt(system)
    elif resume and resume_prompt:  # resume needs resume_prompt
        text = resume_prompt
    else:
        text = prompt
    result = claude.run(f"{step}-{attempt}", text, timeout=STEP_HOURS.get(step, 2) * 3600, resume=None if follow_up else resume)
    expected = analysis / EXPECTED[step]
    if result.ok and not expected.exists():
        result.ok, result.problem = False, "failed"
        result.text = f"{EXPECTED[step]} was not written. {result.text[-300:]}"
    gaps = extraction_gaps(analysis) if step == "extract-rules" and result.ok else None
    if gaps:  # the plugin finished its turn, but its own result lists modules that did not run
        result.ok, result.problem = False, "incomplete"
    record = {**result.as_json(), "attempt": attempt, "finished_at": iso(), "step": step, "gaps": gaps,
              "follow_ups": (done or {}).get("follow_ups", 0) + (1 if follow_up else 0)}
    _save(folder, step, record)
    folder.event("claude_step_done", step=step, ok=result.ok, minutes=round(result.seconds / 60, 1), cost_usd=round(result.cost_usd, 2),
                 denials=len(result.denials))
    if gaps:
        raise Stop(Outcome.WAITING_HUMAN, f"rule extraction finished with {len(gaps['rerun'] or gaps['failed'])} modules missing "
                                          f"(for example {(gaps['rerun'] or gaps['failed'])[0]}), most likely cut off by Claude's usage limit"
                                          + (f", and skipped {', '.join(gaps['skipped_phases'])}" if gaps["skipped_phases"] else "")
                                          + ". When the limit has reset, reframe resume runs the plugin's follow-up for just those modules")
    if not result.ok:
        raise stop_for(result, step)
    return record


def preflight_answers(folder: RunFolder, mode: str, sha256: str, ask: Callable[[str, str, list[str]], str]) -> list[tuple[str, str, str]]:
    """The five answers: Reframe's in autonomous mode (recorded as decisions), the person's in interactive mode."""
    saved = folder.load("claude-preflight-answers.json")
    if saved:
        return [tuple(item) for item in saved]
    answers = []
    for number, (title, question, default) in enumerate(QUESTIONS, 1):
        suggested = default.format(sha256=sha256)
        if mode == "interactive":
            text = ask(f"claude-preflight-{number}", f"The code-modernization plugin's preflight question {number} of 5, {title}:\n\n{question}",
                       [f"Reframe's answer: {suggested}", "Don't know"])
            answer, by = text, "the person"
        else:
            answer, by = suggested, "Reframe (autonomous mode)"
            folder.decide(f"claude-preflight-{number}", answer, "Reframe answers the plugin's preflight questions in autonomous mode from "
                          "what it knows about the run; a person can change them in PREFLIGHT.md", step="claude preflight")
        answers.append((title, answer, by))
    folder.save("claude-preflight-answers.json", answers)
    return answers


def preflight_prompt(system: str, answers: list[tuple[str, str, str]]) -> str:
    lines = [f"{PREFIX}preflight {system}", "",
             "The person's answers to the five Check 0 questions were collected before this run (the question pop-up is not "
             "available here). Write each one verbatim under its question in the Answers section, with who gave it, and do not ask again:"]
    for number, (title, answer, by) in enumerate(answers, 1):
        lines.append(f"{number}. {title}: {answer} (answered by {by})")
    return "\n".join(lines)


# ---- rule extraction's size, before it starts -------------------------------------------------------


def estimate(workspace: Path, plugin_dir: Path, system: str, env: dict[str, str]) -> dict[str, Any]:
    """The plugin's own shard list (scripts/make_shards.py) and its rule of thumb: 10 to 15 agents per shard, about 16k tokens
    per agent; a tiny estate (lens mode) is 15 to 40 agents."""
    result = subprocess.run(["python3", str(plugin_dir / "scripts" / "make_shards.py"), system, ""], cwd=str(workspace), env=env,
                            capture_output=True, text=True, timeout=600)
    output = (result.stdout + result.stderr).strip()
    modules = workspace / "analysis" / system / "extract-rules.modules.json"
    shards = 0
    if modules.exists():
        try:
            value = json.loads(modules.read_text())
            shards = len(value if isinstance(value, list) else value.get("modules") or value.get("shards") or [])
        except ValueError:
            shards = 0
    if not shards:
        match = re.search(r"(\d+)\s+shards?", output)
        shards = int(match.group(1)) if match else 0
    tiny = "tiny estate" in output.casefold()
    low, high = (15, 40) if tiny or shards == 0 else (shards * 10, shards * 15)
    return {"shards": shards, "tiny": tiny, "agents_low": low, "agents_high": high, "tokens_high": high * 16_000,
            "minutes_high": max(5, round(high / 8)), "script_output": output[-1500:]}


def extract_prompt(system: str, size: dict[str, Any], limit: int) -> str:
    return (f"{PREFIX}extract-rules {system}\n\n"
            f"Reframe ran the plugin's shard builder and showed this estimate to the person: {size['shards']} shards, about "
            f"{size['agents_low']} to {size['agents_high']} agents. It is within the agent limit they set ({limit} agents), so the "
            "run is authorized: launch all shards now without asking again (the question pop-up is not available here).")


def extract_resume_prompt(system: str) -> str:
    return (f"Continue {PREFIX}extract-rules {system}. If the workflow stopped, resume it with its run ID (resumeFromRunId) "
            "exactly as the command describes, never restarting it, then save rules_result.json and render BUSINESS_RULES.md.")


def wait_for_answer(folder: RunFolder, qid: str, text: str, options: list[str], poll: float = 10.0, sleep: Callable[[float], None] = time.sleep) -> str:
    """Ask the person through reframe answer and wait for the reply (interactive mode)."""
    pending = folder.load("claude-question.json")  # its own files: pipeline 1 may be asking at the same time
    if not pending or pending.get("id") != qid:
        folder.save("claude-question.json", {"id": qid, "kind": "claude", "text": text, "options": options, "asked_at": iso()})
        folder.event("question", id=qid, kind="claude")
    while True:
        reply = folder.load("claude-answer.json")
        if reply and reply.get("id") == qid:
            folder.remove("claude-answer.json")
            folder.remove("claude-question.json")
            folder.event("answer_used", id=qid)
            return reply["text"]
        sleep(poll)
