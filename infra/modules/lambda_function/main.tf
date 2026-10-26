locals {
  # Python sources, data files and SQL of the selected packages; bytecode caches are never packaged.
  files = sort([
    for f in flatten([for p in var.packages : tolist(fileset(var.source_root, "${p}/**"))]) : f
    if can(regex("\\.(py|json|sql)$", f)) && !can(regex("__pycache__", f))
  ])
}

data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.root}/.build/${var.name}.zip"

  dynamic "source" {
    for_each = local.files
    content {
      content  = file("${var.source_root}/${source.value}")
      filename = source.value
    }
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role_policy" "logs" {
  name   = "logs"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.logs.json
}

resource "aws_iam_role_policy" "function" {
  name   = "function"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  description      = var.description
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = var.handler
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  memory_size      = var.memory_mb
  timeout          = var.timeout_s

  environment {
    variables = var.environment
  }

  logging_config {
    log_format = "Text" # the code already writes one JSON object per line
    log_group  = aws_cloudwatch_log_group.this.name
  }

  depends_on = [aws_iam_role_policy.logs]
}
