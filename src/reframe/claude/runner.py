"""Claude Code, run headless for one step (pipeline 2, stages C2 to C6).

What spike P2-01 showed, and why this module looks as it does:

- A workspace inside a Git repository that Claude Code has not been told to trust has its project allow rules ignored,
  so the policy goes in with --settings: a file outside the workspace (the plugin cannot change it) with absolute
  path rules and the shell sandbox on.
- The environment is built from nothing (no variables from the shell that started Reframe), with the plugin's usage
  counts off. Signing in is Claude Code's own: the person's subscription, or Amazon Bedrock.
- Each step writes a stream-json log; the last "result" line gives the outcome, the turns, the tokens and the cost.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PATH_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
PASSED = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
          "ANTHROPIC_DEFAULT_HAIKU_MODEL")
LIMIT_WORDS = ("usage limit", "hit your limit", "hit your 5-hour limit", "hit your weekly limit", "limit reached", "rate limit", "resets ")
SIGNIN_WORDS = ("please run /login", "not logged in", "invalid api key", "authentication_error", "oauth token has expired",
                "credit balance is too low", "could not resolve authentication", "unable to locate credentials",
                "security token included in the request is invalid", "expiredtoken")


@dataclass
class StepResult:
    name: str
    ok: bool
    text: str = ""
    session_id: str | None = None
    seconds: float = 0.0
    turns: int = 0
    tokens: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    models: list[str] = field(default_factory=list)
    denials: list[dict[str, str]] = field(default_factory=list)
    problem: str | None = None  # "limit", "signin" or "failed"

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "session_id": self.session_id, "seconds": round(self.seconds),
                "turns": self.turns, "tokens": self.tokens, "cost_usd": round(self.cost_usd, 2), "models": self.models,
                "denials": self.denials, "problem": self.problem, "text": self.text[-4000:]}


def environment(auth: str, aws_credentials: Callable[[], dict[str, str]] | None = None, region: str | None = None,
                parent: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if parent is None else parent
    home = source.get("HOME", str(Path.home()))
    env = {"HOME": home, "USER": source.get("USER", ""), "LANG": source.get("LANG", "en_US.UTF-8"), "TERM": "xterm",
           "PATH": os.pathsep.join(home + p[1:] if p.startswith("~") else p for p in PATH_DIRS),
           "CODE_MODERNIZATION_TELEMETRY": "0", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}
    env.update({name: source[name] for name in PASSED if source.get(name)})
    if auth == "api-key":  # the person's Anthropic API key, from the shell that runs Reframe; passed on, never written
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            if source.get(name):
                env[name] = source[name]
    if auth == "bedrock":
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        env["AWS_REGION"] = region or source.get("AWS_REGION", "us-east-1")
        if source.get("AWS_BEARER_TOKEN_BEDROCK"):  # a Bedrock API key the person set; passed on, never written
            env["AWS_BEARER_TOKEN_BEDROCK"] = source["AWS_BEARER_TOKEN_BEDROCK"]
        elif aws_credentials is not None:
            env.update(aws_credentials())
    return env


def summarize(name: str, lines: list[dict[str, Any]], seconds: float, returncode: int) -> StepResult:
    session = next((line.get("session_id") for line in lines if line.get("session_id")), None)
    results = [line for line in lines if line.get("type") == "result"]
    if not results:
        tail = " ".join(str(line)[:200] for line in lines[-3:])
        problem: str | None = "signin" if any(w in tail.casefold() for w in SIGNIN_WORDS) else "limit" if any(w in tail.casefold() for w in LIMIT_WORDS) else "failed"
        return StepResult(name, False, text=f"no result (exit {returncode}) {tail}", session_id=session, seconds=seconds, problem=problem)
    # Claude Code can end with an empty "success" result for a background task's notice, after the step's real result
    # (found on the second test codebase, 26 November 2026: the real one was the usage limit). That notice is not the step's answer.
    real = [r for r in results if not ((r.get("origin") or {}).get("kind") == "task-notification" and not str(r.get("result") or "").strip())]
    last = (real or results)[-1]
    limited = next((r for r in results if r.get("api_error") == "usage_limit_reached"
                    or (r.get("is_error") and any(w in str(r.get("result") or "").casefold() for w in LIMIT_WORDS))), None)
    if limited is not None:
        last = {**last, "is_error": True, "result": limited.get("result") or "usage limit reached"}
    usage = last.get("usage") or {}
    tokens = {key: int(usage.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    text = str(last.get("result") or "")
    lowered = text.casefold()
    failed = bool(last.get("is_error")) or last.get("subtype") != "success"
    problem = None
    if failed:
        problem = "signin" if any(w in lowered for w in SIGNIN_WORDS) else "limit" if limited is not None or any(w in lowered for w in LIMIT_WORDS) else "failed"
    denials = [{"tool": d.get("tool_name", ""), "input": json.dumps(d.get("tool_input"))[:200]} for d in last.get("permission_denials") or []]
    return StepResult(name, not failed, text=text, session_id=last.get("session_id") or session, seconds=seconds,
                      turns=int(last.get("num_turns") or 0), tokens=tokens, cost_usd=float(last.get("total_cost_usd") or 0.0),
                      models=sorted((last.get("modelUsage") or {}).keys()), denials=denials, problem=problem)


class ClaudeCode:
    def __init__(self, workspace: Path, policy: Path, plugin: Path, logs: Path, env: dict[str, str], model: str | None = None,
                 popen: Callable[..., Any] = subprocess.Popen) -> None:
        self.workspace, self.policy, self.plugin, self.logs, self.env, self.model, self.popen = workspace, policy, plugin, logs, env, model, popen

    def argv(self, prompt: str, resume: str | None = None, schema: dict | None = None) -> list[str]:
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--settings", str(self.policy),
                "--plugin-dir", str(self.plugin)]
        if resume:
            argv += ["--resume", resume]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def run(self, name: str, prompt: str, *, timeout: float = 4 * 3600, resume: str | None = None, schema: dict | None = None) -> StepResult:
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        log, errors = self.logs / f"{name}.jsonl", self.logs / f"{name}.err"
        started = time.time()
        with log.open("w") as out, errors.open("w") as err:
            process = self.popen(self.argv(prompt, resume, schema), cwd=str(self.workspace), env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=err)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = -9
        lines = []
        for raw in log.read_text(errors="replace").splitlines():
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue
        result = summarize(name, lines, time.time() - started, returncode)
        if not result.ok and result.problem == "failed":
            tail = errors.read_text(errors="replace")[-500:].casefold()
            if any(w in tail for w in SIGNIN_WORDS):
                result.problem = "signin"
        return result

    def structured(self, result: StepResult) -> Any:
        """The structured output of a --json-schema step (the last result line's structured_output)."""
        for raw in reversed((self.logs / f"{result.name}.jsonl").read_text(errors="replace").splitlines()):
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if line.get("type") == "result":
                return line.get("structured_output")
        return None
