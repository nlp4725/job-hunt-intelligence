"""Deploy settings, from cdk.json context or -c key=value."""

import os
from dataclasses import dataclass

DEPLOY_ROLE_NAME = "jhi-github-deploy"


@dataclass(frozen=True)
class Config:
    region: str
    account: str | None
    domain: str                 # e.g. example.com → api.example.com, app.example.com
    hosted_zone_id: str         # Route 53 zone for `domain`; empty = DNS records added by hand
    alert_email: str
    owner_email: str            # made admin by the migrate step; claimed at first verified sign-in
    monthly_budget_usd: int
    api_desired_count: int
    github_repo: str            # owner/name allowed to deploy
    cognito_domain_prefix: str  # sign-in pages at <prefix>.auth.<region>.amazoncognito.com

    @property
    def api_host(self) -> str:
        return f"api.{self.domain}"

    @property
    def app_host(self) -> str:
        return f"app.{self.domain}"

    @classmethod
    def from_context(cls, node) -> "Config":
        def get(key, default=""):
            value = node.try_get_context(key)
            return default if value in (None, "") else value

        domain = str(get("domain")).strip().lower()
        if not domain:
            raise ValueError("set the domain: -c domain=example.com (the API must be served over HTTPS)")
        return cls(
            region=str(get("region", "us-east-1")),
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            domain=domain,
            hosted_zone_id=str(get("hosted_zone_id")),
            alert_email=str(get("alert_email")),
            owner_email=str(get("owner_email")),
            monthly_budget_usd=int(get("monthly_budget_usd", 75)),
            api_desired_count=int(get("api_desired_count", 1)),
            github_repo=str(get("github_repo")),
            cognito_domain_prefix=str(get("cognito_domain_prefix")) or "jhi-" + domain.split(".")[0],
        )
