# Live AWS acceptance

**NOT AUTHORIZED / NOT EXECUTED**

This plan is for a later run in a designated sandbox account, with a budget the user states at that time. Nothing here was run. Do not treat it as approval to create or change cloud resources.

## Preconditions

- A sandbox AWS account and profile the user names at execution time. Do not reuse a production profile.
- Region taken from that profile. Do not assume a region from an example.
- SSO session fresh enough for the CLI the user already uses.
- An existing Reframe workspace whose saved bucket, connector, role, and workspace id the user has already approved.
- Source the user confirms is allowed in that sandbox.
- A stated ceiling for time and for AWS Transform spend. Stop when either is reached.

## Operations, in order

Read:

1. `reframe status` — local saved stage only.
2. `reframe register-project` — local identity. It must report the existing registration and `aws_jobs_created` 0.
3. Confirm the saved connector and workspace in the private project file. Do not print account ids, bucket names, or role ARNs into the repo.

Write, only after a yes on the existing upload prompt:

4. `reframe assess` for a **new run of the existing project**. This reuses the saved workspace, bucket, connector, and role, and asks before upload. It must create one new job and a new run prefix. It must not replace a previous job or object.
5. If Transform returns a documentation or business-rule task, submit only when the returned schema still requires the known fields. Business-rule scope must include `fileLevel`. Documentation detail must include `DETAILED`. Any other required field or enum is a stop. Do not guess a value.
6. `reframe resume` on that same run only. A second resume must not create another job.

New project, only with a separate yes:

7. `reframe init` in a new workspace. That path creates an isolated bucket, workspace, connector, and role through the existing setup prompts. No upload until the upload question is answered yes.

Not in this acceptance:

- No application deploy.
- No S3 layout migration.
- No change to an older object's key.
- No paid model call unless the user adds that as a separate approval.

## Stop and cleanup

- Stop on SSO expiry, a permission error, an unknown task schema, a missing connector, or a second job id.
- Do not delete the bucket, role, or previous assessment. Cleanup of the new sandbox job, if wanted, is a separate instruction.
- Keep receipts in the private workspace. The published report may contain only status names, run labels, and pass or fail.

## Success

- One new job id for the new run, and the previous job id unchanged.
- Uploaded source and assessment output recorded under the new run, with checksums in the private workspace.
- Human tasks left for a person when the product already pauses.
- Local `previews/business-1` and the existing package path unchanged unless the user later runs review and package on purpose.

Verified behavior after this plan is only what those commands return. Model prose and Transform findings stay evidence to review, not proof that a modernization is equivalent.
