# Reframe — Release Candidate Validation and Live Acceptance Readiness

**Execution target:** `feature/review-site-flow`  
**Status:** Execution instructions, not evidence of completion  
**Scope:** Independent code audit, local regression and offline end-to-end validation, artifact usability checks, safe AWS readiness plan.  
**Authority:** This plan supersedes earlier *next-step prompts* for this phase; it does not replace existing product requirements or approved security controls.

## 1. Objective

Establish whether Reframe's new optional human context, Functional Parity/Reimagine flows, guided CLI integration, AI-DLC draft packages and project-resource planning work as intended **without breaking existing behavior**. Produce verifiable evidence and a narrowly scoped live AWS acceptance proposal. Do not equate passing unit tests with live AWS correctness or implementation-ready handoffs.

### Non-negotiable boundaries

- Work on `feature/review-site-flow`. Preserve the current working tree and all pre-existing changes. Do not discard, rebase, reset, delete or overwrite user work.
- **No live AWS calls or mutations** in this phase, including reads using real credentials, jobs, connector creation/approval, source upload, bucket access, IAM changes, S3 namespace moves or cleanup. Use stubs/fixtures only.
- **No paid model calls**, external-provider generation, installation of unauthorized services, deployment or application code generation.
- **No commit, push, PR, merge or Git identity changes** without a separate explicit approval. Inspect `git status`, log and diff read-only. Do not introduce agent authorship/co-authorship or impersonate the user.
- No confidential client/IntraEdge information, cloud account IDs, S3 names, credentials, session files or raw AWS Transform outputs in publicly publishable files. Treat local and fixture data according to its sensitivity; sanitize reports.
- Preserve legacy CLI commands, existing preview/package paths, `project.json`, existing approval gates and AWS state layouts. Stop before changes requiring breaking migrations.
- Human context remains **optional**. With no context, reverse engineering and draft exports must still work. Never imply proposed Reimagine content is verified business behavior.
- Use independent executor and reviewer passes. Review must inspect actual diff, behavior, artifacts and test evidence; no self-reported PASS without supporting checks.
- Safe local bug fixes are permitted if required for quality gates, with bounded retries. Avoid unrelated refactoring and new product features.

## 2. Baseline and evidence capture

1. Record branch, HEAD, `git status --short`, full tracked diff and untracked file inventory without changing them.
2. Identify the scope of uncommitted work, including previously changed review-site files and the remediation files. Cross-reference `docs/plans/reframe-end-to-end-remediation-plan.md`, `docs/plans/reframe-workflow-enhancements.md` if present, and `docs/plans/reframe-remediation-progress.md`.
3. Run with the project's documented Python 3.12 virtual environment:
   `PYTHONPATH=tests python -m unittest discover -s tests`.
4. Independently confirm the reported **321/321** baseline; if counts differ, report exact reasons and distinguish environment failures from regressions.
5. Verify `reframe --help` and CLI entry-point help work; record commands and any prerequisites. Never claim live provider verification.
6. Save sanitized validation reports in `docs/validation/` (or existing private evidence directory if reports contain sensitive paths). Do not copy raw source or private assessment artifacts into the public repo.

**Output:** `docs/validation/release-candidate-baseline.md` with observed results, not predicted status.

## 3. Independent code and architecture review

Inspect the implementations and diffs for `scripts/reframe.py`, `reframe_optional.py`, `reframe_flow.py`, `reframe_workspace.py`, `reframe_source.py`, `kiro_workflow.py`, `reframe_artifacts.py`, preview and packaging modules, tests, and CLI docs. Examine code paths, not just test names.

Review at minimum:

