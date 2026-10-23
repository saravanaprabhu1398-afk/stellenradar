output "budget_name" {
  value = module.monitoring.budget_name
}
output "gha_plan_role_arn" {
  value = module.github_oidc.plan_role_arn
}

output "gha_apply_role_arn" {
  value = module.github_oidc.apply_role_arn
}

output "bucket_names" {
  value = module.storage.bucket_names
}

output "glue_databases" {
  value = module.catalog.database_names
}

output "athena_workgroup" {
  value = module.catalog.athena_workgroup
}

output "ingest_function_name" {
  value = module.ingest.function_name
}
