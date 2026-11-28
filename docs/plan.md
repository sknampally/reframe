# Reframe plan

The parts of Reframe are named for what they do: the AWS Transform assessment (pipeline 1), the Claude assessment (pipeline 2), the reconciliation (pipeline 3) and the final requirements, wireframes and AI-DLC package (pipeline 4, planned). The numbers stay as short labels in the plan and the specifications.

Status: draft for the owner's agreement, with [the specification](specification.md).

## How the work is done

- One epic for pipeline 1, with the stories below on the board.
- Every story is built, then the builder runs one full round of acceptance from the CLI (specification section 9), then opens one PR with a commit per story. The owner tests from the CLI and merges.
- Code is new and laid out by area (specification section 6). It reuses what the earlier build proved live (the Terraform modules, the MCP client sessions, intent and receipt records, the task classifier), moved across one piece at a time with its tests.
- Unit tests run offline against recorded AWS reply shapes from spike 1, with identifiers removed. Live runs are the acceptance test.

## Stories

| Story | Title | Specification | Done when |
|---|---|---|---|
| P1-01 | Package skeleton, CLI and CI | §3 | `reframe --help` lists the commands; CI runs the tests |
| P1-02 | Run folder, event log, intent and receipt records, resume | §4, §7, §8 | A stopped run resumes without repeating a change; UNCERTAIN is settled from AWS's state |
| P1-03 | Preflight | §4 stage 0 | Each missing piece stops the run with the exact fix |
| P1-04 | Source input (folder, ZIP, GitHub), snapshot and upload | §4 stages 1, 3 | Each source form gives a checked archive whose SHA-256 matches the object in S3 |
| P1-05 | Terraform roots and apply | §4 stage 2 | Plans that only create, applied; outputs recorded |
| P1-15 | Neptune graph: a new one or an existing cluster | §4 stage 2 | The clusters are listed with what each lacks; a usable one can be chosen and is used as it is, never changed |
| P1-17 | Code layout by area | §6 | Core, source, infrastructure, Transform and report code each in its own folder, with tests alongside |
| P1-06 | AWS Transform setup: workspace and reimagine connector | §4 stage 4 | Connector ACTIVE, IDs recorded from AWS's replies |
| P1-07 | Job creation and the driver loop | §4 stages 5, 6 | The job is polled and its tasks classified; done means every plan step SUCCEEDED with "Generate requirements" in the plan |
| P1-08 | Autonomous answers: plan, source location, function selection, confirmation, defaults | §4 stage 6, §5 | Autonomous answers recorded in `decisions.json` |
| P1-25 | Autonomous answers: the readiness menu after the selection | §4 stage 6 | The readiness menu is answered with its one choice that proceeds; "no further action is needed" is a notice; the catalog's review form is never submitted while a question offering choices waits; offline tests for each wording recorded on 23 November 2026 |
| P1-26 | Autonomous answers: a name that is both a domain and a function | §4 stage 6 | When AWS asks whether a name in the selection means the business domain or the business function, autonomous mode answers the domain in the words AWS asks for; interactive mode offers both readings; offline tests for each wording recorded on a second test codebase on 25 November 2026 |
| P1-09 | Interactive mode: `reframe answer` | §5 | Every waiting question can be answered from the CLI |
| P1-10 | Retrieval: download, check, unpack, manifest, summary | §4 stage 7 | Requirements and assessment downloaded and summarized |
| P1-16 | HTML report | §4 stage 8 | One self-contained report covers the overview, functions, requirements, business rules, open questions, data model, assessment and decisions |
| P1-11 | Teardown: inventory, recorded consent, deletion by recorded ID | §4 stage 9 | Every resource is listed at once, consent is recorded before anything is deleted, and nothing the run created remains |
| P1-12 | `status` and `events` | §3 | Shows stage, plan steps, waiting question and next step |
| P1-13 | Live acceptance from the CLI | §9 | Every run in section 9 passes; results recorded without identifiers |
| P1-14 | README | | Written last, from the tested commands |
| P1-18 | Guided prerequisites: check, install on request, remember | §3 | Each missing tool is named with its install command for macOS or Linux and installed only on a yes; Terraform is installed by Reframe and checked against HashiCorp's SHA-256 sums; the MCP server is fetched once; paths are saved and used |
| P1-19 | Guided run: sign-in, source and choices | §3 | `reframe run` with no arguments chooses a profile, checks or starts the sign-in, takes and checks the source, asks the mode, project and Neptune choice, shows a summary, and starts on one yes; `reframe run --source` asks nothing |
| P1-20 | The look of the first console | §3 | Header panel, coloured status, tables and numbered choices in the guided run and `reframe status`; plain output without a terminal |
| P1-21 | Home screen and help in the look of the first console | §3 | `reframe` alone shows the header, the runs with their status, the suggested next step and a numbered menu that runs each command; `reframe --help` and each command's help show tables of commands and options with examples; plain help and exit code 2 without a terminal |
| P1-22 | Read AWS's documents in full | §4 stage 8 | Domain and function names and descriptions come from AWS's catalog; capabilities, user stories, schedules, recovery notes, requirements, open questions and owners are read from every requirements document; each open question in AWS's files is counted once |
| P1-23 | Business review report, from overview down to each requirement | §4 stage 8 | Overview, domain, function and capability levels, each requirement with the business rules behind it, a breadcrumb and search; technical detail in its own section; checked on the outputs of every live run |
| P1-24 | Review marks and their export | §4 stage 8 | Agree, Needs change or Question with a comment on each requirement, rule and open question; progress per domain and function; CSV and JSON downloads naming the run, the source's SHA-256 and the reviewer |

