output "database_names" {
  value = { for k, db in aws_glue_catalog_database.layer : k => db.name }
}

output "athena_workgroup" {
  value = aws_athena_workgroup.this.name
}
