# Reframe

*From legacy source to a reviewed modernization plan.*

A CLI for taking pinned mainframe source through assessment, independent validation,
business review, and an approved implementation preparation package. C#/.NET on AWS
is an intended target; target design remains an explicit architecture decision.

**Status: four-phase POC tested with offline fixtures. One repaired CardDemo AWS
assessment reached the human review gate; uninterrupted release acceptance and
live agent-provider acceptance remain pending.** No application buildout, deployment, data
migration or runtime equivalence is claimed. AI assistance is used to develop this
framework and to prepare candidate artifacts; humans retain approval authority.

**Mac or Windows? Start with the [platform installation and update guide](docs/platform-install.md).**
Windows currently uses Ubuntu/WSL2; native PowerShell runtime is not supported.
The final `.` in the install command is required.

**New workstation? Follow the [engineer installation and login guide](docs/engineer-setup.md) first.**
Then use the [CLI guide](docs/cli-guide.md) and [four-phase workflow](docs/four-phase-workflow.md).
Supply an S3 source ZIP, GitHub URL or clean local Git checkout, configure your own
AWS/provider access, and use the built-in protocols.
No custom prompt writing or manual agent handoffs are required for normal operation.

## Run the guided workflow

Install Git and uv, AWS CLI and your chosen design provider (Claude Code or Codex)
using the [engineer setup guide](docs/engineer-setup.md). Authenticate your AWS profile
and provider. AWS Transform application access is also required; terminal AWS login
alone is insufficient. Setup checks the configured runtimes and offers resource setup.

On macOS/Linux, or inside Ubuntu/WSL2 on Windows:

```bash
git clone https://github.com/sknampally/reframe.git
cd reframe
uv tool install --force --managed-python --python 3.12 --editable .
reframe
```

Choose **1 — Guided start / continue**, or launch it directly with `reframe flow`.
No custom prompts or manual command sequence are needed for the direct draft route.

1. **Set up or continue.** Supply your source, private output directory, AWS profile,
   region, bucket and provider. AWS profile has **no default**: enter it explicitly
   (use `aws configure list-profiles` to find configured names). The CLI verifies
   access and offers SSO login when required. Continuing a saved run reuses its
   explicitly selected profile. For an existing project, choose **continue** or
   **fresh**. Fresh uses a separate output parent; it never deletes the previous run.
2. **Assess.** Confirm source upload and the new AWS assessment. The flow waits for
   a bounded interval and resumes the saved job on your next `/flow`. Authentication,
   connector activation or service blockers can still require action; this is not a
   guarantee of unattended AWS completion.
3. **Review assessment.** Inspect the saved assessment report, then explicitly record
   approval and confirm that the findings match your source. Answer **no** to pause.
4. **Choose preparation.** Select **draft** to skip independent Claude verification,
   record the accountable owner and reason, and choose Claude or Codex for design
   generation. Or select **verify** to run the executor/reviewer within the saved
   budget; unresolved decisions and review gates remain enforced.
5. **Collect outputs.** The draft route generates HTML wireframes, proposed API and
   design specifications, and an AI-DLC draft ZIP in one operation. Use `/privacy`
   then `/artifacts` to see local report and package paths. Review the draft with
   the SME, architect, business owner and technical lead before engineering use.

**Skipping verification does not approve the design.** Draft findings are marked
AWS-assumed, independent verification is skipped, stakeholder decisions remain pending,
and acceptance tests are NOT RUN. A design-generation Claude call is distinct from
executor/reviewer verification. No application or infrastructure is built.

All outputs stay in a private `<repository>_assessment` folder under your chosen parent:

```text
<repository>_assessment/
  source/                       # managed source checkout, when applicable
  runs/                         # assessment evidence and review reports
  preparation/                  # independent verification, if selected
  previews/guided-draft/handoff/ # HTML wireframes and draft specifications
  packages/guided-draft/         # ai-dlc-draft.zip
  reports/index.html            # report and package links
```

Return with `reframe` → **1** or `/flow`. Reuse the same draft name to resume or
verify/reuse an existing export; provider failures do not become approvals. Ctrl+C
cancels the local wait, not an already-started AWS job. Saved evidence is preserved.
The verified route may pause for SME decisions or budget changes; `/help` and
`/guide workflow` describe these controls. `/init` remains available for setup alone.

After pulling a merged update, reinstall dependencies from the repository directory:

```bash
git switch main
git pull --ff-only
uv tool install --force --managed-python --python 3.12 --editable .
reframe
```

`reframe demo` is a simulated preview with no service calls. Existing commands remain
available for scripts. See the [terminal guide](docs/terminal-console.md).

## Why this framework exists

Generated documentation is useful input, but it does not prove business behavior. A credible modernization must connect source evidence to validated requirements, implementation, and measured equivalence. This framework defines that chain and the decisions people must retain.

## Evidence and capability status

