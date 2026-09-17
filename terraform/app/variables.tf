variable "region" {
  type    = string
  default = "us-east-1"
}

variable "domain" {
  type        = string
  description = "e.g. example.com: the API is served at api.<domain>, the web app at app.<domain> (HTTPS needs a domain)"
  validation {
    condition     = can(regex("^[a-z0-9-]+(\\.[a-z0-9-]+)+$", var.domain))
    error_message = "domain must be a domain name such as example.com"
  }
}

variable "hosted_zone_id" {
  type        = string
  default     = ""
  description = "Route 53 zone for the domain; empty = add the DNS records at your registrar by hand"
}

variable "image_tag" {
  type        = string
  description = "image tag in jhi-cloud to run (CI passes the git commit)"
}

variable "alert_email" {
  type      = string
  default   = ""
  sensitive = true # hidden in plan output: the repository's CI logs are public
}

variable "owner_email" {
  type        = string
  default     = ""
  sensitive   = true
  description = "made admin by the migrate step; claimed at the first verified sign-in with this email"
}

variable "monthly_budget_usd" {
  type    = number
  default = 75
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "cognito_domain_prefix" {
  type        = string
  default     = ""
  description = "sign-in pages at <prefix>.auth.<region>.amazoncognito.com; default jhi-<first label of domain>"
}
