# RDS PostgreSQL with pgvector for embeddings and the application tracker (SDD §5.3, ADR-002/007).
# Private, IAM authentication, TLS enforced, master password managed by RDS in Secrets Manager.

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.logical_replication" # DMS CDC from the tracker (ADR-009)
    value        = "1"
    apply_method = "pending-reboot"
  }

  # Caps WAL retained for an idle replication slot; beyond it the slot is invalidated and DMS redoes the full load
  # of the (tiny) tracker instead of the disk filling up.
  parameter {
    name  = "max_slot_wal_keep_size"
    value = "2048"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                             = "stellenradar"
  username                            = "stellenradar_admin"
  manage_master_user_password         = true
  iam_database_authentication_enabled = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  multi_az               = false
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period      = 1
  skip_final_snapshot          = true # dev: terraform destroy must remove everything (NFR-05)
  deletion_protection          = false
  auto_minor_version_upgrade   = true
  apply_immediately            = true
  copy_tags_to_snapshot        = true
  performance_insights_enabled = false
}

# ---------- Cost safety net: stop the instance every night ----------
# The daily pipeline starts and stops RDS around its run (SDD §4.10). AWS restarts a stopped instance by itself after
# seven days, so without this a forgotten instance would run (and bill) until someone notices.

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "stop" {
  name               = "${var.name_prefix}-db-nightly-stop"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "stop" {
  statement {
    actions   = ["rds:StopDBInstance"]
    resources = [aws_db_instance.this.arn]
  }
}

resource "aws_iam_role_policy" "stop" {
  name   = "stop-db"
  role   = aws_iam_role.stop.id
  policy = data.aws_iam_policy_document.stop.json
}

resource "aws_scheduler_schedule" "stop" {
  name                         = "${var.name_prefix}-db-nightly-stop"
  description                  = "Stops the Stellenradar database every night (cost safety net)"
  schedule_expression          = var.stop_schedule
  schedule_expression_timezone = "Europe/Berlin"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:rds:stopDBInstance"
    role_arn = aws_iam_role.stop.arn
    input    = jsonencode({ DbInstanceIdentifier = aws_db_instance.this.identifier })

    retry_policy {
      maximum_retry_attempts = 0 # an already stopped instance makes the call fail – nothing to retry
    }
  }
}
