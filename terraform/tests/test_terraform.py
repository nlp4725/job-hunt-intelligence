"""Checks the Terraform against the rules the design depends on, offline.

    cd terraform && .venv/bin/python -m pytest -q tests

`terraform fmt` and `terraform validate` also run here when terraform is
installed (always in CI).
"""

import re
import shutil
import subprocess
from pathlib import Path

import hcl2
import pytest

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ("account", "bootstrap", "app")


def _tf_files():
    return sorted(p for folder in FOLDERS for p in (ROOT / folder).glob("*.tf"))


def _resources(folder: str) -> dict[str, dict]:
    """{"aws_s3_bucket.resumes": {...attributes}}"""
    found = {}
    for path in sorted((ROOT / folder).glob("*.tf")):
        for block in hcl2.load(path.open()).get("resource", []):
            for kind, named in block.items():
                for name, body in named.items():
                    found[f"{kind}.{name}"] = body
    return found


@pytest.fixture(scope="module")
def app():
    return _resources("app")


@pytest.fixture(scope="module")
def bootstrap():
    return _resources("bootstrap")


def test_no_nat_gateway():
    pattern = re.compile(r'^\s*(?:resource|data)\s+"(aws_nat_gateway)"', re.M)
    offenders = [f"{p.name}: {m}" for p in _tf_files() for m in pattern.findall(p.read_text())]
    assert offenders == []


def test_lambdas_run_without_internet_access(app):
    """Lambda is for database-only work: isolated subnets, and a security group
    whose only ways out are Postgres and S3 (no 0.0.0.0/0)."""
    function = app["aws_lambda_function.this"]
    assert function["vpc_config"][0]["subnet_ids"] == "${aws_subnet.data[*].id}"
    assert function["vpc_config"][0]["security_group_ids"] == ["${aws_security_group.lambda.id}"]
    egress = {n: r for n, r in app.items() if n.startswith("aws_vpc_security_group_egress_rule.")
              and r["security_group_id"] == "${aws_security_group.lambda.id}"}
    assert set(egress) == {"aws_vpc_security_group_egress_rule.lambda_to_db", "aws_vpc_security_group_egress_rule.lambda_to_s3"}
    assert not [r for r in egress.values() if "cidr_ipv4" in r]
    assert app["aws_db_instance.main"]["iam_database_authentication_enabled"] is True


def test_lambdas_sign_in_to_their_own_login_only(app):
    policy = app["aws_iam_role_policy.lambda_db_connect"]["policy"]
    assert "rds-db:connect" in policy and "dbuser:${aws_db_instance.main.resource_id}/${each.value.db_login}" in policy


def test_database_is_private_encrypted_protected_and_tls_only(app):
    db = app["aws_db_instance.main"]
    assert db["publicly_accessible"] is False and db["storage_encrypted"] is True and db["deletion_protection"] is True
    assert db["manage_master_user_password"] is True and "password" not in db
    assert db["engine_version"].startswith("18") and db["backup_retention_period"] >= 7
    params = app["aws_db_parameter_group.main"]["parameter"]
    assert {"name": "rds.force_ssl", "value": "1"} in params


def test_database_subnets_have_no_internet_route(app):
    assert "route" not in app["aws_route_table.data"]
    assert app["aws_db_subnet_group.main"]["subnet_ids"] == "${aws_subnet.data[*].id}"


def test_only_the_load_balancer_is_open_to_the_internet(app):
    open_rules = {name: r for name, r in app.items()
                  if name.startswith("aws_vpc_security_group_ingress_rule.") and r.get("cidr_ipv4") == "0.0.0.0/0"}
    assert {(r["security_group_id"], r["from_port"]) for r in open_rules.values()} == {
        ("${aws_security_group.alb.id}", 443), ("${aws_security_group.alb.id}", 80)}
    assert app["aws_vpc_security_group_ingress_rule.db_from_tasks"]["referenced_security_group_id"] == "${aws_security_group.tasks.id}"


def test_every_bucket_blocks_public_access(app, bootstrap):
    for resources in (app, bootstrap):
        buckets = {n.split(".")[1] for n in resources if n.startswith("aws_s3_bucket.")}
        blocks = {n.split(".")[1]: r for n, r in resources.items() if n.startswith("aws_s3_bucket_public_access_block.")}
        assert buckets and buckets == set(blocks)
        for block in blocks.values():
            assert all(block[k] is True for k in ("block_public_acls", "block_public_policy", "ignore_public_acls",
                                                   "restrict_public_buckets"))


def test_resumes_are_kms_encrypted_and_kept(app):
    rule = app["aws_s3_bucket_server_side_encryption_configuration.resumes"]["rule"][0]
    assert rule["apply_server_side_encryption_by_default"][0]["sse_algorithm"] == "aws:kms"
    assert app["aws_s3_bucket.resumes"]["lifecycle"][0]["prevent_destroy"] is True


def test_generated_secrets_never_enter_state(app):
    text = (ROOT / "app" / "secrets.tf").read_text()
    assert not re.search(r'^\s*resource\s+"random_', text, re.M), "random values must be ephemeral"
    assert not re.search(r"^\s*secret_string\s*=", text, re.M), "use write-only secret_string_wo"