## Decisions

These follow the owner's direction; change any of them before the build finishes.

- Sources: a local folder, a local ZIP file or a GitHub repository; any mainframe codebase.
- AWS Transform plan: assess and reimagine, over the full source including the unassigned programs.
- Neptune: a new graph by default in autonomous mode; an existing cluster when the person chooses one.
- Unknown questions in autonomous mode: AWS's default when it marks one, recorded; otherwise wait for `reframe answer`.
- Teardown: only on `reframe teardown`, after the full list is shown and consent is recorded.
- Where it runs: locally on the owner's Mac.

## The Claude assessment and the comparison (pipeline 2)

Specification: [pipeline-2.md](pipeline-2.md). One epic for pipeline 2, with the stories below. The two spikes come first and may change the stories after them; any change goes back into the specification before it is built. One PR for pipeline 2, with a commit per story; the owner tests from the CLI and merges.

Decisions agreed with the owner:

- **Steps:** the plugin's `preflight`, `assess`, `map` and `extract-rules`, the counterpart of AWS Transform's assessment; not its planning or build steps.
- **Requirements:** generated by Reframe from Claude's results, in AWS Transform's shape, marked as such; the plugin's own outputs kept as written.
- **Sign-in:** the owner's Claude subscription by default, with Amazon Bedrock (the run's AWS profile, or a Bedrock API key from the environment) as the alternative.
- **Agent limit:** set before rule extraction.
- **Rule pairing:** by program, and an AI pass that is labelled as AI output.
- **Plugin usage counts:** off.
- **When it runs:** as its own command, and offered with the default answer yes in the guided run. It starts as soon as the run's Neptune graph exists, alongside AWS Transform (P2-15).

| Story | Title | Specification | Done when |
|---|---|---|---|
| P2-01 | Spike: the plugin headless on CardDemo | §2, §4 C0–C5 | The four steps run with `claude -p` on the subscription and through Bedrock. A note without identifiers records the files and formats written, where the plugin waits for a person headless, where the estimate is read, and the time, agents and tokens per step |
| P2-02 | Spike: the graph in Neptune | §4 C7 | A note records what AWS Transform writes into the graph, the load format, and a load and a query that reach the private cluster from Reframe, tried on a live run |
| P2-03 | Claude prerequisites and sign-in | §4 C0 | Claude Code, its sign-in (subscription or Bedrock), the pinned plugin and `python3` are checked and offered in the guided setup, installed only on a yes. The plugin's usage counts are off. A Bedrock API key is never written by Reframe |
| P2-04 | Workspace from the run's snapshot | §4 C1 | The snapshot's SHA-256 matches pipeline 1's (a mismatch stops the run). The copy is read-only, edits are denied in the settings, Reframe writes the intent, and no AWS Transform result is in the workspace |
| P2-05 | `reframe claude`: the plugin's steps in the worker | §3, §4 C2–C5 | Preflight answers are recorded as decisions or sent with `reframe answer`. Assess, map and rule extraction run with a receipt each. The agent limit is checked before extraction, and resume uses the plugin's run ID. The snapshot is unchanged at the end |
| P2-06 | Guided run continues into pipeline 2 | §3 | The guided run asks with the default answer yes, then asks the sign-in and the agent limit. The worker starts pipeline 2 when pipeline 1 is done. `status`, the home screen and its menu show both pipelines |
| P2-07 | Claude's graph in the same Neptune cluster | §4 C7, teardown | Claude's elements are loaded with source, run and SHA-256, and checked with a query. An existing cluster is loaded only on a yes. Teardown removes what Reframe added, by its records |
| P2-08 | Read Claude's outputs in full | §4 C5 | Preflight, assessment, map, rules and data objects are read into the report model, and the counts match the files on every live run |
| P2-09 | Requirements generated by Reframe from Claude's results | §4 C6 | Domain, function, capability and requirements in AWS Transform's shape. Each cites a rule card or a source line, or is dropped and counted. Each is labelled "generated by Reframe from Claude's results" and kept in its own file |
| P2-10 | Claude's business review report | §4 C8 | Same levels, look and review marks as pipeline 1, the rule cards as written, SME questions, technical detail, and links to the plugin's own pages. The export names the tool |
| P2-11 | Common model and pairing by program | §4 C8 | Domains are aligned by shared programs, with program coverage per tool and rules listed side by side per program, from both runs' files |
| P2-12 | AI pairing of rules, labelled | §4 C8 | A Claude step proposes rule pairs, each with a reason and a confidence, marked as AI output, which a person can mark right or wrong |
| P2-13 | The comparison page | §4 C8 | Overview first, then domains, programs, rules and what only one tool found. Every number links to the items in both reports, and the model-output caveat is stated |
| P2-14 | Live acceptance and README | §7 | Every point of the specification's section 7 passes from the CLI. The README describes pipeline 2 from the tested commands |
| P2-15 | Pipeline 2 alongside pipeline 1 | §3 Alongside pipeline 1 | Pipeline 2 starts when pipeline 1's AWS resources exist, in its own worker with its own lock; the two workers never overwrite each other's records; the comparison waits for AWS Transform's results; a stop in pipeline 1 stops pipeline 2 with the reason; a fresh guided CardDemo run with the default answers goes through both |

