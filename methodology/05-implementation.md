# 05 — Incremental implementation

Purpose: implement one reviewable vertical slice without losing its evidence chain. This repository supplies guidance, not generated application code or a build pipeline. The team chooses the language and the AI coding tool.

## Inputs and prerequisites

Validated slice requirements, approved architecture and UI/data contracts, synthetic fixtures, accepted test oracles, allowed tools/actions, and a bounded task. Use the [executor role contract](../agents/executor.md).

## Procedure

1. Select one end-to-end behavior with explicit exclusions. List requirement IDs, source anchors, output artifacts, and acceptance checks before editing.
2. Implement deterministic domain behavior separately from infrastructure adapters. Preserve numeric, state, authorization, ordering, and error semantics; do not silently modernize behavior into something different.
3. Add meaningful tests for positive, rejection, boundary, and applicable recovery paths. Keep expected results independent from implementation logic.
4. Run the relevant build, tests, static analysis, and dependency/security checks in the approved environment. Report commands, versions, exit status, failures, skips, and result locations privately as appropriate.
5. Submit a candidate revision with requirement-to-code-to-test links, actual results, assumptions, and unresolved findings. If checks cannot run, explain why and leave their acceptance criteria unproven.
6. Have the [reviewer role](../agents/independent-reviewer.md) inspect source, candidate, and tests independently. Fix findings on a new revision; re-review material changes.
7. Verify personal GitHub attribution before commits and use a feature branch/PR. The human owner reviews and merges; no direct main push.

## Deliverables

Implementation diff; traceability matrix; reproducible build/test instructions; actual check results; review record for the exact revision; residual-risk decisions; explicit limitations.

## Exit: G4

The candidate builds and meets the slice's technical criteria, material tests ran with reported results, and independent review has no unresolved critical/high findings. Lower-severity deferrals need human acceptance and an owner. Merge requires the repository owner's decision.

A merged implementation still needs [equivalence testing](06-equivalence-testing.md); unit test success does not prove legacy parity.
