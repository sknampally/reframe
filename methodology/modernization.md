# Modernization methodology

This is a proposed delivery method, not a completed migration record. Each stage ends with the corresponding gate in [quality-gates](../quality-gates/README.md).

| Stage | Work | Required deliverable |
| --- | --- | --- |
| [1. Scope and discovery](01-discovery.md) | Pin source; inventory batch, online, data, security, integrations, schedulers, and optional modules. Record missing source and runtime dependencies. | Approved scope, source manifest, dependency map, unknowns register |
| [2. Requirements validation](02-requirements-validation.md) | Treat extracted rules as candidates. Verify each material rule against source, including error paths, boundaries, state changes, and call context. Resolve duplicates without erasing distinct behavior. | Rule-to-source-to-test matrix; domain-owner decisions |
| [3. Target architecture](03-target-architecture.md) | Compare rehost, refactor, rewrite, retain, and hybrid choices per bounded capability. Estimate cost, operational ownership, and migration risk. | Architecture decisions, domain boundaries, nonfunctional requirements |
| [4. UI and data design](04-ui-and-data-design.md) | Preserve observable online behavior, authorization, transaction boundaries, record semantics, encoding, numeric precision, and reconciliation. | UI/API contracts, data mapping, conversion and recovery plans |
| [5. Incremental implementation](05-implementation.md) | Implement one vertical slice in the language and runtime the team selects. Test boundary and failure behavior before expanding scope. Keep deterministic rules separate from adapters. | Reviewable implementation, tests, dependency manifest, traceability |
| [6. Equivalence and operations](06-equivalence-testing.md) | Compare legacy and target on identical controlled inputs. Test ordering, concurrency, restart, rerun, data reconciliation, security, load, and recovery. | Differential results, deviations, operational runbooks, rollback rehearsal |
| 7. Human release decision | Resolve blocking discrepancies; rehearse cutover, validate rollback triggers and ownership, and record explicit authorization. | Signed decision for exact release and environment; verified post-release checks |

Use the [prompt library](../prompts/README.md) to assign bounded tasks. Start with one source-derived rule and independent review; these instructions have not yet been validated by an end-to-end pilot.

## Evidence rules

Every material claim needs a status: **verified** (reproducible evidence inspected), **reported** (asserted elsewhere, not reproduced), **inferred** (reasoning stated), **proposed** (future design), or **unknown**. Do not upgrade status just because a tool reported success.

A source reference includes repository, pinned commit, path, symbol or paragraph, and line span where meaningful. Line numbers alone drift. A requirement links to its source and test oracle; a test links to controlled inputs, preconditions, expected outputs and state changes, actual results, and environment version. Keep sensitive evidence private and publish only approved derivatives.

## Mainframe-specific challenges

- COBOL numeric representation, decimal scale, truncation, rounding, sign, overflow, and invalid data must be characterized before selecting target types.
- EBCDIC conversion, collating sequences, fixed-width records, REDEFINES, and variable-length layouts need explicit byte-level fixtures.
- VSAM key and alternate-index behavior cannot be assumed equivalent to a relational schema.
- CICS transactions, screen state, session handling, authorization, and commit/rollback boundaries need observable behavior tests.
- Batch scheduling, return codes, business dates, GDG lifecycle, checkpoints, restart/rerun, duplicate posting, and reconciliation are part of business correctness.
- External services, assembler, IMS, Db2, messaging, utilities, and missing programs remain explicit dependencies until characterized.

## Coverage and acceptance

Track validated material rules over in-scope material rules, requirements with executable oracles, paths exercised (positive, negative, boundary, recovery), reconciled records and control totals, and unresolved discrepancies by severity. Declare denominator, exclusions, environment, and evidence revision for every metric. Entry-point coverage does not measure business-rule correctness.

Define performance thresholds, tolerated deviations, and recovery objectives before running acceptance tests. Business owners approve intentional behavior changes; executors cannot silently normalize differences as improvements. A pilot must not be presented as proof for an entire application portfolio.
