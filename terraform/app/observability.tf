# Observability (system design §1.5.15). The API, workers and Lambdas write
# CloudWatch Embedded Metric Format lines (cloud_api/observability.py); CloudWatch
# turns them into metrics in the JHI namespace. This file adds the alarms and
# one dashboard over those metrics, SQS and the load balancer.

locals {
  app_alarms = {
    rescore-dlq = {
      namespace   = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", statistic = "Maximum", period = 300,
      threshold   = 1, periods = 1, dimensions = { QueueName = aws_sqs_queue.rescore_dlq.name },
      description = "Rescore messages failed 5 times. Inspect jhi-rescore-dlq, fix, then redrive."
    }
    rescore-queue-age = {
      namespace   = "AWS/SQS", metric = "ApproximateAgeOfOldestMessage", statistic = "Maximum", period = 300,
      threshold   = 900, periods = 2, dimensions = { QueueName = aws_sqs_queue.rescore.name },
      description = "Rescore messages waiting over 15 min: the Lambda is failing or throttled."
    }
    rescore-publish-failed = {
      namespace   = "JHI", metric = "RescorePublishFailed", statistic = "Sum", period = 300,
      threshold   = 1, periods = 1, dimensions = {},
      description = "The API could not publish to SQS; reconciliation will catch up within the hour."
    }
    unscored-job-age = {
      namespace   = "JHI", metric = "UnscoredJobAgeMinutes", statistic = "Maximum", period = 3600,
      threshold   = 90, periods = 1, dimensions = {},
      description = "Reconciliation found jobs unscored for 90+ min: messages are being lost."
    }
    db-permission-denied = {
      namespace   = "JHI", metric = "DbPermissionDenied", statistic = "Sum", period = 300,
      threshold   = 1, periods = 1, dimensions = {},
      description = "A request touched a table its database role may not: possible isolation bug."
    }
    api-unhandled-errors = {
      namespace   = "JHI", metric = "ApiUnhandledError", statistic = "Sum", period = 300,
      threshold   = 3, periods = 1, dimensions = {},
      description = "Unhandled API errors; search /jhi/api for event=unhandled_error and the request_id."
    }
    resume-parse-failures = {
      namespace   = "JHI", metric = "ResumeParseFailed", statistic = "Sum", period = 3600,
      threshold   = 5, periods = 1, dimensions = {},
      description = "Five or more resumes failed to parse in an hour."
    }
    seniority-classify-failures = {
      namespace   = "JHI", metric = "SeniorityClassifyFailed", statistic = "Sum", period = 3600,
      threshold   = 10, periods = 1, dimensions = {},
      description = "DeepSeek classification failing: captures are saved without a level."
    }
    expertise-failures = {
      namespace   = "JHI", metric = "ExpertiseFailed", statistic = "Sum", period = 3600,
      threshold   = 10, periods = 1, dimensions = {},
      description = "Paid expertise scoring calls failing."
    }
    token-scope-denied = {
      namespace   = "JHI", metric = "TokenScopeDenied", statistic = "Sum", period = 3600,
      threshold   = 5, periods = 1, dimensions = {},
      description = "An API token keeps calling routes outside its scope: misconfigured or misused."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "app" {
  for_each            = local.app_alarms
  alarm_name          = "jhi-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  period              = each.value.period
  threshold           = each.value.threshold
  evaluation_periods  = each.value.periods
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_p95_latency" {
  alarm_name          = "jhi-api-p95-latency"
  alarm_description   = "API p95 above 500 ms for 15 minutes (target N3)."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = 0.5
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix }
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

locals {
  jhi_metric = { for name in ["CaptureOutcome", "RescorePublished", "RescorePublishFailed", "ScoresWritten", "RescoreMessageFailed",
    "ReconcileStaleUsers", "ReconcileMissingScores", "UnscoredJobAgeMinutes", "ResumeProcessed", "ResumeParseFailed",
    "SeniorityClassifyFailed", "SeniorityClassifyMs", "ExpertiseDraftMs", "ExpertiseDraftFailed", "ExpertiseScored",
  "ExpertiseFailed", "DbPermissionDenied", "TokenScopeDenied", "ApiUnhandledError"] : name => ["JHI", name] }

  widgets = [
    { title = "API latency (s)", metrics = [
      ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "p50" }],
      ["...", { stat = "p95" }],
    ] },
    { title = "API errors", metrics = [
      ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "Sum" }],
      concat(local.jhi_metric["ApiUnhandledError"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["DbPermissionDenied"], [{ stat = "Sum" }]),
    ] },
    { title = "Captures by outcome", metrics = [
      for outcome in ["scored", "saved", "blocked", "duplicate", "needs_track", "invalid"] :
      ["JHI", "CaptureOutcome", "Outcome", outcome, { stat = "Sum" }]
    ] },
    { title = "Rescore queue", metrics = [
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.rescore.name, { stat = "Maximum" }],
      ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.rescore.name, { stat = "Maximum", yAxis = "right" }],
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.rescore_dlq.name, { stat = "Maximum", label = "DLQ" }],
    ] },
    { title = "Scoring", metrics = [
      concat(local.jhi_metric["ScoresWritten"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["RescoreMessageFailed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["RescorePublishFailed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["UnscoredJobAgeMinutes"], [{ stat = "Maximum", yAxis = "right" }]),
    ] },
    { title = "Resumes and LLM", metrics = [
      concat(local.jhi_metric["ResumeProcessed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["ResumeParseFailed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["SeniorityClassifyFailed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["ExpertiseFailed"], [{ stat = "Sum" }]),
      concat(local.jhi_metric["SeniorityClassifyMs"], [{ stat = "p95", yAxis = "right" }]),
    ] },
    { title = "Database", metrics = [
      ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier, { stat = "Average" }],
      ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.main.identifier, { stat = "Maximum", yAxis = "right" }],
    ] },
    { title = "Lambda", metrics = [
      for key, fn in aws_lambda_function.this : ["AWS/Lambda", "Errors", "FunctionName", fn.function_name, { stat = "Sum" }]
    ] },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "jhi"
  dashboard_body = jsonencode({
    widgets = [for i, w in local.widgets : {
      type   = "metric"
      x      = (i % 2) * 12
      y      = floor(i / 2) * 6
      width  = 12
      height = 6
      properties = {
        title   = w.title
        region  = var.region
        view    = "timeSeries"
        stacked = false
        period  = 300
        metrics = w.metrics
      }
    }]
  })
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/jhi"
}
