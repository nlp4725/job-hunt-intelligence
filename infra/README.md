# Cloud infrastructure (AWS CDK, Python)

Two stacks in `us-east-1`:

- **JhiGithubDeploy**: deployed once from your machine. Lets GitHub Actions deploy with no stored AWS keys.
- **JhiCloud**: everything else. Deployed by `.github/workflows/cloud.yml` on every push to `main` after the tests pass.

```
Internet ─► load balancer (HTTPS, api.<domain>) ─► API on Fargate ─► RDS Postgres (private, TLS only)
        ─► CloudFront (app.<domain>) ─► private S3 bucket (React build)
Scheduled Fargate tasks: rescore worker every 5 min, expertise worker every 30 min
Also: S3 resume bucket (KMS), Cognito, Secrets Manager, CloudWatch alarms, monthly budget
```

**Not used:** Lambda, SQS or NAT gateways. `tests/test_cloud_stack.py` fails if a Lambda, SQS queue, custom resource or NAT gateway appears in a template. Some CDK shortcuts create hidden Lambdas, so this code avoids them:

- `BucketDeployment`: the workflow runs `aws s3 sync` instead.
- `autoDeleteObjects`.
- the `log_retention=` shortcut: log groups are created explicitly with retention.
- `restrictDefaultSecurityGroup`.
- `OpenIdConnectProvider`: `OidcProviderNative` is used instead.
- RDS password rotation.

The queue is the `rescore_queue` table.

**Migrations** run in a short-lived container inside every API task, before the API container starts. This container runs `python -m cloud_api.deploy_tasks migrate`: Alembic to head as the database owner, then the two API logins. If the migration fails, the API doesn't start. The deploy's circuit breaker then rolls back to the previous version.

## One-time setup

You need an AWS account, a domain, the AWS CLI logged in as an administrator, Node 22 and Docker.

1. **Domain.** Buy one, ideally in Route 53 (Registered domains): that creates a hosted zone. Note its **hosted zone ID**. If the domain lives elsewhere, leave `hosted_zone_id` empty. On the first deploy you then add DNS records at your registrar:
   - the certificate validation CNAMEs, shown in the ACM console while the deploy waits
   - `api` pointing to the `AlbDnsName` output
   - `app` pointing to the `CloudFrontDomain` output
2. **Bootstrap CDK and create the GitHub deploy role** (from `infra/`):
   ```bash
   python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
   npx aws-cdk@2 bootstrap aws://<ACCOUNT_ID>/us-east-1
   npx aws-cdk@2 deploy JhiGithubDeploy -c domain=<domain>
   ```
3. **GitHub** (repository Settings):
   - Environments → create `production`. Optionally add yourself as a required reviewer, so each deploy waits for your click.
   - Variables → `AWS_ACCOUNT_ID`, `DOMAIN`, `HOSTED_ZONE_ID` (empty if not in Route 53), `ALERT_EMAIL`, `OWNER_EMAIL` (your sign-in email).
4. **Push to `main`** (or merge a PR). The workflow tests, builds the arm64 image and deploys `JhiCloud`. The first deploy takes ~20–30 min, mostly RDS and CloudFront. Confirm the SNS email subscription for alerts.
5. **DeepSeek key**, once. Until it is set, captures are saved without a seniority level, and expertise drafts fail.
   ```bash
   aws secretsmanager put-secret-value --secret-id <DeepSeekSecretArn output> --secret-string '<key>'
   aws ecs update-service --cluster <ClusterName output> --service <ApiServiceName output> --force-new-deployment
   ```
6. **Your admin account.** The migrate step creates `OWNER_EMAIL` as an admin with no sign-in attached. Sign up at the app with that email. Once Cognito has verified it, your first sign-in claims the admin account.

## Day to day

- **Check before merging:** `cd infra && .venv/bin/python -m pytest -q tests && npx aws-cdk@2 synth -c domain=<domain>`.
- **See what a deploy would change:** `npx aws-cdk@2 diff JhiCloud -c domain=<domain>`, with AWS credentials.
- **Logs:** CloudWatch log groups `JhiCloud-ApiLogs…`, `MigrateLogs`, `RescoreLogs`, `ExpertiseLogs` (kept 30 days).

## Cost at launch (approximate, us-east-1)

| Item | Monthly |
|---|---|
| RDS db.t4g.micro + 20 GB gp3 + backups | ~$15 |
| Load balancer | ~$17 |
| API task (0.5 vCPU, 1 GB, arm64, always on) | ~$15 |
| Public IPv4 addresses (load balancer + tasks) | ~$7–11 |
| Workers (short scheduled runs) | ~$1–3 |
| S3, CloudFront, KMS, Secrets Manager (5 secrets), CloudWatch | ~$5 |
| Cognito (under the free tier's monthly active users) | $0 |
| **Total** | **~$60–65** + DeepSeek |

The budget alarm (`monthly_budget_usd`, default 75) emails you at 80% of actual spend and at 100% of forecast. DeepSeek is billed outside AWS, so set a spend limit in the DeepSeek console.
