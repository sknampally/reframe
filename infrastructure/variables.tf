variable "aws_region" {
  description = "Region for the sandbox bucket and role."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile you configured locally. Leave null to use the ambient credentials."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Fallback prefix when guided setup does not pass a dated bucket and role name."
  type        = string
  default     = "reframe-sandbox"
}

variable "bucket_name" {
  description = "Bucket for one source. Empty keeps the fallback prefix plus a random suffix."
  type        = string
  default     = ""
  validation {
    condition = (
      var.bucket_name == "" ||
      can(regex("^reframe-[a-z0-9]+(-[a-z0-9]+)*-[0-9]{8}-[0-9a-f]{4}$", var.bucket_name)) ||
      can(regex("^reframe-[0-9]{8}-[0-9a-f]{8}$", var.bucket_name))
    )
    error_message = "Bucket name must be reframe, the source, the UTC date, and a short id."
  }
}

variable "role_name" {
  description = "Role for one source. Empty keeps the fallback prefix plus -role."
  type        = string
  default     = ""
  validation {
    condition = (
      var.role_name == "" ||
      can(regex("^reframe-[a-z0-9]+(-[a-z0-9]+)*-[0-9]{8}-[0-9a-f]{4}-role$", var.role_name)) ||
      can(regex("^reframe-[0-9]{8}-[0-9a-f]{8}-role$", var.role_name))
    )
    error_message = "Role name must be the shared sandbox name plus -role."
  }
}