| Capability | Evidence status | Public repository status |
| --- | --- | --- |
| AWS Transform assessment | One repaired CardDemo run reached sealed evidence review; uninterrupted acceptance pending | S3 ZIP or GitHub/local source, chosen S3 destination, workspace/connector setup, existing local/S3 findings, wait/resume |
| Independent validation | Structural checks and synthetic handoff verified | Claude Sonnet executor plus Claude Opus reviewer independently request source and findings; distinct pinned model IDs, bounded rework |
| Approval gates | Hash invalidation tested | Separate assessment, SME/architect and business specification gates |
| HTML business review | Deterministic rendering and escaping tested | Claude/Codex-generated screen content; inert HTML with rules, states and specifications |
| Implementation preparation | Synthetic four-phase package tested | Versioned ZIP with specifications, prompts, backlog, decisions, monorepo proposal and checksums |
| Repeated assessment comparison | Fixture comparison tested; live CardDemo runs pending | Preserved snapshots, normalized content diff and optional source-grounded AI comparison |
| Application buildout, migration and equivalence | Not implemented by this POC | Future engineering work guided by the preparation package |

A service reporting a completed assessment step does not establish correct requirements. A generated test plan is not an executed test suite. See [limitations](docs/limitations.md).

## POC workflow

![Four-phase POC: assessment, separate Claude Sonnet executor and Opus reviewer, HTML business review, and approved implementation package](docs/images/poc-workflow.svg)

[Open the full-size workflow](docs/images/poc-workflow.svg) · [Execution guide](docs/cli-guide.md)

Each phase stops at its approval boundary. Service completion and reviewer PASS do
not approve business requirements. Private records preserve provenance, uncertainty,
review results and exact approved versions. Deferred decisions retain explicit
blocked downstream work. Microservices are evaluated against alternatives, not assumed.

The [CLI guide](docs/cli-guide.md) describes implemented behavior and limitations.
The [broader reference architecture](docs/images/reference-architecture.svg) and
[detailed delivery workflow](architecture/multi-agent-workflow.md) include future
implementation/equivalence stages beyond this preparation POC.

## Next: prove the CardDemo journey

1. Configure private AWS/Transform access and confirm the selected S3 bucket, workspace and connector. The CLI setup must pass before starting assessment jobs.
2. Pin one CardDemo commit and the sign-on scope. Run two independent assessments with matching inputs and recorded human task choices; preserve both snapshots.
3. Compare run A and run B, inspect material differences against source, and approve the baseline for validation. More findings do not automatically mean better findings.
4. Run the Sonnet executor and Opus reviewer. Resolve or explicitly defer SME questions and approve the validated specifications.
5. Generate the HTML review site, collect stakeholder feedback, approve the exact version and create the implementation package.

**Acceptance requires a live journey, not just generated files:** verify interruption/resume, approval stops, evidence traceability, reviewer corrections and package completeness. Full CardDemo/AWS acceptance is still pending. No application buildout is part of this POC.

Follow the [CLI guide](docs/cli-guide.md) and [two-run comparison protocol](docs/assessment-comparison.md); the framework supplies the role prompts and manages the handoffs.

## Start here

The four-phase CLI is the primary POC interface. The six methodology guides below cover the broader modernization lifecycle, including future buildout and equivalence work.

| Phase | Guide |
| --- | --- |
| 1 | [Scope and discovery](methodology/01-discovery.md) |
| 2 | [Requirements validation](methodology/02-requirements-validation.md) |
| 3 | [Target architecture](methodology/03-target-architecture.md) |
| 4 | [UI and data design](methodology/04-ui-and-data-design.md) |
| 5 | [Incremental implementation](methodology/05-implementation.md) |
| 6 | [Equivalence testing and release readiness](methodology/06-equivalence-testing.md) |

**Primary path:** [four-phase CLI setup and execution](docs/cli-guide.md).
**Experiment:** [two-run CardDemo comparison](docs/assessment-comparison.md).
**Legacy diagnostics:** [single-rule Kiro adapter](docs/kiro-automation.md) and
[manual pilot runner](docs/running-a-pilot.md). These are retained for compatibility;
they are not the new four-phase interface.

1. Read the [methodology](methodology/modernization.md) and [quality gates](quality-gates/README.md).
2. Define a bounded task using the [task contract](contracts/task-contract.yaml).
3. Assign the [orchestrator](agents/orchestrator.md), [executor](agents/executor.md), and [reviewer](agents/independent-reviewer.md) responsibilities. The CLI dispatches roles; the manual instructions remain diagnostic references.
4. Walk through the [synthetic CardDemo skeleton](examples/carddemo/README.md). Pin upstream source before making source-specific claims.
5. Record evidence, unresolved gaps, review results, and human decisions. Missing evidence blocks the relevant gate.

## Repository map

The current foundation contains the following files. The tree includes documentation and the initial controllers; code presence does not establish live-service acceptance.

