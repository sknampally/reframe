# Four-phase assessment and engineering preparation

The source upload defines assessment coverage. For this POC, S3 input is a ZIP
object, with a bounded local snapshot for independent source inspection. GitHub
and clean local Git sources remain supported. S3 object versions or ETags pin the
download; the local content-tree fingerprint is not presented as a source commit.

## 1. Assessment

The default AWS profile selects Analyze code, Analyze data, Generate technical
documentation and Extract business logic. Kickoff is included by AWS. Decomposition
editing and test-plan refinement are not included in this profile: proposed domain
boundaries and acceptance scenarios are prepared in phase 2 for SME review.

The controller supplies the recorded source location, retains current file
classifications, selects all eligible uploaded files for detailed documentation
and rule extraction, and acknowledges known report-only checkpoints. Final findings
remain unapproved. Unknown task contracts and execution failures stop with private
diagnostics rather than silently inventing a response.

```sh
reframe init --workspace /private/project --source s3://your-private-bucket/source.zip \
  --scope 'Assess the entire uploaded repository' --aws-profile your-profile --region us-east-1
reframe setup --attach-workspace YOUR_WORKSPACE --attach-connector YOUR_CONNECTOR \
  --connector-approved 'Assessment owner'
reframe assess --allow-new-assessment --wait
```

AWS access and connector activation must be configured before starting the job.
For Git sources the CLI uploads the pinned source; S3 sources are downloaded and
snapshotted before preparing the assessment upload. The default expanded-source
limit is 100 MB. ZIP traversal, symlinks, hidden metadata and encrypted members are
rejected. The owner reviews completed findings and provenance before:

```sh
reframe approve assessment --by 'Assessment owner' --note 'Reviewed findings and scope' --confirm-source-match
reframe resume
```

## 2. Prepare and challenge

The executor inspects source and AWS findings. A separate reviewer independently
inspects the evidence and challenges the candidate. The controller produces an HTML
report with rules, evidence anchors, proposed boundaries, acceptance scenarios,
open questions and the independent review. SME decisions regenerate the report.
Approval binds to the specification, decisions and exact report bytes.

```sh
reframe decide --question QUESTION_ID --by 'SME' --answer 'Recorded decision' --disposition resolved
reframe approve validation --by 'SME and architect owner' --note 'Reviewed report and decisions'
reframe resume
```

## 3. Business review

Generate linked prototype screens and structured API contracts from the approved
specification. Routes, request/response contracts, authentication assumptions and
business-rule references are review artifacts. They are not running services.
Business, architecture and technical-lead decisions belong in the approval record;
feedback invalidates downstream approvals and starts a revision.

```sh
reframe approve specifications --by 'Business review owner' --note 'Business, architect and technical lead review recorded'
reframe resume
```

## 4. Engineering handoff

The versioned ZIP includes specifications, decisions, traceability, acceptance
scenarios, API contracts, both HTML reports, a dependency-ordered backlog, section
prompts, proposed repository layout, AI-DLC metadata, AGENTS.md and SKILL.md.
Kiro, Claude Code, Codex and Cursor can consume the package. Actual implementation,
deployment and executed equivalence testing remain engineering work.

## Acceptance status

Offline regression tests exercise phase gates, source extraction and artifact
integrity. A fresh cloud run is still required to establish unattended live
completion for the default assessment profile. Local tests do not establish AWS
task compatibility, semantic completeness or runtime equivalence.
