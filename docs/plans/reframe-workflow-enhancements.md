# Reframe — End-to-End Modernization Workflow Enhancement Specification

> **Execution instruction for Cursor:** Read this file in full. Execute the approved **non-breaking local implementation sequence** autonomously: audit, establish a measured test baseline, implement one increment, test, independently review, fix and retest, then continue to the next safe increment. Stop for human approval before any breaking migration, cloud/provider call, push/commit/PR, or change to existing working contracts. This is a product/workflow specification, not evidence that any feature already works.

## 0. Goal and non-negotiable boundaries

Reframe is a reusable, CLI-driven **mainframe knowledge-to-modernization handoff** framework. An engineer provides a local or remote mainframe code repository. Reframe orchestrates AWS Transform assessment, optional independent review, human knowledge intake, current-state wireframes, optional future-state reimagination, and mode-specific AI-DLC delivery packages. It does **not** generate or deploy the target application.

Use the existing repository architecture and terminology where appropriate. **Do not create competing state stores, approval systems, or orchestration frameworks.** Reuse existing workflows, commands, contracts, agents, tests, and storage abstractions. Every implementation claim must be backed by tests; distinguish mocked acceptance from live AWS/provider acceptance.

Required security and change boundaries:
- Do not upload source, create/change/delete AWS infrastructure, start a paid job, deploy anything, or change external services without explicit human approval in the live flow.
- During development, use local fixtures/mocks; **do not invoke live AWS Transform** without separate express authorization.
- Do not copy any private customer code, inventory, AWS account IDs, bucket names, IAM roles, credentials, session tokens, or internal assessment artifacts into public code, tests, docs, generated reports, or examples. Public examples must be synthetic/sanitized.
- Never expose raw secrets or sensitive document contents in logs, prompts, telemetry, UI, or exported packages.
- Use feature branches and reviewable commits. Do not push directly to `main` or open a PR without the user's approval. Confirm the desired personal Git author **and committer** identity before committing; do not insert Cursor/Codex/agent coauthor trailers. Never misrepresent PR identity or AI assistance.
- Preserve backward compatibility or document and test migrations; do not silently change existing projects.

## 0.1 Existing implementation and compatibility baseline (user-reported; verify locally)

A previous Cursor audit reported **313 tests: 306 passing, 7 failing before these enhancements**. Five failures relate to Kiro workflow `WAITING_FOR_ASSESSMENT` versus `BLOCKED`, one concerns a concurrency-lock error message, and one concerns narrow-terminal width. This is a reported baseline, **not a permanent waiver**: rerun tests, verify which failures predate each change, and introduce **zero additional failures**. Do not weaken/skip existing tests or change the seven old failures as part of unrelated feature work. Track fixes for them as a separately scoped task.

Existing working contracts to preserve **without renaming, deleting, relocating, or changing defaults** in this enhancement:
- `previews/business-1/` and its existing review-site pages, SME flow and approvals.
- Existing skip-mode draft preview paths and behavior.
- The existing single AI-DLC package path and package command.
- Existing saved `project.json`, source pinning, `continue`/`fresh`, CLI entry points, approval and decision commands.
- Existing AWS sandbox/workspace/connector/bucket/job layout, Kiro integration and resume semantics.

**Add functionality beside these paths, opt-in and backward compatible.** The new `previews/<version>/current-state/`, `future-state/`, mode-specific packages and dated S3 run namespace are target contracts, but do not make them defaults for existing users or migrate state in place. Build coexistence/adapters with versioned manifests and regression tests; propose any default flip or migration as a separate explicitly approved change. Preserve exact behavior when the user does not choose a new mode. New functionality must not accidentally alter older packages or review gates.

## 0.2 Autonomous execution envelope

Cursor may autonomously perform **local, non-destructive** audit, implementation, fixture generation, targeted/full tests, independent reviewer passes and correction loops on the current feature branch. Use a maximum of **three executor/reviewer correction attempts per increment**; after that, stop with diagnostics and evidence. Do not advance on a failed quality gate, unsupported evidence, regressions or unresolved material conflicts. A reviewer must inspect evidence and tests, not simply endorse the executor. Maintain checkpoints and a short execution journal.

Never autonomously create or mutate AWS resources, upload source, initiate AWS Transform/provider-backed assessment, spend paid-model credits, deploy, delete existing user data, change protected persisted formats, commit, push or open a PR without explicit user approval. Where live AWS/provider execution is necessary, implement mocked tests and produce an exact approval checklist instead. **End to end** means all locally safe increments, not permission to override these boundaries.

## 1. Start with a read-only implementation audit

