resource "aws_sns_topic" "alerts" {
  name = "jhi-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = nonsensitive(var.alert_email != "") ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  alarms = {
    api-5xx = {
      namespace  = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", statistic = "Sum", period = 300,
      threshold  = 5, periods = 1, operator = "GreaterThanOrEqualToThreshold",
      dimensions = { LoadBalancer = aws_lb.api.arn_suffix }
    }
    api-unhealthy = {
      namespace  = "AWS/ApplicationELB", metric = "UnHealthyHostCount", statistic = "Maximum", period = 60,
      threshold  = 1, periods = 5, operator = "GreaterThanOrEqualToThreshold",
      dimensions = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
    }
    db-cpu = {
      namespace  = "AWS/RDS", metric = "CPUUtilization", statistic = "Average", period = 300,
      threshold  = 80, periods = 3, operator = "GreaterThanThreshold",
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }
    }
    db-free-storage = {
      namespace  = "AWS/RDS", metric = "FreeStorageSpace", statistic = "Minimum", period = 300,
      threshold  = 2147483648, periods = 1, operator = "LessThanThreshold",
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "jhi-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  period              = each.value.period
  threshold           = each.value.threshold
  evaluation_periods  = each.value.periods
  comparison_operator = each.value.operator
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_budgets_budget" "monthly" {
  count        = nonsensitive(var.alert_email != "") ? 1 : 0
  name         = "jhi-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
