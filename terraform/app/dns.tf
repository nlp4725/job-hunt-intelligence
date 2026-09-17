# Certificates for api.<domain> (load balancer) and app.<domain> (CloudFront,
# which needs us-east-1: the region this runs in). With a Route 53 zone the
# validation and alias records are created here; without one, add them at your
# registrar while the first apply waits (README.md).

resource "aws_acm_certificate" "api" {
  domain_name       = local.api_host
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "app" {
  domain_name       = local.app_host
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.use_zone ? {
    for option in concat(tolist(aws_acm_certificate.api.domain_validation_options), tolist(aws_acm_certificate.app.domain_validation_options)) :
    option.domain_name => option
  } : {}
  zone_id         = var.hosted_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = local.use_zone ? [aws_route53_record.cert_validation[local.api_host].fqdn] : null
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = local.use_zone ? [aws_route53_record.cert_validation[local.app_host].fqdn] : null
}

resource "aws_route53_record" "api" {
  count   = local.use_zone ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = local.api_host
  type    = "A"
  alias {
    name                   = aws_lb.api.dns_name
    zone_id                = aws_lb.api.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "app" {
  count   = local.use_zone ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = local.app_host
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}
