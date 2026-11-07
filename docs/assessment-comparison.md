# CardDemo repeatability experiment

Purpose: observe how two AWS Transform assessments differ for the same source and
recorded configuration. This is not a benchmark of universal accuracy or proof that
one run is correct because it produced more rules.

1. Retain earlier findings as a private historical baseline. Do not delete them.
   Incomplete prior assessments must be labeled incomplete; do not silently compare
   them as equivalent to fully completed runs.
2. Pin the public CardDemo repository to a reviewed commit. Use the same checkout,
   tracked upload file selection, scope, AWS region, Transform workspace/connector,
   framework/MCP versions and job intent for both new runs. The ZIP is deterministic
   for the same included bytes. The private upload manifest lists included hashes
   and excluded names. No source is published in this framework.
3. Start run-a, record required AWS task choices and wait for exported findings.
   Start run-b independently with the same inputs and human choices. Retain all
   service receipts and the exact downloaded findings. AWS may change its underlying
   implementation; record timestamps and available version metadata rather than
   claiming the service version is controlled.
4. Use `reframe compare` for the content diff. JSON key order and trailing text
   whitespace are normalized; meaningful values, IDs and source anchors are not
   silently removed. Generated names/IDs may still produce noise. Recorded settings
   mismatches are surfaced. Equality is equality of collected outputs only.
5. Use `compare --semantic` for an evidence-grounded AI-assisted analysis. Examine
   rules, dependencies, data relationships, documentation, tests, scope omissions
   and contradictions. For each material difference, retain left/right evidence,
   primary source anchor, impact and a supported/unsupported/SME-required verdict.
6. The owner reviews the comparison, especially incomplete coverage and ambiguous
   semantics, and selects the assessment baseline for Phase 2. Do not merge two
   assessments blindly or automatically call the longer output better.

Record human AWS task choices in private experiment notes alongside the run records;
this initial CLI does not extract/normalize every HITL response or lock AWS service
versions. Without matching choices, differences may be caused by inputs rather than
nondeterminism. Both collected snapshots must be complete enough for the question
being asked. No current public result claims the live experiment has occurred.