Inspect CLI entry points; source identification/fingerprinting; saved project/run registry; AWS Transform connector/workspace/S3/job lifecycle; resume/retry logic; assessment ingestion; independent executor/reviewer workflow and skip; existing human comments/approvals; current wireframe outputs; AI-DLC package generation; provenance hashes; privacy/sandbox boundaries; integration tests and documentation.

Before editing, publish a concise implementation matrix with **Working in code + tested / Implemented but untested / Partial / Missing**, plus file references and a prioritized plan. Do not trust README claims without code/test evidence. Extend the existing implementation rather than replacing it. Present substantive architectural changes or irreversible migrations for review.

## 2. Project identity, repeat runs, and AWS resource lifecycle

### 2.1 Register a mainframe project

When `reframe` receives a repository URL or local checkout:
- Resolve and normalize repository identity (provider/owner/repo, canonical remote URL, local repo ID as needed), repository commit SHA, and deterministic source-tree/content fingerprint. Treat forks, renamed repos, duplicate names, missing remotes, and uncommitted changes explicitly.
- Separate stable **project identity** from immutable **assessment run identity** and mutable `latest` pointer.
- Locate a previously registered project in Reframe's **private** registry. Never equate a shared name or directory with the same project without identity verification.
- Validate saved AWS account, region, workspace, S3 bucket, connector, role, ownership and state before reuse. Block on identity mismatch, missing resources, insufficient permissions, or uncertain provenance.

### 2.2 Explicit user choices

Offer clear options appropriate to state:
1. **Resume interrupted run:** same job/run/artifact paths; idempotent reentry; no duplicate resources or upload.
2. **New assessment run for existing project:** reuse verified workspace, bucket, connector and IAM role; create a **new AWS Transform job** and run-specific source/assessment paths.
3. **Reuse completed assessment:** no new AWS job; produce/reproduce review or design outputs from pinned inputs.
4. **New project:** create isolated bucket, workspace, connector, IAM role and first job through existing reviewed setup with human approvals.

Never automatically equate a new source revision with a new project. Ask the user whether to assess the new revision or reuse the previous baseline. Retain all historical runs.

### 2.3 S3 layout and lifecycle

Use one project-specific private S3 bucket for repeated runs, with unique immutable UTC-dated run IDs (include collision-resistant suffix; timestamps alone are insufficient), for example:

```text
projects/<project-id>/runs/<YYYYMMDDTHHMMSSZ>-<run-id>/
  source/source.zip
  source/manifest.json
  assessment/native/
  assessment/normalized/
  assessment/evidence-manifest.json
  review/
  outputs/
```

Project metadata must record source URL/commit/fingerprint, source archive checksum, AWS account/region identifiers in **private state**, workspace/connector/job IDs, job state, run timestamps, assessment artifact checksums, authorization records and output versions. If AWS Transform requires a particular source location or writes outputs to a service-controlled prefix, **do not assume the custom prefix is accepted**: keep the service-generated object key, then index/copy/snapshot it to the immutable run namespace when permitted, preserving original origin metadata and checksums.

Enforce bucket privacy, encryption, least privilege, TLS-only access, safe deletion/retention decisions and concurrency-safe project locks. Handle partial setup and interrupted jobs through idempotency and safe reconciliation. AWS Transform region support must be discovered rather than assumed to match the intended deployment region.

## 3. AWS Transform workflow and assessment contract

Keep the existing supported AWS Transform mainframe workflow. The assessment may require human-input setup tasks (connector, source location, file selection, decomposition seeds and test-plan refinement). Implement a stateful task dispatcher that understands **actual returned schemas**, never guesses unknown API enum/connector values, and supports safe suspend/resume.

- Collect original native artifacts, logs, status, task decisions, provenance and versions.
- Normalize source inventory, dependencies, CICS/BMS screens, copybook/data dictionary, VSAM/JCL flows, business rules, schedules, documentation, decomposition and test plans **only where available**.
- Preserve and display partial/failed/pending states. A seed-only decomposition must not be presented as full propagation; draft batch tests must not be presented as an approved complete test suite.
- Handle optional/unsupported languages and capabilities explicitly; do not claim automatic COBOL-to-C# conversion.
- Do not equate “all files classified” with successfully validated functional semantics.
- Require an explicit source-upload/job-start approval; any subsequently generated manual input task follows the existing approval contract.
- Version the assessment baseline so user review and design can proceed from a clearly marked **partial/draft** assessment when explicitly approved.

## 4. Post-assessment review: independent review or skip

