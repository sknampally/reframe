# Live assessment readiness

Reframe must turn observed recovery steps into reusable CLI behavior. An operator's
successful recovery is evidence of an integration issue, not evidence that the CLI
has passed an unattended assessment. This checklist records that distinction.

## Recovery coverage

| Observed issue | Framework behavior | Verification boundary |
| --- | --- | --- |
| MCP dependency or CPU architecture mismatch | Fail during setup with a managed Python reinstall instruction; save private diagnostics | Local tests; workstation repair still requires installing a compatible interpreter |
| Expired AWS credentials | Guided init offers AWS SSO login and rechecks access | Browser sign-in is a user action; unattended session renewal is not guaranteed |
| Mainframe connector rejected generic S3 configuration | Setup uses the mainframe S3 connector type and bucket ARN configuration | Observed successful live connector creation; not a universal connector contract |
| Connector confirmation supplied while AWS still reports PENDING | Setup queries AWS and requires ACTIVE in the selected account; an explicitly supplied role must match | Offline readiness regression tests; administrator activation remains required |
| Job creation requires an orchestrator omitted by the advertised schema | Discover the unique service-returned mainframe orchestrator before uploading; ambiguity blocks creation | Live discovery observed; offline discovery/ambiguity tests |
| Job exists but no assessment plan starts | Recognized onboarding prompts trigger a complete assessment-only capability selection using the pinned source, scope and connector | Offline message and recovery tests; fresh CLI-only live acceptance pending |
| AWS asks to repeat capability selection | Send the full named capability set with existing job/source context; maximum two distinct onboarding responses | Bounded attempts, private intent/receipt and uncertain-send reconciliation tests |
| Kickoff asks for the already uploaded source location | Submit only the recorded ZIP URI to the recognized source-location task after checking its identity, schema and local archive checksum | Offline submission/resume tests; no change to optional chat/features or other task types |
| Non-blocking collaborator invitation remains open | Leave the invitation untouched and continue assessment polling | Offline regression test; blocking approvals still pause |
| Job reports EXECUTING while a task is pending | Handle recognized routine checkpoints under the saved full-upload policy; pause on unknown decisions | Schema and upload-manifest checks; final findings approval remains separate |
| Tool reports failure inside a successful transport envelope | Save the raw tool response privately before returning an error | Local diagnostics; raw responses are never public evidence |

Plan selection sends no source-derived instructions to the job agent: the message
is constructed from the user's saved scope and pinned run configuration. Unknown
chat prompts do not authorize additional actions. Existing plans are not recreated.
An interrupted message with no successful receipt requires reconciliation; resume
does not blindly resend it or create a new job.

## Assessment transport and scope

The mainframe adapter uses the AWS Transform MCP server. The separately documented
`atx` CLI serves AWS Transform custom and continuous modernization; equivalent
specialized mainframe job control has not been verified. It is not a required
installation or a replacement for this adapter. See the [AWS CLI getting-started
guide](https://docs.aws.amazon.com/transform/latest/userguide/custom-get-started.html).

The default assessment requests code analysis, data analysis, technical documentation
and business-rule extraction. It excludes decomposition editing and test-plan
refinement. Proposed application boundaries and test specifications belong to the
subsequent preparation phase and its SME/architect review.

## Administrator boundary

The CLI can create the selected private bucket, workspace and connector after
authorization. An administrator must provide or activate the connector's IAM role.
For scripted activation, use `reframe setup --role-arn <approved-role-arn>` and
`--connector-approved <administrator-name>` for the selected private workspace.
The CLI verifies AWS readiness rather than trusting the administrator's name alone.
It does not manufacture a trust policy by copying another customer's role.

Creating a dedicated IAM role automatically is **not implemented**. The observed
manual role creation is not part of unattended CLI acceptance. Any future role
provisioning must use documented trust requirements, a reviewable bucket-scoped
policy and explicit authorization. Administrator access alone does not establish
the service trust relationship.

## Release acceptance still required

- Verify kickoff source configuration and every assessment capability through
  documented CLI paths. Any required console task must be identified and explained.
- Preserve explicit human decisions for scope changes and generated deliverables;
  distinguish those decisions from mechanical source configuration.
- Complete a new CardDemo assessment from a clean pinned checkout using only the
  installed CLI, with no operator-issued MCP calls or private state edits.
- Repeat on a second mainframe repository using configuration changes only.
- Exercise interruption/resume and compare two assessments of the same source
  revision before claiming repeatability.

Until these checks pass, describe live assessment automation as a POC under
validation. Passing local tests does not establish live end-to-end acceptance,
support for every mainframe dialect, or behavioral equivalence.

## CardDemo live evidence — repaired run

One full-upload CardDemo run reached `AWAITING_ASSESSMENT_APPROVAL` through Reframe.
Kickoff and all four assessment capabilities reported `SUCCEEDED`. AWS's overall
job remained `EXECUTING` with a non-blocking collaborator invitation, so the CLI
recorded completion on the exact successful plan rather than claiming AWS closed
the job. It downloaded 224 original files from 26 output folders and sealed 545
text/provenance files. Original PDFs and ZIPs remain private; 202 binary files or
archive members were not text-extracted. A private review index provides links to
all original and extracted evidence. No assessment approval or agent validation
was performed.

This run required controller fixes, audited CLI upgrades, renewed browser SSO,
and an explicit expansion-limit increase. It is evidence of repaired CLI operation,
not uninterrupted acceptance from one release. Export retries currently enumerate
and download again rather than reuse completed downloads. The second-repository,
repeat-run comparison and live agent-phase acceptance checks remain open.
