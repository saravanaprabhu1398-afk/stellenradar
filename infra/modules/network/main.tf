# Private network for RDS and the in-VPC components (SDD §5.2, ADR-003): no internet gateway, no NAT, only an S3
# gateway endpoint. Lambdas that call other AWS APIs (Bedrock, Athena) stay outside the VPC.

data "aws_region" "current" {}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name_prefix }
}

resource "aws_subnet" "private" {
  for_each          = var.private_subnets
  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = "${data.aws_region.current.region}${each.key}"
  tags              = { Name = "${var.name_prefix}-private-${each.key}" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-private" }
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${var.name_prefix}-s3" }
}

# ---------- Security groups ----------

resource "aws_security_group" "this" {
  for_each    = toset(["rds", "migrate", "match", "dms"])
  name        = "${var.name_prefix}-${each.key}"
  description = "Stellenradar ${each.key}"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name_prefix}-${each.key}" }
}

locals {
  db_clients = toset(["migrate", "match", "dms"])
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_clients" {
  for_each                     = local.db_clients
  security_group_id            = aws_security_group.this["rds"].id
  referenced_security_group_id = aws_security_group.this[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from ${each.key}"
}

resource "aws_vpc_security_group_egress_rule" "clients_to_rds" {
  for_each                     = local.db_clients
  security_group_id            = aws_security_group.this[each.key].id
  referenced_security_group_id = aws_security_group.this["rds"].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL to RDS"
}

resource "aws_vpc_security_group_egress_rule" "clients_to_s3" {
  for_each          = local.db_clients
  security_group_id = aws_security_group.this[each.key].id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "S3 through the gateway endpoint"
}
