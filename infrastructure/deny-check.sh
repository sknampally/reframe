#!/bin/sh
# Confirm the sandbox role cannot list a production bucket. The bucket name stays on this machine.
set -eu
bucket=${1:?Pass the production bucket name. Do not commit it.}
cd "$(dirname "$0")"
role_arn=$("${TOFU:-tofu}" output -raw sandbox_role_arn)
creds=$(aws sts assume-role --role-arn "$role_arn" --role-session-name reframe-deny-check --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
access_key=$(printf '%s' "$creds" | awk '{print $1}')
secret_key=$(printf '%s' "$creds" | awk '{print $2}')
session_token=$(printf '%s' "$creds" | awk '{print $3}')
set +e
output=$(AWS_ACCESS_KEY_ID="$access_key" AWS_SECRET_ACCESS_KEY="$secret_key" AWS_SESSION_TOKEN="$session_token" \
  aws s3 ls "s3://${bucket}" 2>&1)
status=$?
set -e
unset access_key secret_key session_token creds
printf '%s\n' "$output" | grep -q 'AccessDenied' && exit 0
echo "Sandbox role was not denied. Refusing to treat this role as isolated." >&2
exit "${status:-1}"
