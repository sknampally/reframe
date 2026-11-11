# Offline acceptance

Commands used the installed `reframe` binary against two temporary workspaces. The fixture source is `https://github.com/example/fixture-mainframe.git` at a synthetic 40-character commit. No AWS credentials were read and no model was called. `tests/test_release_cli.py` covers the source-only parity export, the skip-name refusal, and a new-revision registry choice. The other steps below were run with the CLI and are covered by `tests/test_reframe_optional.py`.

## Journey A — source-only functional parity

| Step | Command | Exit | Result |
|---|---|---|---|
| No context | `reframe context status` | 0 | `not_provided` |
| Register | `reframe register-project` | 0 | `REGISTERED`, no job |
| Current-state | `reframe design-export --version v1 --mode functional-parity` | 0 | `previews/v1/current-state` |
| Draft zip | `reframe package-export --version v1 --mode functional-parity` | 0 | `DRAFT_READY`, `final_handoff` `NOT_REQUESTED` |
| Skip name | `reframe design-export --version draft` | 2 | Refused. Skip file unchanged |
| Reserved name | `reframe design-export --version Business-1` | 2 | Refused. `previews/business-1/index.html` stayed `legacy-review` |

`reframe assess` was not run. The fixture stands in for an already recorded review site. Guided `/flow` remains interactive and was not driven with a terminal in this pass. Its packaged-project question is covered by `tests/test_guided_flow.py`, and the default answer is skip.

Unpacked parity zip: 30 members, no absolute or `..` paths. Every manifest checksum matched the unpacked file. Manifest: `package_status` `DRAFT`, `human_context` `not_provided`, `implementation_status` `NOT_STARTED`, source commit recorded. OpenAPI contains `/payments/validate`. README says NOT RUN. The legacy zip bytes stayed `legacy-zip`. `project.json` stayed `PACKAGED`.

Verdict: **PASS** for the non-interactive export. Assessment creation and `/flow` itself were not executed.

## Journey B — context and reimagine

| Step | Exit | Observed |
|---|---|---|
| Import a symlink | 2 | Quarantined. No extract written |
| Import the clarification | 0 | `IMPORTED` |
| Import the same file again | 2 | Already imported. One extract remains |
| Accept the clarification | 0 | Attributed. Not treated as source proof |
| Accept a note that says `Contradicts: R1` | 0 | Conflict stays open |
| Package the old v1 export | 2 | Re-export required. The first zip was kept |
| `context resolve --resolution keep-evidence` | 0 | `resolved` |
| Accept a future-state note for journey `pay` | 0 | |
| `design-export --version v2 --mode reimagine` | 0 | Current and future pages |
| `package-export --version v2 --mode reimagine` | 0 | `DRAFT_READY` |
| Import another clarification, then package v2 | 2 | Re-export required. No AWS command ran |

v2 current-state HTML contains the clarification, the words "not verified source behavior", the field "whole dollars", the state "Amount rejected", and `href="#screen-s1"`. Future-state HTML contains "Amount future", the accepted change "Combine amount", and the same screen link. A separate no-context reimagine export contains `proposed_unapproved` and leaves the legacy review file unchanged.

Unpacked reimagine zip: 33 members, checksums matched, `human_context` `provided`, `package_status` `DRAFT`.

Verdict: **PASS**.

## Journey C — lifecycle

| Step | Exit | Observed |
|---|---|---|
| `register-project` on the same commit | 0 | `REUSE`, `aws_jobs_created` 0 |
| Same repository, new commit, no choice | 2 | Asks for `assess-new-revision` or `reuse-baseline` |
| `--choice reuse-baseline` | 0 | Original commit kept. No job |
| `--choice assess-new-revision` | 0 | Prefix `projects/p0a814216/runs/20261111T155309Z-bbbbbb/`, `aws_job` null, `uploaded` false |
| Different repository | 2 | Identity refused |
| `reframe resume` with no active assessment | 2 | "No active assessment to resume". No job created |

SSO expiry, missing connector, and permission failures are not decided by `register-project`. Those stops belong to the existing assessment commands and were not executed. `resource_plan` remains a library function with the same zero-job result in unit tests.

Verdict: **PASS** for registry choices and refusal. **NOT_IMPLEMENTED** for live permission, SSO, and connector checks in this command.
