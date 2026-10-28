data "aws_region" "current" {}

# ---------- Alerts ----------

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "EventBridgeAndCloudWatchPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# ---------- State machine (SDD §4.10: ingest -> curate -> enrich) ----------

locals {
  lambda_retry = [{
    ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
    IntervalSeconds = 5
    MaxAttempts     = 3
    BackoffRate     = 2
  }]
  to_failure = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "NotifyFailure" }]

  definition = {
    Comment        = "Stellenradar daily run: ingest BA postings, SCD2-merge them into curated.postings, enrich new versions"
    StartAt        = "Ingest"
    TimeoutSeconds = 7200
    States = {
      Ingest = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.ingest_function_arn
          Payload = {
            "run_id.$"   = "$$.Execution.Name"
            "run_date.$" = "States.ArrayGetItem(States.StringSplit($$.Execution.StartTime, 'T'), 0)"
          }
        }
        ResultSelector = { "manifest.$" = "$.Payload" }
        ResultPath     = "$.ingest"
        TimeoutSeconds = 960
        Retry          = local.lambda_retry
        Catch          = local.to_failure
        Next           = "Curate"
      }
      Curate = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.curate_function_arn
          Payload = {
            "run_id.$"     = "$.ingest.manifest.run_id"
            "run_date.$"   = "$.ingest.manifest.run_date"
            "start_time.$" = "$$.Execution.StartTime"
            "complete.$"   = "$.ingest.manifest.complete"
          }
        }
        ResultSelector = { "checks.$" = "$.Payload" }
        ResultPath     = "$.curate"
        TimeoutSeconds = 660
        Retry          = local.lambda_retry
        Catch          = local.to_failure
        Next           = "Enrich"
      }
      Enrich = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.enrich_function_arn
          Payload = {
            "run_id.$"   = "$.ingest.manifest.run_id"
            "run_date.$" = "$.ingest.manifest.run_date"
          }
        }
        ResultSelector = { "stats.$" = "$.Payload" }
        ResultPath     = "$.enrich"
        TimeoutSeconds = 960
        Retry          = local.lambda_retry
        Catch          = local.to_failure
        Next           = "PublishSuccess"
      }
      PublishSuccess = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:cloudwatch:putMetricData"
        Parameters = {
          Namespace  = "Stellenradar"
          MetricData = [{ MetricName = "PipelineSucceeded", Value = 1, Unit = "Count" }]
        }
        ResultPath = null
        Retry      = [{ ErrorEquals = ["States.ALL"], IntervalSeconds = 5, MaxAttempts = 3, BackoffRate = 2 }]
        End        = true
      }
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn    = aws_sns_topic.alerts.arn
          Subject     = "Stellenradar daily run failed"
          "Message.$" = "States.Format('Execution {} failed. Error: {} - Cause: {}', $$.Execution.Name, $.error.Error, $.error.Cause)"
        }
        ResultPath = null
        Next       = "Failed"
      }
      Failed = {
        Type  = "Fail"
        Error = "PipelineFailed"
        Cause = "See the NotifyFailure input and the Lambda logs"
      }
    }
  }
}

data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn-daily"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid     = "InvokePipelineLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      var.ingest_function_arn, "${var.ingest_function_arn}:*",
      var.curate_function_arn, "${var.curate_function_arn}:*",
      var.enrich_function_arn, "${var.enrich_function_arn}:*",
    ]
  }
  statement {
    sid       = "Alert"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    sid       = "SuccessMetric"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData has no resource-level permissions; restricted by namespace below
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Stellenradar"]
    }
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "daily"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "daily" {
  name       = "${var.name_prefix}-daily"
  role_arn   = aws_iam_role.sfn.arn
  type       = "STANDARD"
  definition = jsonencode(local.definition)
}

# ---------- Schedule: 06:00 Europe/Berlin, DST-safe ----------

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.daily.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "start-daily"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name_prefix}-daily"
  description                  = "Starts the Stellenradar daily pipeline"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.daily.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})
  }
}

# ---------- Failures outside the Catch (timeouts, aborts) and missing runs ----------

resource "aws_cloudwatch_event_rule" "execution_failed" {
  name        = "${var.name_prefix}-daily-failed"
  description = "Daily state machine ended without success (covers timeouts and aborts the Catch cannot see)"
  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [aws_sfn_state_machine.daily.arn]
      status          = ["TIMED_OUT", "ABORTED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "execution_failed" {
  rule = aws_cloudwatch_event_rule.execution_failed.name
  arn  = aws_sns_topic.alerts.arn
}

resource "aws_cloudwatch_metric_alarm" "no_success" {
  alarm_name          = "${var.name_prefix}-no-successful-run"
  alarm_description   = "No successful Stellenradar run for ${var.freshness_alarm_hours} hours (NFR-01)"
  namespace           = "Stellenradar"
  metric_name         = "PipelineSucceeded"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = var.freshness_alarm_hours
  datapoints_to_alarm = var.freshness_alarm_hours
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
