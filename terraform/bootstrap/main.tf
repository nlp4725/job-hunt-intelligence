# One-time setup, applied from your machine with admin credentials (README.md).
# Creates what the CI deploy needs before it can run: the Terraform state
# bucket, the image registry, and the GitHub OIDC role. State for this folder
# stays local (terraform.tfstate, gitignored): keep the file.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "job-hunt-intelligence", managed_by = "terraform-bootstrap" }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  type        = string
  description = "owner/name of the repository allowed to deploy"
  default     = "nlp4725/job-hunt-intelligence"
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  github     = "token.actions.githubusercontent.com"
}

# --- Terraform state ---------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = "jhi-tfstate-${local.account_id}"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# --- Image registry ------------------------------------------------------------

resource "aws_ecr_repository" "cloud" {
  name                 = "jhi-cloud"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "cloud" {
  repository = aws_ecr_repository.cloud.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

# --- GitHub Actions deploy role ------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.github}"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "deploy" {
  name                 = "jhi-github-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.github}:aud" = "sts.amazonaws.com"
          "${local.github}:sub" = "repo:${var.github_repo}:environment:production"
        }
      }
    }]
  })
}

# What `terraform apply` in terraform/app needs, scoped to this project's names
# where the service allows it (jhi-* roles, buckets, secrets and the registry).
resource "aws_iam_role_policy" "deploy" {
  name = "jhi-deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StateAndFrontendBuckets"
        Effect   = "Allow"
        Action   = "s3:*"
        Resource = ["arn:aws:s3:::jhi-*", "arn:aws:s3:::jhi-*/*"]
      },
      {
        Sid    = "ImageRegistry"
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
          "ecr:DescribeImages", "ecr:DescribeRepositories", "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart", "ecr:ListTagsForResource"]
        Resource = aws_ecr_repository.cloud.arn
      },
      {
        Sid      = "RegistryLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "sts:GetCallerIdentity"]
        Resource = "*"
      },
      {
        Sid    = "ProjectServices"
        Effect = "Allow"
        Action = ["ec2:*", "ecs:*", "elasticloadbalancing:*", "rds:*", "kms:*", "cognito-idp:*", "cloudfront:*",
          "acm:*", "route53:*", "logs:*", "cloudwatch:*", "sns:*", "events:*", "budgets:*",
        "s3:ListAllMyBuckets", "s3:CreateBucket", "secretsmanager:GetRandomPassword", "secretsmanager:ListSecrets"]
        Resource = "*"
      },
      {
        Sid    = "ProjectSecrets"
        Effect = "Allow"
        Action = "secretsmanager:*"
        Resource = ["arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:jhi/*",
        "arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:rds!*"]
      },
      {
        Sid      = "ProjectRoles"
        Effect   = "Allow"
        Action   = "iam:*"
        Resource = ["arn:aws:iam::${local.account_id}:role/jhi-*", "arn:aws:iam::${local.account_id}:policy/jhi-*"]
      },
      {
        Sid      = "ServiceLinkedRoles"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole", "iam:GetRole"]
        Resource = "arn:aws:iam::${local.account_id}:role/aws-service-role/*"
      },
    ]
  })
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.cloud.repository_url
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