```text
reframe/
├── README.md
├── reframe                         # Repository-local entry point
├── pyproject.toml                    # Installable reframe command
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── THIRD_PARTY.md
├── .gitignore
├── .github/
│   └── pull_request_template.md
├── architecture/
│   └── multi-agent-workflow.md
├── methodology/
│   ├── modernization.md
│   ├── 01-discovery.md
│   ├── 02-requirements-validation.md
│   ├── 03-target-architecture.md
│   ├── 04-ui-and-data-design.md
│   ├── 05-implementation.md
│   └── 06-equivalence-testing.md
├── prompts/
│   ├── README.md
│   ├── 01-executor.md
│   ├── 02-independent-reviewer.md
│   ├── 03-orchestrator.md
│   ├── 04-rule-validation-pilot.md
│   ├── automated/                   # Legacy single-rule protocols
│   │   ├── executor.md
│   │   └── reviewer.md
│   └── pipeline/
│       ├── executor.md
│       ├── reviewer.md
│       ├── wireframes.md
│       └── comparison.md
├── agents/
│   ├── orchestrator.md
│   ├── executor.md
│   └── independent-reviewer.md
├── contracts/
│   ├── README.md
│   ├── task-contract.yaml
│   ├── review-record.yaml
│   └── approval-record.yaml
├── quality-gates/
│   └── README.md
├── examples/
│   ├── config/
│   │   ├── claude-settings.example.json
│   │   └── kiro-settings.example.json
│   └── carddemo/
│       ├── README.md
│       └── synthetic-example.md
├── scripts/
│   ├── pilot.py
│   ├── kiro_workflow.py
│   ├── modernize_terminal.py
│   ├── modernize_setup.py
│   ├── modernize.py
│   ├── modernize_models.py
│   └── modernize_artifacts.py
├── tests/
│   ├── test_pilot.py
│   ├── test_kiro_workflow.py
│   ├── test_mcp_transport.py
│   ├── test_terminal.py
│   ├── test_guided_cli.py
│   ├── test_modernize.py
│   └── fixtures/
│       └── mcp_server.py
└── docs/
    ├── images/
    │   ├── terminal-preview.svg
    │   ├── poc-workflow.svg
    │   └── reference-architecture.svg
    ├── platform-install.md
    ├── terminal-console.md
    ├── engineer-setup.md
    ├── cli-guide.md
    ├── assessment-comparison.md
    ├── kiro-automation.md
    ├── limitations.md
    └── running-a-pilot.md
```

Installed `skills/` and `docs/lessons-learned.md` remain future additions; they are not part of the current tree. Lessons learned require actual reviewed execution evidence.

| Path | Purpose |
| --- | --- |
| [architecture](architecture/multi-agent-workflow.md) | Intended workflow, trust boundaries, and recovery |
| [methodology](methodology/modernization.md) | Discovery through cutover and rollback |
| [agents](agents/orchestrator.md) | Bounded role instructions |
| [prompts](prompts/README.md) | Stable role instructions consuming one task manifest |
| [reframe CLI](docs/cli-guide.md) | Four-phase setup, assessment, validation, HTML and packaging |
| [assessment comparison](docs/assessment-comparison.md) | Controlled two-run experiment and its limits |
| [Kiro automation](docs/kiro-automation.md) | Legacy single-rule adapter diagnostics |
| [pilot runner](docs/running-a-pilot.md) | Manual diagnostic operations and record-integrity checks |
| [contracts](contracts/README.md) | Task, review, and approval record templates |
| [quality-gates](quality-gates/README.md) | Evidence required to advance |
| [examples/carddemo](examples/carddemo/README.md) | Sanitized case-study skeleton and synthetic example |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Attribution, validation, and PR process |
| [SECURITY.md](SECURITY.md) | Publication boundaries and vulnerability reporting |

## Delivery roadmap

- Foundation: method, roles, gates, diagrams, public boundaries, phase guides, and stable prompts are supplied.
- Local pilot runner: shared manifest, metered operations, sealing, and review preflight are implemented for manual diagnostics.
- Validation pilot: pin public source and validate one rule with independently reviewed evidence.
- Preparation CLI: four phases, Claude Code review, HTML, versioned packages and repeated assessment comparison implemented; live provider/AWS acceptance pending.
- Implementation pilot: build one vertical .NET slice with measured equivalence and failure-path tests.
- Operational pilot: rehearse migration, recovery, observability, and rollback before any release claim.

Each milestone requires its own reviewed evidence; the roadmap is not a list of working features.

## Responsible publication and attribution

Publish original framework material and approved synthetic examples only. Never add employer or client inventories, credentials, cloud resource identifiers, private assessment exports, or raw AWS Transform artifacts. Redacting a few names from an internal export is insufficient.

This foundation was prepared with AI assistance using Codex. AI assistance does not imply independent validation. The repository owner reviews and decides what to merge; commit identity must match the owner's verified personal GitHub identity, without agent co-author attribution or concealment of assistance.

Original framework material is covered by the [MIT license](LICENSE). [AWS CardDemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo) is a separate upstream project with its own license; no application source or datasets are bundled here. See [third-party guidance](THIRD_PARTY.md).
