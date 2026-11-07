output "sandbox_role" {
  description = "Value for the private transform.sandbox_role setting."
  value       = aws_iam_role.sandbox.name
}

output "sandbox_role_arn" {
  description = "Local activation value. Do not commit it or paste it into an issue."
  value       = aws_iam_role.sandbox.arn
}

output "upload_prefix" {
  description = "Value for the private transform.upload_prefix setting."
  value       = "s3://${aws_s3_bucket.sandbox.id}/${local.prefix}"
}
