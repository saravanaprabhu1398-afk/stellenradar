variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "alert_email" {
  type        = string
  description = "Recipient for budget, anomaly and pipeline alerts. Set in terraform.tfvars (git-ignored)."
}

variable "monthly_budget_usd" {
  type    = number
  default = 30
}
variable "github_repo" {
  type    = string
  default = "saravanaprabhu1398-afk/stellenradar"
}
