# Generated passwords are ephemeral and written with write-only attributes, so
# they never land in Terraform state or plans. The DeepSeek key is set by hand
# once (README.md).

ephemeral "random_password" "app_login" {
  length  = 40
  special = false
}

ephemeral "random_password" "admin_login" {
  length  = 40
  special = false
}

ephemeral "random_password" "resume_key" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "app_login" {
  name_prefix             = "jhi/app-login-"
  description             = "Login in the jhi_app role (user requests)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_login" {
  secret_id                = aws_secretsmanager_secret.app_login.id
  secret_string_wo         = jsonencode({ username = "jhi_api", password = ephemeral.random_password.app_login.result })
  secret_string_wo_version = 1
}

resource "aws_secretsmanager_secret" "admin_login" {
  name_prefix             = "jhi/admin-login-"
  description             = "Login in the jhi_admin_api role (admin routes)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "admin_login" {
  secret_id                = aws_secretsmanager_secret.admin_login.id
  secret_string_wo         = jsonencode({ username = "jhi_admin", password = ephemeral.random_password.admin_login.result })
  secret_string_wo_version = 1
}

resource "aws_secretsmanager_secret" "resume_key" {
  name_prefix             = "jhi/resume-key-"
  description             = "Resume text encryption key (hashed into a Fernet key)"
  recovery_window_in_days = 30
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_secretsmanager_secret_version" "resume_key" {
  secret_id                = aws_secretsmanager_secret.resume_key.id
  secret_string_wo         = ephemeral.random_password.resume_key.result
  secret_string_wo_version = 1
}

resource "aws_secretsmanager_secret" "deepseek" {
  name_prefix             = "jhi/deepseek-api-key-"
  description             = "Set once by hand: aws secretsmanager put-secret-value (terraform/README.md)"
  recovery_window_in_days = 7
}

# A placeholder so tasks can start before the real key is set; the value you
# put later is never overwritten (the write-only version stays 1).
resource "aws_secretsmanager_secret_version" "deepseek_placeholder" {
  secret_id                = aws_secretsmanager_secret.deepseek.id
  secret_string_wo         = "unset"
  secret_string_wo_version = 1
}
