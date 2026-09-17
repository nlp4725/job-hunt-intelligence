terraform {
  required_version = ">= 1.11"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
  # bucket, key and region come from -backend-config (README.md, CI workflow).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "job-hunt-intelligence", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  api_host   = "api.${var.domain}"
  app_host   = "app.${var.domain}"
  use_zone   = var.hosted_zone_id != ""
  image      = "${data.aws_ecr_repository.cloud.repository_url}:${var.image_tag}"
}

data "aws_ecr_repository" "cloud" {
  name = "jhi-cloud" # created by terraform/bootstrap
}
