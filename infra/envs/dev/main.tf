locals {
  name_prefix = "stellenradar-${var.env}"
}

module "monitoring" {
  source            = "../../modules/monitoring"
  name_prefix       = local.name_prefix
  alert_email       = var.alert_email
  monthly_limit_usd = var.monthly_budget_usd
}
data "aws_caller_identity" "current" {}

# The account already has a GitHub OIDC provider that other workloads may use, so it is looked up, not managed.
module "github_oidc" {
  source               = "../../modules/github_oidc"
  name_prefix          = local.name_prefix
  github_repo          = var.github_repo
  github_owner_id      = var.github_owner_id
  github_repo_id       = var.github_repo_id
  state_bucket         = "stellenradar-tfstate-${data.aws_caller_identity.current.account_id}"
  create_oidc_provider = false
}
