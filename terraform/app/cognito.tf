resource "aws_cognito_user_pool" "users" {
  name                     = "jhi-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 3
      max_length = 254
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cognito_user_pool_domain" "users" {
  domain       = var.cognito_domain_prefix != "" ? var.cognito_domain_prefix : "jhi-${split(".", var.domain)[0]}"
  user_pool_id = aws_cognito_user_pool.users.id
}

# The web app signs people in on its own pages: email and password go to this
# client over SRP (ALLOW_USER_SRP_AUTH below), which is why there is no need for
# ALLOW_USER_PASSWORD_AUTH — the password itself never crosses the wire. Sign-up,
# the emailed confirmation code and forgot-password are unauthenticated user-pool
# APIs and need no flow enabled at all. The OAuth settings stay because a
# federated provider (Google, Apple) can only be reached through
# /oauth2/authorize, and that comes back to /auth/callback.
resource "aws_cognito_user_pool_client" "web" {
  name                                 = "jhi-web"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = ["https://${local.app_host}/auth/callback", "http://localhost:5173/auth/callback"]
  logout_urls                          = ["https://${local.app_host}/", "http://localhost:5173/"]
  id_token_validity                    = 1
  access_token_validity                = 1
  refresh_token_validity               = 30
  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }
}
