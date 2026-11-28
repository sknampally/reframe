"""Calls into api_helper.py, which runs in the pinned MCP server's interpreter (see its docstring).

A reply AWS refused (HTTP 4xx, or a message naming the refusal) is Rejected; anything else before a change
was sent is NotSent; a failure of a send or delete whose outcome is unknown is Uncertain.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from importlib import resources
from typing import Any

from reframe.core.errors import NotSent, Rejected, Uncertain
from reframe.core.preflight import MCP_SERVER
from reframe.core.shell import Runner, SigninExpired, base_environment, run_subprocess, signin_expired
from reframe.transform.client import ServerConfig

REFUSED = re.compile(r"HTTP 4\d\d|not authorized|AccessDenied|ValidationException|ResourceNotFound|ConflictException", re.I)


class TransformApi:
    def __init__(self, config: ServerConfig, runner: Runner = run_subprocess) -> None:
        self.config, self.runner = config, runner

    def _call(self, request: dict[str, Any], changes: bool) -> Any:
        helper = resources.files("reframe.transform") / "api_helper.py"
        with resources.as_file(helper) as path:
            argv = ["uvx", "--offline", "--from", MCP_SERVER, "python", "-I", str(path)]
            env = {**base_environment(), "AWS_PROFILE": self.config.profile, "AWS_REGION": self.config.region,
                   "AWS_TRANSFORM_MCP_WRITE_DIR": str(self.config.write_dir)}
            self.config.write_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                result = self.runner(argv, env, 300, json.dumps(request).encode())
            except OSError as error:
                raise NotSent(f"the AWS Transform helper could not start ({error})") from None
            except subprocess.TimeoutExpired:
                if changes:
                    raise Uncertain(f"{request['op']} did not finish in time") from None
                raise NotSent(f"{request['op']} did not finish in time") from None
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if not lines:
            if changes:
                raise Uncertain(f"{request['op']}: the helper gave no reply ({result.stderr.strip()[-300:]})")
            raise NotSent(f"{request['op']}: the helper gave no reply ({result.stderr.strip()[-300:]})")
        reply = json.loads(lines[-1])
        if reply.get("ok"):
            return reply.get("value")
        message = str(reply.get("error") or "")
        if signin_expired(message):
            raise SigninExpired(message[:300])
        if REFUSED.search(message) or (isinstance(reply.get("status"), int) and 400 <= reply["status"] < 500):
            raise Rejected(request["op"], message[:500])
        if changes:
            raise Uncertain(f"{request['op']}: {message[:300]}")
        raise NotSent(f"{request['op']}: {message[:300]}")

    def messages(self, workspace_id: str, job_id: str) -> list[dict[str, Any]]:
        return self._call({"op": "messages", "workspace_id": workspace_id, "job_id": job_id}, changes=False) or []

    def send(self, workspace_id: str, job_id: str, text: str, interaction_origin_id: str | None = None,
             idempotency_token: str | None = None) -> dict[str, Any]:
        return self._call({"op": "send", "workspace_id": workspace_id, "job_id": job_id, "text": text,
                           "interaction_origin_id": interaction_origin_id,
                           "idempotency_token": idempotency_token or str(uuid.uuid4())}, changes=True) or {}

    def list_connectors(self) -> list[dict[str, Any]]:
        return self._call({"op": "list_connectors"}, changes=False) or []

    def delete_connector(self, connector_id: str) -> Any:
        return self._call({"op": "delete_connector", "connector_id": connector_id}, changes=True)
