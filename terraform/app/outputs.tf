output "api_url" {
  value = "https://${local.api_host}"
}

output "app_url" {
  value = "https://${local.app_host}"
}

output "alb_dns_name" {
  description = "without a Route 53 zone: CNAME api.<domain> to this"
  value       = aws_lb.api.dns_name
}

output "cloudfront_domain" {
  description = "without a Route 53 zone: CNAME app.<domain> to this"
  value       = aws_cloudfront_distribution.site.domain_name
}

output "certificate_validation_records" {
  description = "without a Route 53 zone: add these CNAMEs at your registrar"
  value = [for o in concat(tolist(aws_acm_certificate.api.domain_validation_options), tolist(aws_acm_certificate.app.domain_validation_options)) :
  { name = o.resource_record_name, type = o.resource_record_type, value = o.resource_record_value }]
}

output "frontend_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_domain" {
  value = "${aws_cognito_user_pool_domain.users.domain}.auth.${var.region}.amazoncognito.com"
}

output "deepseek_secret_arn" {
  value = aws_secretsmanager_secret.deepseek.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}
