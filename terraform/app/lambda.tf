# Two Lambda functions for database-only work (system design §1.6). Both run
# in the isolated subnets: no internet, no NAT. They reach Postgres (security
# group) and S3 (gateway endpoint) only, and sign in to Postgres with IAM
# database authentication, so they need no password and no Secrets Manager.
#
#   jhi-rescore     every 5 minutes: drains rescore_queue, scores new jobs for every user
#   jhi-admin-task  by hand: {"command": "status"} or {"command": "import_sqlite", "s3_key": "imports/..."}
#
# Same image as the API; awslambdaric is the entry point.

data "aws_ec2_managed_prefix_list" "s3" {
  name = "com.amazonaws.${var.region}.s3"
}

resource "aws_security_group" "lambda" {
  name        = "jhi-lambda"
  description = "Lambda functions: no inbound; outbound only to Postgres and S3"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_db" {
  security_group_id            = aws_security_group.lambda.id
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_s3" {
  security_group_id = aws_security_group.lambda.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.s3.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "db_from_lambda" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.lambda.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# --- import bucket (the one-time SQLite upload) ---------------------------------

resource "aws_s3_bucket" "imports" {
  bucket_prefix = "jhi-imports-"
}

resource "aws_s3_bucket_public_access_block" "imports" {
  bucket                  = aws_s3_bucket.imports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "imports" {
  bucket = aws_s3_bucket.imports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "imports" {
  bucket = aws_s3_bucket.imports.id
  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter {}
    expiration {
      days = 1 # the import deletes its file; this catches uploads never imported
    }
  }
}

# --- functions ---------------------------------------------------------------------

locals {
  lambdas = {
    rescore = {
      name     = "jhi-rescore"
      handler  = "cloud_api.lambda_handlers.rescore"
      db_login = "jhi_rescore"
      user_env = "JHI_RESCORE_DB_USER"
      timeout  = 300
      memory   = 1024
      extra    = {}
    }
    admin = {
      name     = "jhi-admin-task"
      handler  = "cloud_api.lambda_handlers.admin"
      db_login = "jhi_admin_task"
      user_env = "JHI_ADMIN_TASK_DB_USER"
      timeout  = 900
      memory   = 2048
      extra    = { IMPORT_BUCKET = aws_s3_bucket.imports.bucket }
    }
  }
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each           = local.lambdas
  name               = "jhi-lambda-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  for_each   = local.lambdas
  role       = aws_iam_role.lambda[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_db_connect" {
  for_each = local.lambdas
  name     = "jhi-db-connect"
  role     = aws_iam_role.lambda[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "rds-db:connect"
      Resource = "arn:aws:rds-db:${var.region}:${local.account_id}:dbuser:${aws_db_instance.main.resource_id}/${each.value.db_login}"
    }]
  })
}

resource "aws_iam_role_policy" "admin_imports" {
  name = "jhi-imports"
  role = aws_iam_role.lambda["admin"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:DeleteObject"]
      Resource = "${aws_s3_bucket.imports.arn}/imports/*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = local.lambdas
  name              = "/aws/lambda/${each.value.name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "this" {
  for_each      = local.lambdas
  function_name = each.value.name
  role          = aws_iam_role.lambda[each.key].arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["arm64"]
  timeout       = each.value.timeout
  memory_size   = each.value.memory

  image_config {
    entry_point = ["python", "-m", "awslambdaric"]
    command     = [each.value.handler]
  }

  ephemeral_storage {
    size = 2048 # the admin import downloads a ~130 MB SQLite file and snapshots it
  }

  vpc_config {
    subnet_ids         = aws_subnet.data[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge({
      DB_HOST               = aws_db_instance.main.address
      DB_PORT               = tostring(aws_db_instance.main.port)
      DB_NAME               = aws_db_instance.main.db_name
      (each.value.user_env) = each.value.db_login
    }, each.value.extra)
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[each.key].name
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_vpc, aws_cloudwatch_log_group.lambda]
}

# --- rescore schedule ----------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "rescore" {
  name                = "jhi-rescore"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "rescore" {
  rule = aws_cloudwatch_event_rule.rescore.name
  arn  = aws_lambda_function.this["rescore"].arn
}

resource "aws_lambda_permission" "rescore_schedule" {
  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["rescore"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.rescore.arn
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.lambdas
  alarm_name          = "${each.value.name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.this[each.key].function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

output "admin_task_function" {
  value = aws_lambda_function.this["admin"].function_name
}

output "import_bucket" {
  value = aws_s3_bucket.imports.bucket
}
