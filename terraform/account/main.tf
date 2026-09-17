# Step 0, applied once from the AWS Organizations management account with an
# admin SSO role (never the root user): creates the job-hunt member account and
# gives your IAM Identity Center user admin access to it. Everything else
# (bootstrap/, app/) then runs inside that account. State stays local
# (terraform.tfstate, gitignored): keep the file.
#
#   cp example.tfvars account.tfvars   # fill in; *.tfvars is gitignored
#   AWS_PROFILE=<management admin profile> terraform init
#   AWS_PROFILE=<management admin profile> terraform plan  -var-file=account.tfvars
#   AWS_PROFILE=<management admin profile> terraform apply -var-file=account.tfvars

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "job-hunt-intelligence", managed_by = "terraform-account" }
  }
}

variable "account_email" {
  type        = string
  description = "root email for the new account; must not belong to any other AWS account (e.g. you+jobhunt@example.com)"
}

variable "sso_user_name" {
  type        = string
  description = "your IAM Identity Center user name (often your email)"
}

variable "account_name" {
  type    = string
  default = "job-hunt"
}

# --- the account -------------------------------------------------------------

data "aws_organizations_organization" "current" {}

resource "aws_organizations_account" "job_hunt" {
  name              = var.account_name
  email             = var.account_email
  parent_id         = data.aws_organizations_organization.current.roots[0].id
  close_on_deletion = false
  lifecycle {
    prevent_destroy = true
    # AWS doesn't return these after creation; changing them would replace the account.
    ignore_changes = [role_name, iam_user_access_to_billing]
  }
}

# --- your admin access -----------------------------------------------------------

data "aws_ssoadmin_instances" "this" {}

locals {
  sso_instance_arn  = tolist(data.aws_ssoadmin_instances.this.arns)[0]
  identity_store_id = tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]
}

data "aws_identitystore_user" "me" {
  identity_store_id = local.identity_store_id
  alternate_identifier {
    unique_attribute {
      attribute_path  = "UserName"
      attribute_value = var.sso_user_name
    }
  }
}

resource "aws_ssoadmin_permission_set" "job_hunt_admin" {
  name             = "JobHuntAdmin"
  description      = "Administrator on the job-hunt account"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT4H"
}

resource "aws_ssoadmin_managed_policy_attachment" "job_hunt_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.job_hunt_admin.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_ssoadmin_account_assignment" "me" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.job_hunt_admin.arn
  principal_type     = "USER"
  principal_id       = data.aws_identitystore_user.me.user_id
  target_type        = "AWS_ACCOUNT"
  target_id          = aws_organizations_account.job_hunt.id
  depends_on         = [aws_ssoadmin_managed_policy_attachment.job_hunt_admin]
}

output "account_id" {
  value = aws_organizations_account.job_hunt.id
}

output "next_step" {
  value = "aws configure sso --profile jhi-admin  (account ${aws_organizations_account.job_hunt.id}, role JobHuntAdmin, region us-east-1)"
}
