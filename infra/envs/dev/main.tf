locals {
  name_prefix = "stellenradar-${var.env}"
}

module "monitoring" {
  source            = "../../modules/monitoring"
  name_prefix       = local.name_prefix
  alert_email       = var.alert_email
  monthly_limit_usd = var.monthly_budget_usd
}