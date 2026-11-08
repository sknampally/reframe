# 03 — Target architecture

Purpose: choose a migration strategy and boundaries supported by validated behavior and operational needs. The target language, runtime, platform, and UI technology are decisions to justify. No language is the default.

## Inputs and prerequisites

Validated requirements for the selected slice, dependency and data boundaries, security needs, operational ownership, deployment constraints, and agreed performance/recovery objectives. Unresolved behavior cannot be hidden behind a target design assumption.

## Procedure

1. Compare retain, rehost, refactor, rewrite, and hybrid options for the bounded capability. State decision drivers, rejected options, migration risk, and reversibility.
2. Define responsibilities and interfaces using business behavior and transaction boundaries. Do not convert every COBOL program into a service by default.
3. Map online, batch, scheduler, data, and integration responsibilities. Preserve ordering, atomicity, error semantics, and ownership or identify an intentional approved change.
4. Decide where deterministic domain logic ends and storage, UI, and cloud adapters begin. A UI technology is a decision, not an automatic requirement.
5. Define authentication/authorization boundaries, secret handling, observability, audit, failure handling, and recovery ownership.
6. Record capacity and cost assumptions, how they will be measured, and the allowed experiment budget. Do not create resources to validate estimates without scoped authority.
7. Define coexistence, migration, reconciliation, rollback triggers, and the owner of each operational action. Obtain architecture review before implementation.

## Architecture decision record

Record decision ID, baseline and requirement IDs, context, options, decision, consequences, data/transaction effects, security controls, operational owner, assumptions needing experiments, and exact approval scope. Keep resource identifiers and private deployment details outside public examples.

## Exit: G3

The selected design traces to requirements, compares credible alternatives, defines failure and rollback behavior, and has human architecture approval. Unmeasured capacity/cost claims remain assumptions.

Next: [UI and data design](04-ui-and-data-design.md).