- **Project identity:** Canonical local/Git repository identity, upstream/fork ambiguity, pinned commit, source digest, collision avoidance, same repo/new commit behavior, explicit resume vs new run vs reuse baseline.
- **Resource planning:** Existing project should *plan* reuse of workspace/bucket/connector/role and a new job/run; new project should *plan* isolated resources; planning must never claim `aws_jobs_created > 0` or cause side effects. Verify the dated prefix is UTC, unique, stable on resume and cannot overwrite another run. Flag any unimplemented real AWS verification.
- **State and compatibility:** No unexpected writes to persisted `project.json`, approvals, saved stages, existing preview or package paths. Verify atomicity and recovery, locks and concurrent invocations, dry-run behavior and idempotence.
- **Human context:** Optional/empty handling, supported file formats, sandboxed path resolution, symlink/escape prevention, duplicate imports, attribution, provenance and checksums, acceptance vs conflict resolution, contradictions remaining open until explicit resolution, invalidation only of dependent outputs, rejection/supersession behavior and private-data controls.
- **Modes:** Functional Parity baseline fidelity; Reimagine future-state changes labeled `proposed_unapproved`; correct gating for approval/handoff; current/future separation; no silent invented business facts; behavior with and without human context.
- **Wireframes:** Screens, journeys, navigation links, validations, error states and input/output descriptions are generated from traceable assessment evidence; confirm whether context is actually used in model inputs or only added during export. Do not claim regeneration if no model call occurs.
- **AI-DLC export:** Explicit DRAFT status; manifest, evidence/source revision, approvals, scope/exclusions, screen specifications, API/data contracts, backlog/prompts/test guidance where actually present; safe internal ZIP paths, no unexpected files, missing-artifact handling, deterministic package manifest/checksums and independent unpackability. Differentiate draft package from implementation-ready package.
- **Review loop:** Executor/reviewer separation, structured PASS/FAIL/NEEDS_HUMAN_DECISION, bounded retries, stable criteria, inability to bypass gates by changing tests or downgrading findings.
- **AWS Transform boundary:** Unsupported connector properties or enums must fail closed, no guessed API values, clear distinction between mocked and real AWS capabilities.

Produce a findings matrix with file/line evidence, severity (`P0` correctness/security, `P1` handoff/completeness, `P2` cleanup), reproduction, suggested fix and status. Do not introduce changes unless test-backed and safe within the stated boundaries.

**Output:** `docs/validation/independent-code-review.md`.

## 4. Offline end-to-end acceptance

Use synthetic/publicly usable fixtures only. Run the CLI orchestration, not merely helper functions. Create temporary isolated project/workspace directories; ensure no real credentials are used.

### Journey A — Source-only Functional Parity

1. Register/pin a fixture repo; confirm there is no human context.
2. Simulate assessment-complete input and follow the existing guided/review-or-skip process as permitted by its state machine.
3. Choose Functional Parity and export a versioned current-state preview.
4. Confirm legacy `previews/business-1`, existing skip draft and original `package` output are preserved.
5. Export the mode-specific draft AI-DLC ZIP. Unpack into a clean directory and verify every manifest link and referenced file, screen navigation, API schema and source provenance.
6. Confirm no human context is required, `DRAFT` is visible, and tests/functional equivalence are **not** marked passed.

### Journey B — Optional context + Reimagine

1. Import sanitized current-state clarification and future-state proposal with names/roles/versions and attribution.
2. Demonstrate duplicate and symlink import rejection; show conflict remains open after accepting a contradictory note until explicit resolution.
3. Generate current-state and future-state wireframes with distinct navigation and source-to-proposal change map. Show proposal/approval labels on every material future-state difference.
4. Confirm only approved context can become an accepted requirement; rejected/superseded sources cannot silently enter final exports.
5. Export the draft Reimagine ZIP and unpack in a clean temporary directory; check it can be understood without the original Reframe working directory.
6. Modify context and verify targeted invalidation/regeneration, preserved historical outputs, and no accidental AWS Transform rerun.

### Journey C — Lifecycle and failure recovery (mocked)

- Same repository/same revision: planned infrastructure reuse, explicit baseline reuse or new run choice.
- Same repository/new revision: reuses *project* resources but plans new isolated dated run and job.
- New repository: distinct resource plan.
- Interrupted run: resume without duplicate job or overwritten objects.
- Ambiguous identity, missing connector, SSO expiry, invalid permissions: stop/report; do not fabricate success.
- Concurrent attempts and inconsistent state: safe refusal or atomic recovery.

For all journeys, capture exact commands, fixture identifiers, outputs, exit codes and assertions, with no account-specific details. If CLI cannot genuinely perform a journey, report **PARTIAL / NOT_IMPLEMENTED**, not PASS, and either implement a safe local fix or give an explicit acceptance gap.

**Output:** `docs/validation/offline-acceptance.md` plus corresponding fixture tests.

## 5. Independent AI-DLC and UI deliverable quality gate

Do not assess deliverables solely by successful ZIP creation or HTML presence. Verify in the actual exported files:

