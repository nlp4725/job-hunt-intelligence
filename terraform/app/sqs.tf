# Rescoring (system design §1.5.9). The API publishes after each commit:
#   {"type": "job", "job_id": …}   a captured job, score it for every user
#   {"type": "user", "user_id": …} a profile or resume change, rescore that user's board
# The jhi-rescore Lambda consumes batches and reports per-message failures, so
# only failed messages retry; after 5 receives a message moves to the DLQ.
# An hourly {"type": "reconcile"} run catches anything a lost message missed.

resource "aws_sqs_queue" "rescore_dlq" {
  name                      = "jhi-rescore-dlq"
  message_retention_seconds = 1209600 # 14 days to inspect and redrive
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "rescore" {
  name                       = "jhi-rescore"
  visibility_timeout_seconds = 720    # ≥ 6 × the Lambda timeout (120 s), as AWS recommends
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20     # long polling
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.rescore_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "rescore_dlq" {
  queue_url = aws_sqs_queue.rescore_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.rescore.arn]
  })
}

# The Lambda service polls SQS for the function, so the function itself needs
# no network path to SQS and stays in the isolated subnets.
resource "aws_lambda_event_source_mapping" "rescore" {
  event_source_arn        = aws_sqs_queue.rescore.arn
  function_name           = aws_lambda_function.this["rescore"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 2 # caps database connections from scoring
  }
}

resource "aws_iam_role_policy" "rescore_consume" {
  name = "jhi-rescore-consume"
  role = aws_iam_role.lambda["rescore"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
      Resource = aws_sqs_queue.rescore.arn
    }]
  })
}

resource "aws_iam_role_policy" "api_publish_rescore" {
  name = "jhi-publish-rescore"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.rescore.arn
    }]
  })
}

# --- hourly reconciliation ------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "reconcile" {
  name                = "jhi-rescore-reconcile"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "reconcile" {
  rule  = aws_cloudwatch_event_rule.reconcile.name
  arn   = aws_lambda_function.this["rescore"].arn
  input = jsonencode({ type = "reconcile" })
}

resource "aws_lambda_permission" "reconcile_schedule" {
  statement_id  = "AllowHourlyReconcile"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["rescore"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconcile.arn
}
