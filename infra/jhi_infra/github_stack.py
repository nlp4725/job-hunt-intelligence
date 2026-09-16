"""Deployed once from your machine: lets GitHub Actions deploy without stored
AWS keys. GitHub's OIDC token for a push to main of this repo can assume
jhi-github-deploy, which may only assume the CDK bootstrap roles (the
standard CDK deploy path) plus what the frontend upload needs (granted in
CloudStack)."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

from jhi_infra.config import DEPLOY_ROLE_NAME, Config

GITHUB_OIDC = "token.actions.githubusercontent.com"


class GithubDeployStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        # The native provider: iam.OpenIdConnectProvider would add a Lambda custom resource.
        provider = iam.OidcProviderNative(self, "GithubOidc", url=f"https://{GITHUB_OIDC}",
                                          client_ids=["sts.amazonaws.com"])
        role = iam.Role(
            self, "DeployRole", role_name=DEPLOY_ROLE_NAME, max_session_duration=cdk.Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(provider.oidc_provider_arn, conditions={
                "StringEquals": {f"{GITHUB_OIDC}:aud": "sts.amazonaws.com",
                                 f"{GITHUB_OIDC}:sub": f"repo:{config.github_repo}:environment:production"},
            }),
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-role-{self.account}-{self.region}"],
        ))
        cdk.CfnOutput(self, "DeployRoleArn", value=role.role_arn)
