# Remediation progress

Local work on `feature/review-site-flow`. No commit, push, pull request, or live AWS call.

## Environment

- Command: `PYTHONPATH=tests python -m unittest discover -s tests`
- Interpreter: the project virtualenv, Python 3.12
- CLI smoke: `reframe --help` lists `context`, `design-export`, `package-export`, and `register-project`
- No ruff or mypy configuration is present in `pyproject.toml`

## Baseline and gate A

Earlier suite on this branch: 320 tests, 313 passed, 7 failed.

Root causes:

- A job-status poll with no plan chat and no recorded connector or upload was treated as plan onboarding, then blocked. Onboarding still sends one objective when the connector and upload are already recorded.
- A workspace lock with no in-progress assessment step now says another command is using the workspace. A running step still names the watcher.
- The narrow-console test passed an explicit width without a height. Under `TERM=dumb`, Rich ignores width unless height is set. The test now sets both. A normal terminal already wrapped to the requested width.

Final suite after the later increments: 321 tests, 321 passed, 0 failed. Runtime about 8.5 seconds.

## Later increments

- Context import checks the file before writing, refuses symbolic links, and keeps a duplicate from creating a second extract. Accepting a statement leaves a contradiction open until `context resolve`.
- Accepted current-state text is merged into the draft input before HTML rendering. Reimagine future-state pages are rendered screens, and unapproved changes stay `proposed_unapproved`.
- Mode packages reuse the handoff writer and include manifest checksums, backlog, OpenAPI, instructions, and wireframes. The legacy `package` command is unchanged. Status stays `DRAFT`.
- Guided flow asks for an optional mode only after the approved package exists. The default answer is skip. Skip drafts are not rewritten.
- `resource_plan` returns resume, reuse, new-assessment, and new-project plans with `aws_jobs_created: 0`. Live AWS remains unverified.
- `review_cycle` returns PASS, FAIL, or NEEDS_HUMAN_DECISION and stops on the third failed attempt.

## Review

Independent review returned PASS. No findings. Live AWS and paid model calls were not run.