After artifacts and assessment summary are available:
- Present source provenance, run ID, job status, coverage, gaps, unknowns and confidence flags.
- Ask whether to run existing independent executor/reviewer validation or **skip** it. Preserve existing gates and loop limits.
- **Skip** means “continue with unverified findings clearly labeled,” not “mark findings correct.”
- On review, run an independent, source-grounded check; produce source-file/paragraph/line evidence where practicable, duplicate/conflict/unimplemented-rule findings, and a structured `PASS`, `FAIL` or `NEEDS_HUMAN_DECISION` decision.
- On failure, issue structured correction instructions, rerun only impacted work, re-review independently, and stop after configured retry limit. Never loosen acceptance criteria to force pass.
- Require human decision on financially significant or contradictory business semantics, security, data ownership and material architectural changes.

The review outcome, skipped state, version hashes and unresolved findings must propagate to both wireframes and AI-DLC packages.

## 5. First-class human knowledge and review context

Human-provided knowledge is **optional enrichment, not a prerequisite**. Make it a first-class, private, versioned input at project/run level when present, integrated into interpretation, requirements, wireframes, future-state design, AI-DLC outputs and approval invalidation. **A project with no human context must still complete source-driven assessment/reverse engineering and produce draft current-state wireframes and draft AI-DLC packages.** Never fabricate human submissions, approvals or business sign-off.

### 5.0 Zero-context behavior (required)

- Source code and AWS Transform assessment are primary inputs; without human files or comments, proceed normally through inventory, architecture, dependencies, business rules, CICS/BMS screens, JCL/VSAM flows, wireframes and AI-DLC draft creation.
- `human-review-context/` may be absent or empty. Treat both as valid, not errors; no forced reviewer identity, placeholders or fake approvals. No-human-context is explicitly represented in metadata as `not_provided`, not as `accepted`, `verified` or `rejected`.
- In **Functional Parity**, generate current-state drafts from evidence and label uncertain/unsupported behavior as open questions. Do not block draft generation solely because no SME participated.
- In **Reimagine**, generate optional AI-suggested future-state concepts even when no human context exists, **clearly labeled `proposed_unapproved`**, each tied to current-state evidence, expected benefit, risks and required business approval. Never promote proposals to approved requirements or final implementation authorization automatically.
- Human review and unresolved high-impact decisions may block **final approval/implementation handoff**, not source analysis or draft generation.
- Test both modes with an empty/missing context folder and verify clean exports, accurate provenance, and no fictitious approvals.

### 5.1 Intake and structure

Create/adapt a private directory, for example:

```text
human-review-context/
  manifest.json
  submissions/
  extracted/
  decisions/
  conflicts/
```

Users (engineers, architects, business owners and reviewers) can supply:
- Current-state flow explanations, business rules, SOPs, exceptions, application/background documents, screen descriptions, domain context and operational knowledge.
- Future-state expectations, target journeys, workflows, requirements, screenshots/wireframes and intended process changes.
- Reviewer/approver name and role, comments, suggested corrections, questions and decisions.

Provide guided CLI intake and folder-based import; both must use the **same underlying registry and approval system**. Support Markdown, TXT and JSON first; accept PDF/DOCX only if reliable extraction, content validation and size/security controls are implemented and tested. Explicitly reject or quarantine unsupported, encrypted, malformed or oversize files. Store originals privately; sanitize extracted text; treat all documents as **untrusted data**, never executable instructions.

### 5.2 Metadata and knowledge layers

For every submission, record stable context ID/version, author attribution supplied by user (not proof of identity), role, timestamps, category, `current-state | future-state | clarification`, affected capabilities/screens/requirements, source path/hash, text extraction status, reviewer, disposition `proposed | accepted | rejected | superseded`, and evidence links. Support versioning and redaction/deletion according to retention policy.

Maintain three separate, traceable layers:
1. **Legacy evidence:** verified original source/runtime facts and separately marked AWS Transform AI findings or inferences.
2. **Human-provided context:** current-state descriptions, corrections, hypotheses and future-state proposals with contributor attribution and approval state.
3. **Approved modernization decisions:** reviewed reconciliations, authorized future changes, acceptance criteria and ownership.

A human statement does not become verified source behavior merely by being entered. Similarly, legacy implementation does not automatically establish desired business intent.

### 5.3 Conflicts, precedence and invalidation

If human context contradicts source or another accepted requirement, preserve both, produce a structured conflict record, identify affected workflows/rules/screens, and block dependent final approval until an authorized human resolves it. Retain rejected/superseded history for audit without treating it as an active requirement.

