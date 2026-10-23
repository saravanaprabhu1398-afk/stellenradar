output "budget_name" {
  value = module.monitoring.budget_name
}
output "gha_plan_role_arn" {
  value = module.github_oidc.plan_role_arn
}

output "gha_apply_role_arn" {
  value = module.github_oidc.apply_role_arn
}
