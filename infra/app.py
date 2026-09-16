#!/usr/bin/env python3
"""CDK app for the cloud (productization plan §8 phase 9). No Lambda anywhere:
see infra/README.md.

    cd infra && npx aws-cdk@2 synth -c domain=example.com -c alert_email=you@example.com
"""

import aws_cdk as cdk

from jhi_infra.config import Config
from jhi_infra.github_stack import GithubDeployStack
from jhi_infra.cloud_stack import CloudStack

app = cdk.App()
config = Config.from_context(app.node)
env = cdk.Environment(account=config.account, region=config.region)

GithubDeployStack(app, "JhiGithubDeploy", config=config, env=env)
CloudStack(app, "JhiCloud", config=config, env=env)

app.synth()
