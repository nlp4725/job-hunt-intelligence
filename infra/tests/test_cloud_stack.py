"""Synthesizes both stacks offline and checks the rules the design depends on.

    cd infra && .venv/bin/python -m pytest -q
"""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from jhi_infra.cloud_stack import CloudStack
from jhi_infra.config import Config
from jhi_infra.github_stack import GithubDeployStack

CONTEXT = {"domain": "example.com", "alert_email": "alerts@example.com", "hosted_zone_id": "Z123",
           "github_repo": "owner/repo"}
ENV = cdk.Environment(account="123456789012", region="us-east-1")


def _templates(**context):
    app = cdk.App(context={**CONTEXT, **context})
    config = Config.from_context(app.node)
    cloud = CloudStack(app, "JhiCloud", config=config, env=ENV)
    github = GithubDeployStack(app, "JhiGithubDeploy", config=config, env=ENV)
    return Template.from_stack(cloud), Template.from_stack(github)


@pytest.fixture(scope="module")
def cloud():
    return _templates()[0]


@pytest.fixture(scope="module")
def github():
    return _templates()[1]


def _types(template):
    return {r["Type"] for r in template.to_json()["Resources"].values()}


def test_no_lambda_no_sqs_no_custom_resources(cloud, github):
    for template in (cloud, github):
        types = _types(template)
        assert not [t for t in types if t.startswith(("AWS::Lambda::", "AWS::SQS::", "Custom::", "AWS::CloudFormation::CustomResource"))]


def test_no_nat_gateway(cloud):
    assert "AWS::EC2::NatGateway" not in _types(cloud)


def test_database_is_private_encrypted_protected_and_tls_only(cloud):
    cloud.has_resource_properties("AWS::RDS::DBInstance", {
        "PubliclyAccessible": False, "StorageEncrypted": True, "DeletionProtection": True,
        "Engine": "postgres", "EngineVersion": "18.3", "BackupRetentionPeriod": 7})
    cloud.has_resource_properties("AWS::RDS::DBParameterGroup", {"Parameters": {"rds.force_ssl": "1"}})


def test_database_accepts_connections_only_from_the_tasks(cloud):
    ingress = cloud.find_resources("AWS::EC2::SecurityGroupIngress", {"Properties": {"FromPort": 5432}})
    assert len(ingress) == 1
    assert "SourceSecurityGroupId" in next(iter(ingress.values()))["Properties"]
    for group in cloud.find_resources("AWS::EC2::SecurityGroup").values():
        assert not [r for r in group["Properties"].get("SecurityGroupIngress", []) if r.get("CidrIp") == "0.0.0.0/0"
                    and r.get("FromPort") not in (80, 443)]


def test_buckets_block_public_access_and_resumes_use_kms(cloud):
    for bucket in cloud.find_resources("AWS::S3::Bucket").values():
        block = bucket["Properties"]["PublicAccessBlockConfiguration"]
        assert all(block[k] for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"))
    cloud.has_resource_properties("AWS::S3::Bucket", {"BucketEncryption": {"ServerSideEncryptionConfiguration": [
        Match.object_like({"ServerSideEncryptionByDefault": Match.object_like({"SSEAlgorithm": "aws:kms"})})]}})


def test_passwords_and_keys_come_from_secrets_manager_never_plain_env(cloud):
    for task in cloud.find_resources("AWS::ECS::TaskDefinition").values():
        for container in task["Properties"]["ContainerDefinitions"]:
            plain = {e["Name"] for e in container.get("Environment", [])}
            assert not [n for n in plain if n.endswith(("PASSWORD", "_USER", "_KEY")) or "SECRET" in n], container["Name"]


def test_api_runs_migrations_first_and_only_the_migrate_container_gets_the_owner_login(cloud):
    api_task = next(t for t in cloud.find_resources("AWS::ECS::TaskDefinition").values()
                    if any(c["Name"] == "api" for c in t["Properties"]["ContainerDefinitions"]))
    containers = {c["Name"]: c for c in api_task["Properties"]["ContainerDefinitions"]}
    assert containers["migrate"]["Essential"] is False
    assert containers["api"]["DependsOn"] == [{"ContainerName": "migrate", "Condition": "SUCCESS"}]
    assert "DB_OWNER_PASSWORD" not in {s["Name"] for s in containers["api"]["Secrets"]}
    assert "DB_OWNER_PASSWORD" in {s["Name"] for s in containers["migrate"]["Secrets"]}


def test_api_is_https_only_with_a_long_enough_idle_timeout(cloud):
    cloud.has_resource_properties("AWS::ElasticLoadBalancingV2::Listener", {"Port": 443, "Protocol": "HTTPS"})
    cloud.has_resource_properties("AWS::ElasticLoadBalancingV2::Listener", {"Port": 80, "DefaultActions": [
        Match.object_like({"Type": "redirect"})]})
    cloud.has_resource_properties("AWS::ElasticLoadBalancingV2::LoadBalancer", {"LoadBalancerAttributes": Match.array_with([
        {"Key": "idle_timeout.timeout_seconds", "Value": "180"}])})


def test_workers_are_scheduled_fargate_tasks(cloud):
    rules = cloud.find_resources("AWS::Events::Rule")
    schedules = sorted(r["Properties"]["ScheduleExpression"] for r in rules.values())
    assert schedules == ["rate(30 minutes)", "rate(5 minutes)"]
    for rule in rules.values():
        assert rule["Properties"]["Targets"][0]["EcsParameters"]["LaunchType"] == "FARGATE"


def test_every_log_group_has_retention(cloud):
    groups = cloud.find_resources("AWS::Logs::LogGroup")
    assert groups and all(g["Properties"].get("RetentionInDays") for g in groups.values())


def test_budget_and_alarms(cloud):
    cloud.resource_count_is("AWS::Budgets::Budget", 1)
    assert len(cloud.find_resources("AWS::CloudWatch::Alarm")) == 4


def test_github_role_trusts_only_this_repos_production_environment(github):
    github.has_resource_properties("AWS::IAM::Role", {"RoleName": "jhi-github-deploy", "AssumeRolePolicyDocument": {
        "Statement": [Match.object_like({"Condition": {"StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": "repo:owner/repo:environment:production"}}})]}})


def test_a_domain_is_required():
    app = cdk.App(context={**CONTEXT, "domain": ""})
    with pytest.raises(ValueError, match="domain"):
        Config.from_context(app.node)
