variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. stellenradar-dev."
}

variable "account_id" {
  type        = string
  description = "AWS account ID, appended to bucket names for global uniqueness."
}

variable "force_destroy" {
  type        = bool
  default     = false
  description = "Allow terraform destroy to delete non-empty buckets (dev only, ADR-008)."
}

variable "staging_retention_days" {
  type    = number
  default = 30
}

variable "athena_results_retention_days" {
  type    = number
  default = 7
}