def test_passwords_reach_containers_only_as_secrets():
    text = (ROOT / "app" / "ecs.tf").read_text()
    for line in text.splitlines():
        if re.search(r'name\s*=\s*"[A-Z_]*(PASSWORD|_USER|_KEY)"', line):
            assert "valueFrom" in line, line


def test_api_migrates_first_and_only_migrate_gets_the_owner_login():
    text = (ROOT / "app" / "ecs.tf").read_text()
    api_block = text[text.index('resource "aws_ecs_task_definition" "api"'):text.index('resource "aws_lb" "api"')]
    migrate, api = api_block.split('name             = "migrate"')[1].split('name         = "api"')
    assert "essential        = false" in migrate and "local.owner_secrets" in migrate
    assert 'dependsOn    = [{ containerName = "migrate", condition = "SUCCESS" }]' in api
    assert "owner_secrets" not in api


def test_api_is_https_with_a_redirect_and_a_long_idle_timeout(app):
    assert app["aws_lb_listener.https"]["protocol"] == "HTTPS"
    assert app["aws_lb_listener.http_redirect"]["default_action"][0]["type"] == "redirect"
    assert app["aws_lb.api"]["idle_timeout"] >= 150


def test_workers_are_scheduled(app):
    assert app["aws_cloudwatch_event_target.worker"]["ecs_target"][0]["launch_type"] == "FARGATE"   # expertise: needs DeepSeek
    reconcile = app["aws_cloudwatch_event_target.reconcile"]
    assert reconcile["arn"] == '${aws_lambda_function.this["rescore"].arn}' and "reconcile" in reconcile["input"]


def test_rescore_queue_retries_dead_letters_and_is_encrypted(app):
    queue, dlq = app["aws_sqs_queue.rescore"], app["aws_sqs_queue.rescore_dlq"]
    assert queue["sqs_managed_sse_enabled"] is True and dlq["sqs_managed_sse_enabled"] is True
    assert "maxReceiveCount" in queue["redrive_policy"] and "rescore_dlq.arn" in queue["redrive_policy"]
    lambda_timeout = int(re.search(r"timeout\s*=\s*(\d+)\s*#", (ROOT / "app" / "lambda.tf").read_text()).group(1))
    assert queue["visibility_timeout_seconds"] >= 6 * lambda_timeout
    mapping = app["aws_lambda_event_source_mapping.rescore"]
    assert mapping["function_response_types"] == ["ReportBatchItemFailures"]
    assert mapping["scaling_config"][0]["maximum_concurrency"] <= 5


def test_observability_alarms_and_dashboard(app):
    alarms = (ROOT / "app" / "observability.tf").read_text()
    for alarm in ("rescore-dlq", "rescore-queue-age", "rescore-publish-failed", "unscored-job-age", "db-permission-denied",
                  "api-unhandled-errors", "resume-parse-failures", "seniority-classify-failures", "token-scope-denied"):
        assert f"{alarm} = {{" in alarms, alarm
    assert "aws_cloudwatch_dashboard.main" in app and "aws_cloudwatch_metric_alarm.api_p95_latency" in app
    assert app["aws_ecs_service.api"]["wait_for_steady_state"] is True


def test_every_log_group_has_retention(app):
    assert app["aws_cloudwatch_log_group.tasks"]["retention_in_days"] > 0


def test_the_account_is_protected_and_personal_details_stay_out_of_the_repo():
    account = _resources("account")["aws_organizations_account.job_hunt"]
    assert account["lifecycle"][0]["prevent_destroy"] is True and account["close_on_deletion"] is False
    text = (ROOT / "account" / "main.tf").read_text()
    assert "@" not in re.sub(r"#.*", "", text).replace("you+jobhunt@example.com", ""), "emails belong in a gitignored .tfvars"


def test_the_deploy_role_can_never_touch_resume_files(bootstrap):
    policy = bootstrap["aws_iam_role_policy.deploy"]["policy"]
    assert '"s3:*"' not in policy.replace(" ", "")
    assert "DenyResumeFileAccess" in policy and "jhi-resumes-*/*" in policy


def test_deploy_role_trusts_only_the_production_environment_of_one_repo(bootstrap):
    trust = bootstrap["aws_iam_role.deploy"]["assume_role_policy"]
    assert ":environment:production" in trust and "StringEquals" in trust
    assert bootstrap["aws_ecr_repository.cloud"]["image_tag_mutability"] == "IMMUTABLE"


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
@pytest.mark.parametrize("folder", FOLDERS)
def test_terraform_fmt_and_validate(folder, tmp_path):
    cwd = ROOT / folder
    subprocess.run(["terraform", "fmt", "-check", "-diff"], cwd=cwd, check=True, capture_output=True)
    env = {"TF_DATA_DIR": str(tmp_path / ".terraform"), "PATH": shutil.os.environ["PATH"],
           "HOME": shutil.os.environ.get("HOME", "")}
    subprocess.run(["terraform", "init", "-backend=false", "-input=false"], cwd=cwd, check=True, capture_output=True, env=env)
    result = subprocess.run(["terraform", "validate", "-no-color"], cwd=cwd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
