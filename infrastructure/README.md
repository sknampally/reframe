# Sandbox

Guided start applies this stack once per source. The bucket, workspace, and connector share one name: `reframe-`, the repository name, the UTC date, and a short id. The role name adds `-role`. OpenTofu state stays in that source's private assessment folder. The role can read and write objects anywhere in its bucket. Analysis output is not limited to the `reframe/` upload prefix. It denies object and bucket reads everywhere else, including production mainframe data in the same account.

OpenTofu does not create the Transform workspace. Guided start does that after apply. The `.tf` files are the shared configuration language OpenTofu runs. Apply writes `.terraform.lock.hcl` beside this stack. That file stays on the machine that applied it, so the framework checkout stays clean for workspace initialization.

The CLI reads these settings and refuses to start a new AWS assessment when any of them is empty:

- `transform.workspace_id`
- `transform.upload_prefix`
- `transform.sandbox_role`

## Apply

Guided start runs this apply for a new source. To run it yourself, configure an AWS CLI profile in your terminal, then run:

```bash
export AWS_PROFILE=your-sandbox-profile
tofu -chdir=infrastructure init
tofu -chdir=infrastructure apply
```

Copy `sandbox_role` and `upload_prefix` into the private run settings. Use `sandbox_role_arn` only for connector activation and the deny check. Do not commit those values, and do not paste an account ID, role ARN, or bucket name into an issue.

## Deny check

Guided start runs this check. It creates an empty temporary bucket, lists that bucket with the sandbox role, and deletes the temporary bucket. The check continues only when AWS denies the list. You do not choose a bucket. Then comment on story 40 that the production deny check passed.
