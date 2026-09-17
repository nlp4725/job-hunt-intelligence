# One image, three task definitions: the API (with a migrate container that
# must succeed first) and two scheduled workers. No Lambda, no SQS: the queue
# is the rescore_queue table and the workers are Fargate tasks on a schedule.

resource "aws_ecs_cluster" "main" {
  name = "jhi"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "tasks" {
  for_each          = toset(["api", "migrate", "rescore", "expertise"])
  name              = "/jhi/${each.key}"
  retention_in_days = 30
}

locals {
  db_env = [
    { name = "DB_HOST", value = aws_db_instance.main.address },
    { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
    { name = "DB_NAME", value = aws_db_instance.main.db_name },
    { name = "AWS_REGION", value = var.region },
  ]
  owner_secrets = [
    { name = "DB_OWNER_USER", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
    { name = "DB_OWNER_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
  ]
  login_secrets = [
    { name = "JHI_APP_DB_USER", valueFrom = "${aws_secretsmanager_secret.app_login.arn}:username::" },
    { name = "JHI_APP_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_login.arn}:password::" },
    { name = "JHI_ADMIN_DB_USER", valueFrom = "${aws_secretsmanager_secret.admin_login.arn}:username::" },
    { name = "JHI_ADMIN_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.admin_login.arn}:password::" },
  ]
  llm_secrets = [{ name = "DEEPSEEK_API_KEY", valueFrom = aws_secretsmanager_secret.deepseek.arn }]
  runtime     = { operating_system_family = "LINUX", cpu_architecture = "ARM64" }

  logging = { for name in ["api", "migrate", "rescore", "expertise"] : name => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.tasks[name].name
      awslogs-region        = var.region
      awslogs-stream-prefix = name
    }
  } }
}

# --- IAM -------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Pulls the image, writes logs and reads the secrets at task start.
resource "aws_iam_role" "execution" {
  name               = "jhi-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "jhi-read-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "secretsmanager:GetSecretValue"
      Resource = [aws_db_instance.main.master_user_secret[0].secret_arn, aws_secretsmanager_secret.app_login.arn,
        aws_secretsmanager_secret.admin_login.arn, aws_secretsmanager_secret.resume_key.arn,
      aws_secretsmanager_secret.deepseek.arn]
    }]
  })
}

# What the API itself may do: resume files only.
resource "aws_iam_role" "api" {
  name               = "jhi-api"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "api_resumes" {
  name = "jhi-resumes"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.resumes.arn}/*" },
      { Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.resumes.arn },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.resumes.arn },
    ]
  })
}

# Workers need no AWS API access beyond what the execution role provides.
resource "aws_iam_role" "worker" {
  name               = "jhi-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

# --- API ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "api" {
  family                   = "jhi-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api.arn
  runtime_platform {
    operating_system_family = local.runtime.operating_system_family
    cpu_architecture        = local.runtime.cpu_architecture
  }
  container_definitions = jsonencode([
    {
      # Runs Alembic to head and ensures the API logins; the API starts only if this succeeds.
      name             = "migrate"
      image            = local.image
      essential        = false
      command          = ["python", "-m", "cloud_api.deploy_tasks", "migrate"]
      environment      = concat(local.db_env, var.owner_email != "" ? [{ name = "JHI_OWNER_EMAIL", value = var.owner_email }] : [])
      secrets          = concat(local.owner_secrets, local.login_secrets)
      logConfiguration = local.logging["migrate"]
    },
    {
      name         = "api"
      image        = local.image
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      dependsOn    = [{ containerName = "migrate", condition = "SUCCESS" }]
      environment = concat(local.db_env, [
        { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.users.id },
        { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.web.id },
        { name = "RESUME_BUCKET", value = aws_s3_bucket.resumes.bucket },
        { name = "RESUME_KMS_KEY_ID", value = aws_kms_key.resumes.arn },
        { name = "CORS_ORIGINS", value = "https://${local.app_host}" },
      ])
      secrets = concat(local.login_secrets, local.llm_secrets,
      [{ name = "JHI_RESUME_KEY", valueFrom = aws_secretsmanager_secret.resume_key.arn }])
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = local.logging["api"]
    },
  ])
  depends_on = [aws_secretsmanager_secret_version.app_login, aws_secretsmanager_secret_version.admin_login,
  aws_secretsmanager_secret_version.resume_key, aws_secretsmanager_secret_version.deepseek_placeholder]
}

resource "aws_lb" "api" {
  name                       = "jhi-api"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  idle_timeout               = 180 # expertise drafts can take ~100 s
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name                 = "jhi-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.api.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_ecs_service" "api" {
  name                               = "jhi-api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.api_desired_count
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 120
  wait_for_steady_state              = true # a failed rollout fails the CI deploy
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}

# --- scheduled workers --------------------------------------------------------------

locals {
  workers = {
    rescore = {
      command  = ["python", "-m", "cloud_api.rescore_worker", "--once"]
      schedule = "rate(5 minutes)"
      secrets  = local.owner_secrets
    }
    expertise = {
      command  = ["python", "-m", "cloud_api.expertise_worker", "--once", "--limit", "50"]
      schedule = "rate(30 minutes)"
      secrets  = concat(local.owner_secrets, local.llm_secrets)
    }
  }
}

resource "aws_ecs_task_definition" "worker" {
  for_each                 = local.workers
  family                   = "jhi-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn
  runtime_platform {
    operating_system_family = local.runtime.operating_system_family
    cpu_architecture        = local.runtime.cpu_architecture
  }
  container_definitions = jsonencode([{
    name             = each.key
    image            = local.image
    essential        = true
    command          = each.value.command
    environment      = local.db_env
    secrets          = each.value.secrets
    logConfiguration = local.logging[each.key]
  }])
}

data "aws_iam_policy_document" "events_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "jhi-worker-scheduler"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "jhi-run-workers"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecs:RunTask", Resource = [for t in aws_ecs_task_definition.worker : t.arn_without_revision] },
      { Effect = "Allow", Action = "iam:PassRole", Resource = [aws_iam_role.execution.arn, aws_iam_role.worker.arn] },
    ]
  })
}

resource "aws_cloudwatch_event_rule" "worker" {
  for_each            = local.workers
  name                = "jhi-${each.key}"
  schedule_expression = each.value.schedule
}

resource "aws_cloudwatch_event_target" "worker" {
  for_each = local.workers
  rule     = aws_cloudwatch_event_rule.worker[each.key].name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.scheduler.arn
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.worker[each.key].arn_without_revision
    launch_type         = "FARGATE"
    task_count          = 1
    network_configuration {
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.tasks.id]
      assign_public_ip = true
    }
  }
}
