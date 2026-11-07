# Sandbox

This stack creates one private S3 bucket and one IAM role for fixture AWS Transform runs. The role can use the `reframe/` prefix in that bucket. It denies object and bucket reads everywhere else, including production mainframe data in the same account.

Terraform does not create the Transform workspace. After apply, create or attach that workspace with `reframe setup` and keep its id in the private run settings.

The CLI reads these settings and refuses to start a new AWS assessment when any of them is empty:

- `transform.workspace_id`
- `transform.upload_prefix`
- `transform.sandbox_role`

## Apply

Configure an AWS CLI profile in your terminal, then run:

```bash
export AWS_PROFILE=your-sandbox-profile
terraform -chdir=infrastructure init
terraform -chdir=infrastructure apply
```

Copy `sandbox_role` and `upload_prefix` into the private run settings. Use `sandbox_role_arn` only for connector activation and the deny check. Do not commit those values, and do not paste an account ID, role ARN, or bucket name into an issue.

## Deny check

Pass a production mainframe bucket you already use. The script must report that the read was denied. Then comment on story 40 that the production deny check passed.

```bash
infrastructure/deny-check.sh YOUR_PRODUCTION_BUCKET
```
