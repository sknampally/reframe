# Release-candidate baseline

Observed on `feature/review-site-flow` at HEAD `3cd77f2`. The working tree was already dirty before this validation and was preserved.

## Git inventory before this validation

Tracked changes already present:

- `docs/cli-guide.md`
- `scripts/kiro_workflow.py`
- `scripts/reframe.py`
- `scripts/reframe_flow.py`
- `scripts/reframe_optional.py`
- `tests/test_guided_flow.py`
- `tests/test_reframe_optional.py`
- `tests/test_terminal.py`

Untracked plans already present:

- `docs/plans/reframe-end-to-end-remediation-plan.md`
- `docs/plans/reframe-release-candidate-validation-plan.md`
- `docs/plans/reframe-remediation-progress.md`

`docs/plans/reframe-workflow-enhancements.md` is already in HEAD `3cd77f2`.

## Commands

Interpreter: project virtualenv, Python 3.12.16.

```text
PYTHONPATH=tests python -m unittest discover -s tests
```

First run this phase: 321 tests, 321 passed, about 8.8 seconds. This matches the previously reported 321/321 baseline.

```text
reframe --help
reframe context --help
```

Both exited 0. Help lists `context`, `design-export`, `package-export`, and `register-project`. No provider login and no AWS call was made.

## After the packaging fix and CLI journey test

`reframe_optional` was missing from the setuptools module list, so a non-editable install would not ship the module the new commands import. It is now listed in `pyproject.toml`. `tests/test_release_cli.py` calls `reframe.main`.

Second full run: **322 tests, 322 passed**, about 8.5 seconds.

No ruff or mypy configuration is present.