1. **Current-state screens:** Business purpose, fields, types/validation, navigation, error paths and source/assessment references, or explicit unknowns.
2. **Future-state screens:** Real revised journeys/screens rather than a generic change table; each change records rationale, approval state and impact on business rules/data/APIs.
3. **Packaging:** Every claimed file exists and references resolve after unpack; stage and mode names are consistent. Preserve draft/approval boundaries and privacy exclusions.
4. **AI-DLC readiness:** Evaluate an explicit checklist for requirements, acceptance criteria, implementation backlog, agent prompts, contracts, security, tests, traceability and open decisions. If a category is missing, mark it missing and keep package labelled draft; do not fabricate content merely to pass tests.
5. **Reviewer independence:** Reviewer samples source-to-rule-to-screen-to-test chains and checks implementation output, not executor narrative.

**Output:** `docs/validation/deliverables-quality-gate.md` with PASS/PARTIAL/FAIL by artifact and exact evidence.

## 6. Defect correction and quality gates

- Fix independently substantiated P0/P1 defects that are safe, local and backward compatible. Preserve existing working behavior.
- For each fix: add a failing regression test first where practical; apply minimal correction; run focused tests then full suite; require independent reviewer PASS.
- Maximum **three** repair/review cycles per defect; on continued failure, stop with reproduction, evidence, impact and options. Do not weaken assertions to force a PASS.
- Confirm zero new regressions. Keep the tests and offline journeys reproducible in CI without AWS credentials or paid models.
- Avoid sweeping refactors, changing AWS resource policies, changing persisted contract formats or renaming paths in this phase.

## 7. Controlled live AWS acceptance proposal — planning ONLY

Prepare a separate, ready-to-approve test plan for later use with a **designated sandbox account and an explicit user-provided budget**. No execution yet.

Include:

- Preconditions: AWS Transform supported region/account profile, permissions, existing source/project registration, connector and bucket availability, SSO freshness, permitted source data and ownership.
- Exact API/CLI operations in order and read vs write classification. Avoid unsupported or guessed connector types/fields; derive from actual service task schemas.
- Existing-project scenario: validate resource identities and scope; create one *new job* plus date/run-isolated source and assessment objects, without changing previous objects.
- New-project scenario: approved isolated workspace/bucket/connector/role creation with least privilege, consent before upload and spend.
- Checkpoints, maximum runtime/cost threshold, stop/rollback/cleanup policy, logs and evidence collection, and expected human-input tasks.
- Success conditions: actual AWS Transform job/task transitions, exact object provenance, evidence coverage and reproducibility. Clearly separate verified behavior from API/model output assumptions.
- No deployment and no blanket approval to create or modify cloud resources.

**Output:** `docs/plans/reframe-live-aws-acceptance.md` labelled **NOT AUTHORIZED / NOT EXECUTED**.

## 8. Final readiness and handoff

Produce a concise release-candidate report with:

- Branch/HEAD, dirty working tree status and files changed during this phase.
- Unit suite before/after counts, exact command, offline journey outcomes, representative exported artifact paths.
- Independent reviewer PASS/FAIL/NEEDS_HUMAN_DECISION with any remaining issues.
- Capability matrix: `mocked-verified`, `locally end-to-end verified`, `live-unverified`, `not implemented`.
- Backward compatibility checks: `project.json`, `previews/business-1`, skip drafts, single legacy package and existing approvals.
- P0/P1/P2 risk list and explicit go/no-go for a **checkpoint PR review** versus a **live AWS test**.
- If all local gates pass: provide a proposed commit/PR title and description, but **do not commit/push/open PR**.
- Single next approval request covering only the necessary live AWS actions (if needed); avoid piecemeal follow-up questions already resolved here.

### Definition of done for this plan

A pass requires: reproducible green unit suite; independent code review of actual changes; offline Journeys A/B/C either fully PASS or honestly documented as NOT_IMPLEMENTED with blocking severity; directly inspected wireframes/ZIPs; no regression in legacy behavior; clear draft labels; and a specific, nonexecuted live AWS validation plan. No claim of production readiness or end-to-end modernization equivalence is permitted without live and business validation.

**Execute all safe steps autonomously. Stop only for material blockers, failed gates after bounded retries, required breaking changes, paid/external execution, cloud resource operations, or git publish actions.**
