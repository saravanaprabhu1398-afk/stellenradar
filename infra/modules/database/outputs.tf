output "address" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "identifier" {
  value = aws_db_instance.this.identifier
}

output "arn" {
  value = aws_db_instance.this.arn
}

output "resource_id" {
  value       = aws_db_instance.this.resource_id
  description = "Used in rds-db:connect ARNs (ADR-007)."
}

output "master_user_secret_arn" {
  value = aws_db_instance.this.master_user_secret[0].secret_arn
}
