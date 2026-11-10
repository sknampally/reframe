data "aws_caller_identity" "current" {}

data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  bucket_name = var.bucket_name != "" ? var.bucket_name : "${var.name_prefix}-${random_id.suffix.hex}"
  role_name   = var.role_name != "" ? var.role_name : "${var.name_prefix}-role"
  prefix      = "reframe"
}

resource "aws_s3_bucket" "sandbox" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "sandbox" {
  bucket                  = aws_s3_bucket.sandbox.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sandbox" {
  bucket = aws_s3_bucket.sandbox.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "sandbox" {
  bucket = aws_s3_bucket.sandbox.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "trust" {
  statement {
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:SetContext",
    ]
    principals {
      type        = "Service"
      identifiers = ["transform.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid     = "DenyCheck"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [data.aws_iam_session_context.current.issuer_arn]
    }
  }
}

data "aws_iam_policy_document" "sandbox" {
  statement {
    sid    = "ListSandbox"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.sandbox.arn]
  }

  statement {
    sid    = "UseSandbox"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
      "s3:PutObjectTagging",
    ]
    resources = ["${aws_s3_bucket.sandbox.arn}/*"]
  }

  statement {
    sid    = "DenyReadsOutsideSandbox"
    effect = "Deny"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
    ]
    not_resources = [
      aws_s3_bucket.sandbox.arn,
      "${aws_s3_bucket.sandbox.arn}/*",
    ]
  }
}

resource "aws_iam_role" "sandbox" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

resource "aws_iam_role_policy" "sandbox" {
  name   = "${local.role_name}-boundary"
  role   = aws_iam_role.sandbox.id
  policy = data.aws_iam_policy_document.sandbox.json
}