## Sharing the results

Specification: [sharing.md](sharing.md). An epic of its own, outside pipelines 1 and 2: the results of a run, shared with people who don't run Reframe.

| Story | Title | Specification | Done when |
|---|---|---|---|
| S-01 | `reframe publish`: a cleaned copy of a run's reports | §2 | AWS Transform's review, Claude's review, the comparison and the plugin's own pages are copied with an index page. Account IDs, ARNs, bucket names, S3 locations, job, workspace and connector IDs, profile names, e-mail addresses and local paths are removed. A check of the copy finds none left, or nothing is written |
| S-02 | The CardDemo reports in the repository | §3 | The reports of a full CardDemo run are in `reports/carddemo/`, linked from the README, and open in a browser from a clone with no Reframe run |
| S-03 | The reports in a private S3 bucket | §4 | One Terraform root creates a private, encrypted, versioned S3 bucket that refuses requests not over TLS. `reframe publish --to aws` uploads a cleaned copy under `<project>/<run>/`. The bucket outlives runs and is removed only by `reframe reports teardown`, by its own records |
| S-04 | Opening the reports with an AWS sign-in | §4 | `reframe reports` lists what is published and `reframe reports open` downloads a report with the reader's own AWS sign-in and opens it. Nothing can be read without access to the account (owner's decision, 23 November 2026). The CardDemo reports are uploaded and opened this way |
| S-05 | Spike: the AWS sign-in for a link | – | Done. IAM Identity Center belongs to the organization's management account, so a sign-in link needs its administrator to add a SAML application by hand |

S-06 to S-08, a link that opens a report in the browser after the AWS sign-in (CloudFront, Cognito federated with IAM Identity Center, and a sign-in check at the edge), were built and then dropped by the owner on 23 November 2026, before they were merged. Published reports stay in the private bucket and are opened or downloaded with each reader's own AWS sign-in. The AWS resources the link had created were removed by Reframe's records, and the three stories are closed as not planned.

