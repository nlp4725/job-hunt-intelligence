# Cloud infrastructure (Terraform)

```
Internet ─► load balancer (HTTPS, api.<domain>) ─► API on Fargate ─► RDS Postgres (private, TLS only)
        ─► CloudFront (app.<domain>) ─► private S3 bucket (React build)
Lambda (isolated subnets, IAM DB auth): jhi-rescore every 5 min, jhi-admin-task on demand
Scheduled Fargate task: expertise worker every 30 min
Also: S3 resume bucket (KMS), Cognito, Secrets Manager, CloudWatch alarms, monthly budget. Region us-east-1.
```

| Folder | What | Applied by |
|---|---|---|
| `account/` | The `job-hunt` member account in your AWS Organization, and a `JobHuntAdmin` SSO permission set assigned to you | You, once, from the management account with an admin SSO role (not root). Its state file stays local |
| `bootstrap/` | Terraform state bucket, `jhi-cloud` image registry, GitHub OIDC deploy role | You, once, from your machine. Its state file stays local |
| `app/` | Everything else | GitHub Actions on every push to `main` (`.github/workflows/cloud.yml`) |

**Lambda for database-only work; no SQS, no NAT gateway.** `tests/test_terraform.py` fails if SQS or NAT is added, or if a Lambda gets a route to the internet.
- **Queue:** the `rescore_queue` table, written in the same transaction as each capture.
- **Rescore worker:** the `jhi-rescore` Lambda, every 5 minutes, isolated subnets, IAM database authentication.
- **Admin tasks:** the `jhi-admin-task` Lambda (`status`, `import_sqlite`), invoked by hand.
- **Expertise worker:** a Fargate task on a 30-minute schedule (it calls DeepSeek, so it needs the internet).
- **Web app upload:** `aws s3 sync` in CI.
- **Database owner password:** RDS keeps it in Secrets Manager (`manage_master_user_password`).
- **Other passwords and the resume key:** generated as ephemeral values and written with write-only attributes, so they never appear in Terraform state or plans.

**Deploy order in CI:** tests → build the arm64 image, tagged with the commit → `terraform apply` with that tag. The API task runs `python -m cloud_api.deploy_tasks migrate` in a short-lived container first. The API container starts only if migrations succeed. `wait_for_steady_state` fails the job, and the circuit breaker rolls back, if the new version doesn't come up healthy.

## One-time setup

1. **A separate AWS account** (`terraform/account/`). Sign in to the management account's CLI with an admin SSO role, not root. Then:
   ```bash
   cd terraform/account
   cp example.tfvars account.tfvars          # your SSO user name and a new root email; gitignored
   AWS_PROFILE=<management admin> terraform init
   AWS_PROFILE=<management admin> terraform apply -var-file=account.tfvars
   aws configure sso --profile jhi-admin     # the new account, role JobHuntAdmin, us-east-1
   aws sso login --profile jhi-admin
   ```
   Keep this folder's `terraform.tfstate`. Turn on MFA for the management account's root user and put its credentials away.
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
   - Repository variables → `AWS_ACCOUNT_ID`, `DOMAIN`, `HOSTED_ZONE_ID` (empty if not in Route 53). Deploys stay off until `AWS_ACCOUNT_ID` and `DOMAIN` are both set.
   - `production` environment secrets → `ALERT_EMAIL`, `OWNER_EMAIL` (the email you will sign in with). Secrets rather than variables, so they stay out of the public repository's logs.
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
- **Logs:** CloudWatch `/jhi/api`, `/jhi/migrate`, `/jhi/expertise`, `/aws/lambda/jhi-rescore`, `/aws/lambda/jhi-admin-task` (kept 30 days).
- **Admin commands:**
  ```bash
  aws lambda invoke --function-name jhi-admin-task --payload '{"command":"status"}' --cli-binary-format raw-in-base64-out out.json && cat out.json
  ```
- **One-time data import** (after the first deploy, with an empty database):
  ```bash
  sqlite3 data/job_hunt.db ".backup /tmp/job_hunt_export.db"
  aws s3 cp /tmp/job_hunt_export.db "s3://$(terraform -chdir=terraform/app output -raw import_bucket)/imports/job_hunt.db"
  aws lambda invoke --function-name jhi-admin-task --cli-binary-format raw-in-base64-out \
    --payload '{"command":"import_sqlite","s3_key":"imports/job_hunt.db"}' out.json && cat out.json
  ```
  Your private tables (`resume`, `career_goals`, `chat_messages`) are left out, and the upload is deleted after the import.
- **Protected from `terraform destroy`:** the database, resume bucket, KMS key, resume key secret and user pool (`prevent_destroy`, plus deletion protection on RDS and Cognito).

## Cost at launch (approximate, us-east-1)

| Item | Monthly |
|---|---|
| RDS db.t4g.micro + 20 GB gp3 + backups | ~$15 |
| Load balancer | ~$17 |
| API task (0.5 vCPU, 1 GB, arm64, always on) | ~$15 |
| Public IPv4 addresses (load balancer + tasks) | ~$7–11 |
| Expertise worker (short scheduled Fargate runs) | ~$1–2 |
| Lambda (rescore every 5 min, admin tasks) | ~$0 (free tier) |
| S3, CloudFront, KMS, Secrets Manager, ECR, CloudWatch | ~$5–7 |
| Cognito (under the free tier's monthly active users) | $0 |
| **Total** | **~$60–70** + DeepSeek |

The budget (`monthly_budget_usd`, default 75) emails you at 80% of actual spend and at 100% of forecast. DeepSeek is billed outside AWS, so set a spend limit in its console.
