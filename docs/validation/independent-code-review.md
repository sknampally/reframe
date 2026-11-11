# Independent code review

Reviewed the optional-mode and remediation diff on `feature/review-site-flow`, plus the CLI journeys in this phase. Live AWS and paid models were not called.

## Findings

| ID | Severity | Location | Finding | Status |
|---|---|---|---|---|
| RC-1 | P2 | `pyproject.toml` module list | `reframe_optional` was not packaged, so `design-export` would fail in a wheel install. | Fixed. The module is listed, and `tests/test_release_cli.py` checks the declaration. |
| RC-2 | P2 | `scripts/reframe_optional.py` `resource_plan` | The dated-prefix planner is a library function. The CLI path is `register-project`, which writes the registry and still creates no job. | Open. Not a behavior bug. SSO, permission, and connector failures are not decided here. |
| RC-3 | P1 | `design_export` | Accepted human context is merged into the export input. It is not sent to the wireframe model. A page is a new render of the saved `screens.json`, not a new model generation. | Open by design. Draft labels stay in place. No fabricated model output. |
| RC-4 | P1 | Mode zip | Requirements, security, and acceptance files are the shared handoff templates plus a `GAP-1` item when the saved specification is absent. They are not a complete implementation contract. | Open by design. `package_status` is `DRAFT` and tests stay NOT RUN. |

No P0 was found.

## Checks

- `register_project` and `resource_plan` set `aws_jobs_created` to 0. A new commit without `--choice` is refused. A different repository is refused. Canonical Git identity compares host, owner, and repository, so a fork does not match.
- `design_export` reads `previews/business-1` and writes `previews/<version>/`. Names that match `business-1`, `flow-review`, or an existing skip draft are refused before a write.
- Context import refuses a symlink and a duplicate before a second extract. Accepting a contradictory note leaves the conflict open until `context resolve`.
- `package_export` refuses a workspace path or a secret pattern before writing the zip. A context change blocks a new zip until that version is exported again. The legacy package path is not replaced.
- `review_cycle` returns PASS, FAIL, or NEEDS_HUMAN_DECISION and stops on the third failed attempt.
- A status poll with no plan chat and no recorded connector or upload returns to waiting. It does not send a setup message. An empty chat that already has a connector and upload still sends once.
- A workspace lock with no running assessment step says another command is using the workspace. A running step still names the watcher.
