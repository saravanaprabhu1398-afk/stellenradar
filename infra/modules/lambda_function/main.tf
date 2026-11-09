locals {
  # Python sources, data files, SQL, prompt templates and CA bundles of the selected packages; never bytecode caches.
  files = sort([
    for f in flatten([for p in var.packages : tolist(fileset(var.source_root, "${p}/**"))]) : f
    if can(regex("(\\.(py|json|sql|pem)|/prompts/.+\\.md)$", f)) && !can(regex("__pycache__", f))
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

# ENI management for functions inside the VPC (AWS managed policy, scoped by AWS to network interfaces).
resource "aws_iam_role_policy_attachment" "vpc" {
  count      = length(var.subnet_ids) > 0 ? 1 : 0
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
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
  layers           = var.layers

  environment {
    variables = var.environment
  }

  dynamic "vpc_config" {
    for_each = length(var.subnet_ids) > 0 ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }

  logging_config {
    log_format = "Text" # the code already writes one JSON object per line
    log_group  = aws_cloudwatch_log_group.this.name
  }

  depends_on = [aws_iam_role_policy.logs, aws_iam_role_policy_attachment.vpc]
}
