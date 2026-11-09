output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = [for s in aws_subnet.private : s.id]
}

output "security_group_ids" {
  value       = { for k, sg in aws_security_group.this : k => sg.id }
  description = "rds, migrate, match, dms"
}
