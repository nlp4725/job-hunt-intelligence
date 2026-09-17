# Cloud infrastructure (Terraform)

```
Internet ─► load balancer (HTTPS, api.<domain>) ─► API on Fargate ─► RDS Postgres (private, TLS only)
        ─► CloudFront (app.<domain>) ─► private S3 bucket (React build)
Scheduled Fargate tasks: rescore worker every 5 min, expertise worker every 30 min
Also: S3 resume bucket (KMS), Cognito, Secrets Manager, CloudWatch alarms, monthly budget. Region us-east-1.
```

| Folder | What | Applied by |
|---|---|---|
| `bootstrap/` | Terraform state bucket, `jhi-cloud` image registry, GitHub OIDC deploy role | You, once, from your machine. Its state file stays local |
| `app/` | Everything else | GitHub Actions on every push to `main` (`.github/workflows/cloud.yml`) |

**Not used: Lambda, SQS or NAT gateways.** `tests/test_terraform.py` fails if one is added.
- **Queue:** the `rescore_queue` table.
- **Workers:** Fargate tasks started by EventBridge schedules.
- **Web app upload:** `aws s3 sync` in CI.
- **Database owner password:** RDS keeps it in Secrets Manager (`manage_master_user_password`).
- **Other passwords and the resume key:** generated as ephemeral values and written with write-only attributes, so they never appear in Terraform state or plans.

**Deploy order in CI:** tests → build the arm64 image, tagged with the commit → `terraform apply` with that tag. The API task runs `python -m cloud_api.deploy_tasks migrate` in a short-lived container first. The API container starts only if migrations succeed. `wait_for_steady_state` fails the job, and the circuit breaker rolls back, if the new version doesn't come up healthy.

## One-time setup

1. **AWS access for yourself.** Don't use the root user. In the AWS console, enable IAM Identity Center and create a user with `AdministratorAccess`, then run `aws configure sso` → profile `jhi-admin`, and `aws sso login --profile jhi-admin`. Turn on MFA for root and put its credentials away.
2. **Domain.** Buy one, ideally in Route 53 (Registered domains). That creates a hosted zone; note its ID. If the domain lives elsewhere, leave `HOSTED_ZONE_ID` empty. During the first deploy you then add, at your registrar:
   - the certificate validation CNAMEs (ACM console, or the `certificate_validation_records` output)
   - `api` → `alb_dns_name`
   - `app` → `cloudfront_domain`
3. **Bootstrap:**
   ```bash
   cd terraform/bootstrap
   AWS_PROFILE=jhi-admin terraform init
   AWS_PROFILE=jhi-admin terraform apply
   ```
   Keep `terraform.tfstate` from this folder somewhere safe; it is gitignored.
4. **GitHub** (repository Settings):
   - Environments → create `production`. Optionally add yourself as a required reviewer, so each deploy waits for your approval.
   - Variables → `AWS_ACCOUNT_ID`, `DOMAIN`, `HOSTED_ZONE_ID` (empty if not in Route 53), `ALERT_EMAIL`, `OWNER_EMAIL` (the email you will sign in with).
5. **Push to `main`.** The first deploy takes ~20–30 min, mostly RDS, certificates and CloudFront. Then confirm the SNS email subscription.
6. **DeepSeek key**, once. It starts as a placeholder, so captures are saved without a seniority level and expertise drafts fail. Terraform never overwrites the value you set.
   ```bash
   aws secretsmanager put-secret-value --secret-id "$(terraform output -raw deepseek_secret_arn)" --secret-string '<key>'
   aws ecs update-service --cluster jhi --service jhi-api --force-new-deployment
   ```
7. **Your admin account.** The migrate step creates `OWNER_EMAIL` as an admin with no sign-in attached. Sign up at the app with that email. Once Cognito has verified it, your first sign-in claims the admin account.

## Day to day

- **Tests:**
  ```bash
  cd terraform
  python3.13 -m venv .venv && .venv/bin/pip install -r requirements-test.txt
  .venv/bin/python -m pytest -q tests
  ```
- **Plan against real AWS** (read-only):
  ```bash
  cd terraform/app
  AWS_PROFILE=jhi-admin terraform init -backend-config="bucket=jhi-tfstate-<ACCOUNT_ID>" -backend-config="key=app/terraform.tfstate" -backend-config="region=us-east-1"
  AWS_PROFILE=jhi-admin terraform plan -var domain=<domain> -var image_tag=<a pushed commit>
  ```
- **Logs:** CloudWatch `/jhi/api`, `/jhi/migrate`, `/jhi/rescore`, `/jhi/expertise` (kept 30 days).
- **Protected from `terraform destroy`:** the database, resume bucket, KMS key, resume key secret and user pool (`prevent_destroy`, plus deletion protection on RDS and Cognito).

## Cost at launch (approximate, us-east-1)

| Item | Monthly |
|---|---|
| RDS db.t4g.micro + 20 GB gp3 + backups | ~$15 |
| Load balancer | ~$17 |
| API task (0.5 vCPU, 1 GB, arm64, always on) | ~$15 |
| Public IPv4 addresses (load balancer + tasks) | ~$7–11 |
| Workers (short scheduled runs) | ~$1–3 |
| S3, CloudFront, KMS, Secrets Manager, ECR, CloudWatch | ~$5–7 |
| Cognito (under the free tier's monthly active users) | $0 |
| **Total** | **~$60–70** + DeepSeek |

The budget (`monthly_budget_usd`, default 75) emails you at 80% of actual spend and at 100% of forecast. DeepSeek is billed outside AWS, so set a spend limit in its console.
