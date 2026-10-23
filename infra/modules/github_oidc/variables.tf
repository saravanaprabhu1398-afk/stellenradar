variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. stellenradar-dev."
}

variable "github_repo" {
  type        = string
  description = "Repository allowed to assume the roles, as owner/name."
}

variable "github_environment" {
  type        = string
  default     = "dev"
  description = "GitHub environment whose jobs may assume the apply role."
}

variable "state_bucket" {
  type        = string
  description = "Terraform state bucket name."
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "false = use the GitHub OIDC provider that already exists in the account (looked up, never modified or deleted)."
}
