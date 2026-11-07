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
  description = "Short prefix used to build the role name and the bucket name. The bucket also gets a random suffix."
  type        = string
  default     = "reframe-sandbox"
}