In their place (owner's decision, 24 November 2026), the reports stay in the private bucket and anyone who can sign in to the account opens or downloads them by project name:

| Story | Title | Specification | Done when |
|---|---|---|---|
| S-09 | Find the reports bucket from any computer | §6 | `reframe reports` and `reframe reports open` work on a computer that did not publish: the bucket is found by its tags `ManagedBy=reframe` and `ReframeStore=reports` with the reader's own AWS sign-in. More than one such bucket is refused with the list. Teardown still goes only by the publisher's own records |
| S-10 | Open or download a report by project | §6 | `reframe reports open <project>` opens the project's latest published run and `--run <run>` an older one; `reframe reports download <project> --to <folder>` saves a copy to keep or send, with a warning for a macOS-protected folder |
| S-11 | One file to download from the S3 console | §6 | `reframe publish --to aws` also uploads `<project>/<run>/report.zip`, the same cleaned copy, so a reader without Reframe signs in to the AWS console and downloads one file. Republishing replaces it |

## The reconciliation (pipeline 3)

Specification: [pipeline-3.md](pipeline-3.md). It starts from the comparison of AWS Transform's and Claude's assessments, works on every rule except those both tools describe the same way, and decides each against the source code. It writes the recommended business rules, then the recommended requirements, and one SME question list. The person adds context: documents, review marks and answered questions (owner's decisions, 24 November 2026).

| Story | Title | Specification | Done when |
|---|---|---|---|
| P3-01 | Spike: one batch decided against the source | §4 R4 | On CardDemo, the decision prompt and schema run on three batches (a conflict, overlapping pairs, one-sided rules). The evidence check passes or catches each answer, and the time and usage per batch are measured and written into the specification |
| P3-02 | Preflight and inputs | §4 R0, R1 | `reframe reconcile` checks both pipelines, the complete pairing, the snapshot's SHA-256 and the sign-in, and runs on a torn-down run. The items, open questions and context are gathered: Markdown, text, PDF and Word documents; review exports, ignoring another snapshot's; answered questions |
| P3-03 | Identical pairs agreed | §4 R2 | `same` pairs become recommended rules without an agent, citing both tools; a *Needs change* or *Question* mark sends a pair to R4 |
| P3-04 | The estimate and limit | §3, §4 R3 | Items become groups (a Claude rule with its paired AWS rules) and one-sided rules, batched by program with small programs packed; the estimate is shown, and Reframe stops and asks above `--max-batches`; four batches run at once |
| P3-05 | Decisions with evidence | §4 R4 | Every batch's answer has a decision per unit, rules written as specified, and possible defects; evidence, coverage and keys are checked, asked once more, then the unit becomes `needs_sme`; decisions are stored with their input hash and round |
| P3-06 | Running again with more context | §4 R4 *Running again* | A second run with one more answered question decides only the affected rules again and keeps the rest |
| P3-07 | One SME list | §4 R5 | Both tools' open questions and R4's are merged, each original kept exactly once; answered ones are marked with their source; CSV and JSON out, answered files back in |
| P3-08 | Recommended requirements | §4 R6 | Requirements in AWS Transform's frame, each citing a recommended rule; uncited ones dropped and counted; open questions carried; marked as recommended by Reframe |
| P3-09 | The reconciliation page and publish | §4 R7 | The page as specified, linked from the comparison, with marks and downloads; `reframe publish` includes it as `reconciled/` with a fourth card |
| P3-10 | Live acceptance and README | §7 | Every point of section 7 passes from the CLI; the README and `docs/cli.md` describe pipeline 3 from the tested commands |

## The reconciliation, part 2: accuracy and the review loop (pipeline 3)

Agreed by the owner on 24 November 2026, after the reconciliation's live acceptance. On CardDemo it wrote 1,204 recommended rules from 1,111 input rules; 1,182 were rated high confidence and 636 carried a possible-defect note, and the reconciled review has 2,358 items to mark. Part 2 first measures how accurate the recommended rules are, then makes the review something an SME can finish and closes the loop from their marks back into the next round. Its specification is [pipeline-3.md](pipeline-3.md), section 8. P3-21 to P3-24, added on 25 November 2026, give each rule a kind and worked examples and each capability decision tables and a risk level; P3-25, added the same day, reads review downloads made on a published copy; P3-20, the live acceptance, comes last.

| Story | Title | Specification | Done when |
|---|---|---|---|
| P3-11 | Spike: accuracy audit on CardDemo | §8.1 | Done 25 November 2026: of 56 sampled rules 51 correct (91%), 3 with a wrong detail, 2 wrong; every rejection upheld; 25 of 27 defect notes real; every rule rated high |
| P3-12 | Confidence that means something | §8.3, §8.4, §8.5 | Reframe sets confidence from facts it checks: who decided the rule against the code, and whether the code is reached (with the entry lines); rules agreed by both tools are described by a step that reads their code. In the first round after part 2 the five audit items that were not correct are medium, low or rejected |
| P3-13 | Possible defects: once per unit, real or deliberate | §8.3, §8.8 | Each possible-defect note is shown once, on the unit it belongs to, saying whether the behaviour looks unintended or deliberate with the lines that show it, and the SME list's defect entries match one to one. On the audit's new sample the judges agree with the call |
| P3-14 | Review first | §8.10 | The reconciled review opens with a *Review first* view: code that never runs, conflicts, rewritten rules, units waiting for an SME, unintended defects, rules kept from one tool and other medium-confidence rules, in that order, high-risk capabilities and business rules first, each group with its count and progress, filtered by decision, kind, confidence and capability risk |
| P3-15 | Sign-off by function | §8.11 | A reviewer signs off a function in any review page; both downloads carry the sign-offs, the reconciliation reads them, and `reframe status` and the reconciled review show which functions are signed off, by whom and when |
| P3-16 | Marks on requirements and capabilities read | §8.11 | Capabilities can be marked. *Needs change* and *Question* marks on requirements and capabilities, with their comments, make the requirements step rewrite that function's requirements in the next round, given the previous text and the comment; no unit is decided again and no other function changes |
| P3-17 | Marks from the CSV download read | §8.11 | The CSV download has a `key` column and is read like the JSON download, sign-offs included; another snapshot's, or one without a `key` column, is ignored with the reason |
| P3-18 | Agree keeps a rule | §8.11 | A unit whose rules were all marked *Agree* keeps its decision in later rounds unless a later mark or a change to its input rules reopens it; agreed rules are given to a step that redoes their unit; an agreed requirement is kept word for word while its rules are unchanged; the page shows who agreed and when |
| P3-19 | Several reviewers' files | §8.11 | `reframe review --context <folder>` shows each reviewer's coverage, sign-offs and where reviewers disagree, and writes `reconcile/reviews.json`. The next round combines every reviewer's marks, *Needs change* and *Question* taking precedence over *Agree*, and keeps each comment with its reviewer's name |
| P3-21 | What kind of rule | §8.3, §8.6 | Every recommended rule has one of eight kinds (business, validation, error handling, display, technical, side effect, compliance, leftover), shown on the page with a filter; business, validation and compliance rules are listed first |
| P3-22 | Worked examples | §8.3, §8.7 | Every recommended rule has one to three worked examples (given → then) using the code's values and messages; every quoted value appears in a file the decision cites, or the example is removed and counted |
| P3-23 | Decision tables | §8.9 | Where three or more of a capability's rules share conditions, the capability has a decision table, each row citing its rule, with the combinations no rule covers listed as gaps for an SME |
| P3-24 | Risk per capability | §8.9 | Each capability names what it touches from a fixed list (money, card data, personal data, security, audit, record updates, reporting only); Reframe sets its risk from that (high for money, security, audit or a compliance rule), a function takes its highest capability's, and the review and sign-off order use it |
| P3-25 | Reviews of a published copy read | §8.11 | A mark or sign-off downloaded from a copy made by `reframe publish`, where each AWS Transform ID in a key is a stand-in, reaches the next round exactly as the same mark made on the run's own page; `reframe review` counts its coverage; this holds for AWS Transform's, Claude's and the reconciled review |
| P3-26 | What nobody marked is kept | §8.11 | A function written again keeps word for word, with their codes, the previous requirements no reviewer marked and whose cited rules are unchanged; only the marked requirements and capabilities and new or changed rules get new requirements |
| P3-27 | Removed examples counted on each rule | §8.7 | Each recommended rule carries the count of its own examples removed by the check, and the decision step must give every rule an example |
| P3-20 | Live acceptance of part 2 | §8.12 | On CardDemo, from the CLI: round 3 decides every unit again within its estimate; the audit on a new sample shows no fewer correct rules than 91%, no wrong rule rated high and the five P3-11 errors rated lower; two reviewers' files (JSON and CSV) go through `reframe review` and round 4, which redoes only what they touch; the README and `docs/cli.md` describe part 2 from the tested commands |

## Exports

Part of sharing the results: the requirements in the forms people work in. Specified in [sharing.md](sharing.md) before the stories start.

| Story | Title | Specification | Done when |
|---|---|---|---|
| S-12 | Requirements to a spreadsheet | [sharing §7](sharing.md) | `reframe export --to xlsx` writes one workbook from a run's reconciled results: domains, functions, capabilities, requirements, business rules, decision tables, open questions and the SME list as sheets, each requirement with the rules and source lines behind it and the reviewers' marks; every cell is text, cleaned as `reframe publish` cleans its copy |
| S-13 | Backlog items with acceptance criteria | [sharing §8](sharing.md) | #385 · Done 26 November 2026 (#462; the owner moved it forward from Later, the same day): `reframe export --to jira` writes an Epic per function and a Story per capability, and `--to azure-devops` an Epic per domain, a Feature per function and a User Story per capability, each tool's CSV import file, from the design's final specifications with the package's IDs and their acceptance criteria. No credentials; a person imports it. On CardDemo: 154 stories with 1,503 criteria |
| S-14 | Runs in the workspace, with inputs and outputs | [specification §7](specification.md), [how to run](how-to-run.md) | Done 26 November 2026 (the owner's request): every run is in `<workspace>/.reframe/<project>/<run>/`, the folder Reframe is run from, with `inputs/reconcile/` and `inputs/design/` (read when `--context` is not given) and `outputs/`; Reframe's tools and settings stay in `~/.reframe`; `.reframe/` is git-ignored. The existing runs were moved, not rerun, with symlinks left at the old paths. `reframe design` is a command, `reframe publish` includes the design and the package, and the README, the command reference and a one-page [How to run Reframe](how-to-run.md) describe it |
| S-15 | A published run's source, results and reports | [sharing §4](sharing.md) | Done 26 November 2026 (the owner's request): `reframe publish --to aws` uploads the run under `<project>/<run>/` in the standing Reframe bucket as `source/` (the snapshot and a note of its origin without local paths), `results/aws/` and `results/claude/` (the tools' own files) and `reports/` (the cleaned copy and `report.zip`). The owner chose the standing bucket over each run's source bucket, which teardown deletes. Reports published before keep opening |

## Final requirements, wireframes and the AI-DLC package (pipeline 4)

Agreed by the owner on 24 November 2026. From the reconciled and reviewed results, pipeline 4 designs the modernized application, not a lift and shift of the 3270 screens: every legacy screen is mapped one to one to what replaces it, and the new screens are an interactive, clickable prototype. It writes the final specifications and an AI-DLC package that Claude Code or Kiro loads for the forward deployment. People keep adding context after the first run (a design system, the target architecture, personas, SME answers, marks on the prototype, decisions), and each round redoes only the journeys, screens, specifications and package parts that context touches, with a summary of what changed. Model steps write structured results that cite requirements, rules and screens; Reframe checks the citations and draws every page and the prototype itself. Specification: [pipeline-4.md](pipeline-4.md), agreed by the owner on 25 November 2026 with their decisions (section 9).

| Story | Title | Specification | Done when |
|---|---|---|---|
| P4-01 | Specification and the owner's decisions | [pipeline-4.md](pipeline-4.md) | `docs/pipeline-4.md` is agreed, with the owner's decisions on: the target stack and where it comes from, which functions go final (signed off only, others marked draft), the default design system and the pipeline's name |
| P4-02 | Spike: the screen inventory | [§4 D1](pipeline-4.md) | Done 25 November 2026 ([spikes/p4-02-screens.md](spikes/p4-02-screens.md)): every BMS map in CardDemo (21) is read into its fields, positions, attributes and function keys, with no model, and each legacy screen is drawn as a faithful 3270 copy |
| P4-03 | How the screens connect | [§4 D2](pipeline-4.md) | Done 25 November 2026: the flow between screens and programs is read from the source and shown as a map, each link citing its lines; on CardDemo nothing is left open |
| P4-04 | User journeys | [§4 D3](pipeline-4.md) | Done 25 November 2026: screens are grouped into the tasks people do, each journey linked to its capabilities and requirements; on CardDemo 20 journeys over all 21 screens, every answer passing Reframe's checks |
| P4-05 | Modernized screens and their mapping | [§4 D4, §5](pipeline-4.md) | Done 25 November 2026 (#420), and proven in P4-10's live rounds: each journey has modern screen designs. A mapping table takes every legacy screen and field to its new component as kept, merged, dropped or new, with the reason; no legacy field is left unmapped. Each screen also records its role, data and service dependencies, the pain points it removes and test scenarios, and follows a `style-reference/` folder where the design system is silent |
| P4-06 | The clickable prototype | [§4 D5](pipeline-4.md) | Done 25 November 2026: one static HTML app with a route per screen, sample data shaped by the fields and messages written from the rules, each screen beside the legacy screens it replaces; screens and components can be marked *Agree*, *Needs change* or *Question*, journeys signed off, and the marks downloaded |
| P4-07 | Final specifications | [§4 D6, D8](pipeline-4.md) | Done 26 November 2026: per capability, the final requirements and business rules, screen specifications, data contracts from the copybooks, acceptance criteria, decision tables with their gaps, and the questions still open, each citing its sources; each rule and requirement classed as business rule, formatting, error handling, side effect or historical artifact |
| P4-08 | The AI-DLC package | [§4 D7](pipeline-4.md) | Done 26 November 2026, with placeholders and START-HERE.md for what the engineer gives (the stack, house conventions, skills); the *Sign in* build check is the engineer's step (owner's decision). One package in three forms: Kiro (`.kiro/specs/<capability>/` with `requirements.md`, `design.md` and `tasks.md`, and steering files), Claude Code (`CLAUDE.md`, the specifications, the prototype as the UI reference and the tasks) and Spec Kit (a constitution, `specs/<capability>/` and role files under `.claude/agents/`, each task naming its role), every item keeping its requirement and rule IDs. Checked by loading it into Claude Code and building one journey |
| P4-09 | Rounds with more context | [§4 D9](pipeline-4.md) | Done 26 November 2026 (`python -m reframe.design.pipeline`, with `--plan`): added context redoes only the parts it touches; each round versions the package and writes what changed since the last |
| P4-10 | Live acceptance and README | [§8](pipeline-4.md) | Done 26 November 2026 ([results](pipeline-4.md)): rounds 1 and 2 on CardDemo from the CLI; round 2's persona, design system and *Needs change* redid only what they reach, and a kept part now records its pieces so a later round can say why. On CardDemo, from the CLI: the prototype covers every screen, a second round with added context changes only what it should, and the README and `docs/cli.md` describe pipeline 4 from the tested commands |
| P4-11 | Golden datasets and equivalence tests | [§4 D10](pipeline-4.md) | #421 · Done 26 November 2026 (#456; the owner moved it forward from Later, the same day), with no model: per capability, a golden dataset from the worked examples and decision tables in each form of the package, with `golden/compare.py` setting formatting apart from business differences, and the test-generation task naming every case. On CardDemo: 1,595 cases for 139 capabilities, 746 edge cases and 82 combinations left for the owner. Expected outputs come from the rules until P4-13 |
| P4-12 | Risk and replacement order | [§4 D11](pipeline-4.md) | **Later** (the owner, 26 November 2026). Capabilities are ordered for replacement from their risk and what they touch, low-risk and read-only first and money, security and adjudication last, shared data and screens kept together; the order and its reasons are on the design page and in the package's constitution and tasks |
| P4-13 | Spike: real legacy outputs under GnuCOBOL (low priority) | [§4 D12](pipeline-4.md) | **Later** (the owner, 26 November 2026). A note records which CardDemo batch programs compile and run under GnuCOBOL with files built from the copybooks, and whether their outputs can replace the rules' expected outputs in the golden datasets |

## Release 1.0: anyone can try it

The owner's direction (26 November 2026): release v1.0.0 so anyone can try Reframe, with Claude only or with both tools. The specification, with the owner's decisions, is [docs/release-1.md](release-1.md); each story's *done when* is in its issue (epic #433).

| Story | Title | Specification | Issue |
|---|---|---|---|
| C1-01 | A run without AWS | [release 1.0](release-1.md) | #434 · Done 26 November 2026: `reframe run --tools claude`, the guided run's first question and `reframe setup --tools claude`; status, home, resume, report, compare and teardown say what applies |
| C1-02 | The Claude assessment alone | [release 1.0](release-1.md) | #435 · Done 26 November 2026: the Claude worker takes the snapshot; C7 keeps the graph as a file |
| C1-03 | Reconciliation from one tool | [release 1.0](release-1.md) | #436 · Done 26 November 2026: every rule checked against the code in Claude's frame; the two-tool prompts are unchanged |
| C1-04 | Design and publish on a Claude-only run | [release 1.0](release-1.md) | #437 · Done 26 November 2026: the design is unchanged; a published copy has no AWS review or comparison |
| C1-05 | Claude-only live on a second codebase | [release 1.0](release-1.md) | #438 · Done 26 November 2026 ([results](release-1.md)): the second test codebase from `reframe run --tools claude` to the package and a local published copy, about $71 at API prices; a usage limit followed by a task notice is now a limit stop |
| S-16 | Publish to GitHub Pages, optional | [release 1.0](release-1.md) | #439 · Done 26 November 2026: `reframe publish --to pages --repo <owner>/<name>`, after a typed yes |
| S-17 | The demo on GitHub Pages | [release 1.0](release-1.md) | #440 · Done 26 November 2026: `reports/index.html` and `.github/workflows/pages.yml`; Pages is turned on for the repository at the release |
| R1-01 | README for anyone | [release 1.0](release-1.md) | #441 · Done 26 November 2026: a 128-line README with badges, the demo and a quick start; the detail in `docs/guide.md` |
| R1-02 | Package metadata | [release 1.0](release-1.md) | #442 · Done 26 November 2026: 1.0.0 metadata; CI on Linux and macOS, Python 3.10 to 3.13, and a built-package check |
| R1-03 | Contributing | [release 1.0](release-1.md) | #443 · Done 26 November 2026: CONTRIBUTING.md, CHANGELOG.md, issue templates, SECURITY.md updated |
| R1-04 | A clean machine | [release 1.0](release-1.md) | #444 · Done 26 November 2026 ([results](release-1.md)): Linux container and macOS installs; a fresh macOS user account needs the Mac's administrator, so CI's clean macOS runners stand in |
| A1-01 | End-to-end runs | [release 1.0](release-1.md) | #445 · Done 27 November 2026 ([results](release-1.md)): the second test codebase with both tools and with Claude only. CardDemo with both tools was not run again for 1.0: its full run of 22 to 26 November stands, and AWS runs wait for the owner's own AWS account (the owner, 27 November 2026) |
| REL-1 | Release v1.0.0 | [release 1.0](release-1.md) | #446 · Released 27 November 2026, from CodeStrata/reframe |

## Credibility: code quality

The owner's direction (26 November 2026): make Reframe credible to someone reading its code. The checks run in CI on every pull request, with the checkers' versions pinned in `pyproject.toml`'s `dev` extra ([CONTRIBUTING](../CONTRIBUTING.md) says how to run them). Epic #449.

| Story | Title | Specification | Done when |
|---|---|---|---|
| Q1-01 | Honest status | – | #450 · Done 26 November 2026 (#455): `pyproject.toml` classes Reframe as Beta (`Development Status :: 4 - Beta`), not Production/Stable |
| Q1-02 | Lint in CI | [CONTRIBUTING](../CONTRIBUTING.md) | #451 · Done 26 November 2026 (#455): `ruff check src tests` passes in CI with pyflakes, syntax and bugbear rules. The 51 findings of the first check are fixed: unused imports and names, two dead loads in the design round, loop variables never read, and a test that accepted any exception |
| Q1-03 | Types in CI | [CONTRIBUTING](../CONTRIBUTING.md) | #452 · Done 26 November 2026 (#455): `mypy` passes in CI. The modules with type errors still to fix are listed in `pyproject.toml`, and the list only shrinks: 25 of 85 modules at the start, after 13 were fixed |
| Q1-04 | Tests of the drivers, and a coverage floor | [CONTRIBUTING](../CONTRIBUTING.md) | #453 · Done 26 November 2026 (#455): The Claude assessment's driver, the AWS Transform assessment's driver and the command handlers are tested stage by stage with fakes, and CI fails below 80% line coverage. At the start: 81% overall; the drivers went from 44%, 38% and 46% to 95%, 93% and 71% |
| Q1-05 | Split `core/commands.py` | – | #454 · **Next:** unblocked now that v1.0.0 is tagged (27 November 2026). The 900-line module becomes one module per command group, with the Q1-04 tests passing unchanged |
| Q1-06 | Types in every module | [CONTRIBUTING](../CONTRIBUTING.md) | #483 · Done 27 November 2026 (#484): `mypy` passes in CI with every module checked except `reframe.core.commands`, which comes off the list once Q1-05 has split it. The 24 other modules on the list (108 errors at the start) are fixed without changing what the code does, and the offline tests pass unchanged; any real fault the types turn up is fixed with a test and listed in the PR. Two were: preflight accepted a sign-in that named no account, and the reports store could return no bucket |

## Release 1.1: any legacy codebase

The owner's direction (27 November 2026): Reframe takes any legacy codebase Claude's plugin reads, not only mainframes. Outside the mainframe a run is Claude only, since AWS Transform writes requirements only for mainframe code. The specification is [docs/any-legacy.md](any-legacy.md), agreed when #464 merged. Epic #472.

| Story | Title | Specification | Done when |
|---|---|---|---|
| L1-01 | The stack profile | [§4](any-legacy.md) | #473 · `stack-profile.json` names the languages, family, screens, routes, jobs and data with the files that show them, for each test fixture; `--legacy` overrides it; status and the reports show it |
| L1-02 | Pipelines 1 and 2 by profile | [§5.1, 5.2](any-legacy.md) | #474 · AWS Transform is offered only for `mainframe`, and refused before anything is made otherwise; program names follow the profile |
| L1-03 | A reconciliation in the stack's words | [§5.3](any-legacy.md) | #475 · The prompts and source files come from the profile; the evidence check, the audit and the expert's pack work on a non-mainframe fixture |
| L1-04 | A design without screens | [§5.4](any-legacy.md) | #476 · A batch or service fixture gets its specifications, golden datasets, package and backlog, with journeys, screens and the prototype left out and the page saying why |
| L1-05 | Screens and flow from any stack | [§5.4](any-legacy.md) | #477 · The general reader lists each view's fields and actions and the routes and links between them, each citing lines and checked; patterns that settle a link need no model; what fails twice is unresolved |
| L1-06 | Data contracts from any stack | [§5.4](any-legacy.md) | #478 · SQL DDL is read with no model; entity classes by a checked model step; the target stack's types apply as today |
| L1-07 | "Runs as" for any stack | [§5.4](any-legacy.md) | #479 · Online, scheduled job, service or library from screens, routes and job definitions, for each fixture |
| L1-08 | Words, README and docs | [§5.5, 6](any-legacy.md) | #480 · The tagline, the README's table by family, the guide and every stage's words follow section 6 |
| L1-09 | Live acceptance on two codebases | [§7](any-legacy.md) | #481 · Section 7's *done when* on eShopModernizing and JPetStore 6, run locally, nothing committed, the results recorded here |

## Proving it at scale

After everything above is tested and the owner is satisfied.

| Story | Title | Specification | Done when |
|---|---|---|---|
| SC-01 | Spike: a larger codebase | [spike](spikes/sc-01-larger-codebase.md) | #398 · Spike written 26 November 2026 (#461), for the owner to choose: no public COBOL and CICS codebase several times CardDemo's size has a licence that allows it. The paths are CardDemo, GenApp and Bank-of-Z as one codebase (about 2.7 times CardDemo, permissive licences, read today), or NAV's DSF (MIT, about 20 times, a real national system) once Reframe reads PL/I. Estimates are in the spike |
| SC-02 | All pipelines on the larger codebase | to be written | **Later** (the owner, 26 November 2026). Every pipeline runs to the end on it, with the time, agents and cost of each recorded in the documentation |
| SC-03 | Amazon Bedrock, live | [pipeline 2 §4.1](pipeline-2.md) | **Later** (the owner, 26 November 2026). The Claude assessment, the reconciliation and pipeline 4 run live with Claude on Amazon Bedrock. Held until the owner's security clearance for using Bedrock on the codebase |
| SC-04 | Sign in with an Anthropic API key | [pipeline 2 §4.1](pipeline-2.md) | #457 · Done 26 November 2026 (#459), offline (the owner: build and test offline, a live run only on a yes): `--auth api-key` on `reframe run`, `claude`, `reconcile` and `design`, and in the guided run. `ANTHROPIC_API_KEY`, with `ANTHROPIC_BASE_URL` when set, goes to Claude Code from the shell and is never written; the preflight checks it is set; the estimate says it is billed to the key; `reframe publish` replaces a key it finds and names it |
| SC-05 | Amazon Bedrock on a Claude-only run | [pipeline 2 §4.1](pipeline-2.md) | #458 · Done 26 November 2026 (#459), offline: `reframe run --tools claude --auth bedrock --profile <p> --region <r>` uses the profile for Bedrock only and creates no AWS resources; `AWS_BEARER_TOKEN_BEDROCK` needs no profile. The reconciliation and the design now check the Bedrock sign-in against the run's profile (before, they checked it with no identity and always refused). Live runs wait for SC-03 |
| SC-06 | A mainframe expert's own check of an audit sample | [pipeline 3 §8.13](pipeline-3.md) | #460 · Built 26 November 2026 (#461), with no model: `python -m reframe.reconcile.expert pack` writes a blind pack of the audit's sample, cleaned, with `verdicts.csv`; `read` reports the expert's figures, their agreement with each judge and with the audit, and the rules they call wrong. On CardDemo the pack holds the audit's 76 items. **Live:** the expert's verdicts, for the owner to arrange |
