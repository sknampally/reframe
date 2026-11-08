# CardDemo case-study skeleton

For the new preparation POC, use the [four-phase CLI](../../docs/cli-guide.md) and
[repeatability experiment](../../docs/assessment-comparison.md). Pin a reviewed
CardDemo commit and begin with the sign-on journey. Two fresh AWS assessments must
retain separate private outputs. No live result is bundled or claimed here.

**Status: sanitized skeleton, not a reproducible modernization result.** No CardDemo application source, data, AWS Transform export, cloud resource configuration, private inventory, or private evidence link is included.

Public upstream: [AWS CardDemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo). Consult its own license and notices before reuse. This framework links to upstream rather than redistributing it.

## What has been reported

A prior private workflow reported assessment activity, generated business-rule candidates, seed-only decomposition, and a draft batch test plan. Test-plan refinement remained pending human input. The underlying outputs have not been independently verified in this public repository, so they are not a public benchmark or completion claim.

The useful lessons are methodological: generated job-level coverage does not prove rule-level coverage; blank structured expected results do not establish an executable oracle; unassigned dependencies and missing source remain gaps. Online workflows, rejection paths, concurrency, encoding, and recovery require explicit tests beyond a batch plan.

## Fill-in case-study structure

| Section | Public record required before a result claim |
| --- | --- |
| Baseline | Upstream commit, selected public modules, exclusions, and tool versions — currently not populated |
| Scope | One bounded behavior and approved synthetic fixtures |
| Evidence | Source path, paragraph/symbol, revision, and reviewed rule mapping |
| Target | Reasoned design decision for the chosen language and its observable contract |
| Verification | Actual commands, environment, expected and actual results, reviewer findings |
| Outcome | Measured result and limitations; no claim until independently reviewed |

Start with [synthetic rule and test example](synthetic-example.md). Replace it only through a separate PR with source-backed evidence. The example is invented; it must never be cited as a CardDemo business rule.

Use the [one-rule validation prompt](../../prompts/04-rule-validation-pilot.md) for the first bounded task and the [review prompt](../../prompts/02-independent-reviewer.md) for independent source review. No pilot execution is recorded here yet.

## Proposed first reproducible pilot

1. Pin the upstream commit and select one public-source behavior with complete dependencies.
2. Derive its rule with paragraph-level references; have an independent reviewer verify it.
3. Create synthetic positive, rejection, boundary, and recovery fixtures with deterministic expected state.
4. Implement one target slice and run legacy/target comparisons in an authorized environment.
5. Publish only approved source references, synthetic fixtures, reproducible checks, and bounded conclusions.

No production or client data is needed. Do not copy a private assessment bundle into this directory to make the case study look complete.
