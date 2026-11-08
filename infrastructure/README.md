# Sandbox

This stack creates one private S3 bucket and one IAM role for fixture AWS Transform runs. The role can use the `reframe/` prefix in that bucket. It denies object and bucket reads everywhere else, including production mainframe data in the same account.

OpenTofu does not create the Transform workspace. Guided start does that after apply. The `.tf` files are the shared configuration language OpenTofu runs.

The CLI reads these settings and refuses to start a new AWS assessment when any of them is empty:

- `transform.workspace_id`
- `transform.upload_prefix`
- `transform.sandbox_role`

## Apply

Guided start runs this apply when the sandbox settings are empty. To run it yourself, configure an AWS CLI profile in your terminal, then run:

```bash
export AWS_PROFILE=your-sandbox-profile
tofu -chdir=infrastructure init
tofu -chdir=infrastructure apply
```

Copy `sandbox_role` and `upload_prefix` into the private run settings. Use `sandbox_role_arn` only for connector activation and the deny check. Do not commit those values, and do not paste an account ID, role ARN, or bucket name into an issue.

## Deny check

Guided start runs this check. It assumes the sandbox role and lists a bucket name it generates. That bucket is not created. The check continues only when AWS denies the list. You do not choose a bucket. Then comment on story 40 that the production deny check passed.