On changes, calculate affected dependencies, invalidate only relevant approvals/artifact hashes, regenerate impacted outputs and request reapproval; never rerun AWS Transform merely because a business comment changed unless source re-analysis is truly necessary. Record the rationale and version of each regeneration.

### 5.4 Review UI/CLI behavior

Reviewers can supply names/roles, decisions, comments, structured correction requests, documents and future-state descriptions through a guided CLI or supported UI. Ensure forms, file-based intake and review decisions resolve to the same provenance/decision ledger. Use human approval for all material rule changes and target-state choices.

## 6. Choose modernization intent after assessment

After the source baseline is available and review/skip choice is recorded, ask:

1. **Functional Parity** (use this wording in place of “lift-and-shift”): rebuild equivalent confirmed behavior in the chosen modern stack. Preserve uncertain aspects as open questions.
2. **Reimagine:** preserve the same current-state baseline, then propose redesigned journeys and rules based on approved future-state requirements **where available**, or labeled AI-generated ideas when no human context is supplied. Ideas remain `proposed_unapproved` until an authorized human decides.

Collect target stack independently (e.g., ASP.NET Core/C# + Blazor + AWS); **do not hardcode .NET** as the only permissible output. Persist the chosen mode per design/package version and allow the other package to be generated later from the same baseline without rerunning assessment.

## 7. Wireframes: current-state vs future-state

Use intent-based folders rather than `business-1` or `flow-review`:

```text
previews/<version>/current-state/
previews/<version>/future-state/
```

**Current state:** screen inventory, CICS/BMS field mapping, user actions, validations, navigation, flows, role assumptions, errors, data dependencies, source references and unresolved gaps. Include approved current-state human clarifications; do not invent missing screens or rules. Label wireframes as design interpretations, not screenshots of the legacy system.

**Future state (Reimagine only):** proposed streamlined flows, redesigns, approval changes, removed/added steps, security/access implications, API/data/interface changes and rationale. Each material delta must map to original behavior and either (a) approved human future-state context or (b) an explicitly AI-suggested `proposed_unapproved` option, plus rationale, impact and acceptance criteria; decision owner may remain `unassigned` until a person reviews. Unapproved proposals are drafts and cannot be silently included as final implementation requirements.

Require review of current-state before future-state finalization, and separate approval of material future-state differences. Each wireframe must be traceable to an evidence/requirement ID and include status/confidence.

## 8. AI-DLC packages (independently consumable)

Produce **separate, deterministic/versioned packages** from one pinned assessment baseline:

```text
packages/<version>/functional-parity/ai-dlc-package.zip
packages/<version>/reimagine/ai-dlc-package.zip
```

**Parity package:** current-state wireframes and flows, validated/qualified functional requirements and acceptance tests, business rules, data model/contracts, APIs/integration/security/batch/infrastructure design requirements, implementation backlog, agent instructions/skills/prompts, equivalence strategy, known gaps, human decisions, approvals and traceability.

**Reimagine package:** parity baseline **plus** future-state wireframes and processes, explicit change/delta matrix, approved new requirements **and separately labeled unapproved proposals**, impact analysis, target-state acceptance tests and implementation guidance. Do not overwrite or retroactively rewrite baseline evidence. Package consumers must be instructed not to implement unapproved proposals as requirements.

Each package includes a machine-readable manifest with schema/version, modernization mode, source/run fingerprints, artifact hashes, coverage/confidence statuses, approval hashes, unresolved decisions and reference locations. It must be usable in a clean directory by a separate engineer/agent without relying on the original Reframe workspace or live AWS session. Include only permitted sanitized evidence/derived requirements; keep raw human uploads, secrets and customer documents private by default. Avoid leaking local absolute paths or cloud identifiers into public examples or portable exports.

Reproducibility: fixed source and approved input hashes should yield deterministic content and package manifest (normalize timestamps/order/archive metadata where needed); changes invalidate the expected outputs. Implement package integrity verification.

## 9. Agent roles, state machine and quality gates

**Orchestrator:** owns durable workflow state, idempotent retries, input hashes, stage transitions, budgets, human tasks, provenance, concurrency locks, approval invalidation and stop conditions. Never allow executor to silently grant itself an approval.

**Executor:** ingests evidence, prepares derived requirements, wireframes and packages, runs deterministic checks, records outputs and open questions, and applies reviewer feedback without fabricating missing evidence.

**Independent Reviewer:** reads original source and inputs, checks evidence mappings, actual test outcomes and privacy/scope boundaries, returns structured `PASS | FAIL | NEEDS_HUMAN_DECISION` with location-specific findings and severity. Use separate context/session and avoid sharing executor's self-assessment as proof.

Enforce named gates at: source/project verification; AWS resource creation/job start; assessment baseline; independent review or recorded skip; current-state specification; future-state change set (only Reimagine); final package. Material financial rules, conflicting human statements, data conversion risks, security and production deployment remain human-controlled. Distinguish “skipped” from “passed.” Define retries, maximum cost/time, escalation and resume for every stage.

## 10. Tests and acceptance journeys

Add unit, integration (mocked AWS/provider), CLI, state-transition, packaging and regression tests. Existing tests must keep passing. Live AWS tests must require deliberate opt-in, isolated resources and cleanup authorization.

Required cases:
- Same canonical repo/same revision: resume/reuse, no duplicate AWS job unless user expressly selects new assessment.
- Same repo/new revision: same project resources, new dated run prefix/new AWS job after approval.
- New repo: isolated project resources; same-name/fork/remote-change collisions blocked or resolved explicitly.
- Interrupted AWS create/upload/assessment: idempotent resume, correct partial-state recovery.
- Concurrent run attempts: no path overwrites and no duplicate resource creation.
- Actual AWS Transform human task schemas: block/resume safely without guessed values.
- Skip reviewer: preserve unverified flags; reviewed path: fail/rework/review and bounded retries.
- Parity: only current-state wireframes and parity AI-DLC.
- Reimagine: current-state baseline **and** future-state wireframes; both mode-specific packages selectable without rerunning assessment.
- Human context: missing/empty context must still yield complete source-derived drafts in both modes; import, provenance, approved current clarification, approved future change, AI-only future proposals marked unapproved, contradictory evidence as final-approval blocker, rejected/superseded exclusion, changed context invalidating only impacted outputs.
- Malformed/malicious documents and prompt-injection text cannot execute instructions or bypass gates.
- Package manifest completeness, portability, checksums, deterministic generation, security scan, no private paths/IDs/secrets.
- Missing legacy files, unsupported data, incomplete batch tests and unverified business rules remain visible as gaps.
- **Offline demo A:** Existing repo → new approved assessment run reusing AWS infrastructure (mocked) → skip review → functional-parity wireframes → parity AI-DLC.
- **Offline demo B:** New repo → isolated setup (mocked) → assessment → reviewed human documents + conflicts → independent review → reimagine → current and future wireframes → reimagine AI-DLC.

For each acceptance test, show exact command, fixture, asserted observable behavior and pass/fail evidence. Clearly mark which checks are mocked and which were actually tested against a real provider.

## 11. Implementation plan and delivery

Work in reviewable increments; no monolithic rewrite. **Run the entire safe local sequence without repeatedly asking for permission**, but stop at the defined hard boundaries and retain compatibility:
1. Read-only audit and implementation matrix; map to actual modules and schemas.
2. Implement opt-in modernization-mode selection and additional output directories **alongside** existing preview/package paths; verify old paths are untouched.
3. Implement optional human context intake, provenance, conflicts and targeted dependency invalidation through existing state and approval services.
4. Integrate no-context and accepted-context inputs into current-state and future-state wireframe generation; gate AI-suggested Reimagine proposals.
5. Generate separate portable parity/reimagine AI-DLC packages alongside the legacy single package.
6. Complete executor/reviewer contracts, deterministic checks, CLI journeys, CI tests, documentation and Mermaid diagrams.
7. Add an opt-in stable project registry, source identity and repeat-run resource reuse using existing abstractions; **do not migrate old AWS layout or state as part of this increment**. Mock new jobs and prefixes; preserve resume semantics.
8. Refine AWS Transform task and artifact namespace contracts, idempotency and approval gates for the opt-in new-run workflow; obtain explicit approval before any live AWS/provider validation or breaking migration.

At each increment: run targeted tests + full relevant suite, compare against the measured 306/7 (or newly verified) baseline, report changes, quality-gate status, failed checks, security/privacy checks and next steps. If a required stage conflicts with existing persisted paths, leave that stage behind an opt-in interface and document a migration proposal rather than breaking a default. Do not claim finished until both offline acceptance journeys pass and remaining live AWS/provider validation is explicitly identified.

### Final response format for Cursor

Report concisely:
1. Implementation matrix (verified working / partial / missing / untested).
2. Architectural changes and exact files touched.
3. CLI commands and example folder outputs for both modes.
4. Automated test results and evidence, with mocked/live distinctions.
5. Remaining gaps, migration notes and next live acceptance gates.
6. Whether any commit/branch/PR action was taken (must comply with user approval and identity rules).
