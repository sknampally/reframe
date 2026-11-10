#!/bin/sh
# Confirm the sandbox role cannot list a production bucket. The bucket name stays on this machine.
set -eu
bucket=${1:?Pass the production bucket name. Do not commit it.}
cd "$(dirname "$0")"
if [ -n "${SANDBOX_DATA_DIR:-}" ]; then
  export TF_DATA_DIR="$SANDBOX_DATA_DIR"
fi
role_arn=$("${TOFU:-tofu}" output -raw sandbox_role_arn)
assume_status=1
creds=
attempt=0
while [ "$attempt" -lt 6 ]; do
  attempt=$((attempt + 1))
  set +e
  creds=$(aws sts assume-role --role-arn "$role_arn" --role-session-name reframe-deny-check --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text 2>/dev/null)
  assume_status=$?
  set -e
  if [ "$assume_status" -eq 0 ] && [ -n "$creds" ]; then
    break
  fi
  creds=
  if [ "$attempt" -lt 6 ]; then
    echo "The new sandbox role is not ready to sign in yet. Waiting." >&2
    sleep 5
  fi
done
if [ "$assume_status" -ne 0 ] || [ -z "$creds" ]; then
  echo "The sandbox role could not be assumed for the deny check. Choose guided start again so the role trust can update." >&2
  exit 1
fi
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
