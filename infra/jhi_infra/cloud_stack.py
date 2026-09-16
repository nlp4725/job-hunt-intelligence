"""Everything the cloud product runs on (productization plan §1, §5, §8 phase 9).

    Internet ─► ALB (HTTPS, api.<domain>) ─► API on Fargate (public subnets, no inbound except from the ALB)
            ─► CloudFront (app.<domain>) ─► private S3 bucket with the React build
    API + scheduled workers ─► RDS Postgres (isolated subnets, TLS only)
                            ─► S3 resume bucket (KMS), Secrets Manager, Cognito, DeepSeek

No NAT gateway: tasks sit in public subnets with public IPs (for DeepSeek and
Cognito) and security groups that accept nothing from the internet. No Lambda
and no SQS: the queue is the rescore_queue table, workers are scheduled
Fargate tasks, and constructs that create hidden Lambda custom resources are
avoided (tests/test_cloud_stack.py fails if one appears).
"""

import aws_cdk as cdk
from aws_cdk import (
    IgnoreMode,
    aws_budgets as budgets,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_rds as rds,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
)
from constructs import Construct

from jhi_infra.config import DEPLOY_ROLE_NAME, Config

REPO_ROOT = "../"
DB_NAME = "jhi"
APP_LOGIN, ADMIN_LOGIN = "jhi_api", "jhi_admin"
LOG_RETENTION = logs.RetentionDays.ONE_MONTH


def _login_secret(scope: Construct, construct_id: str, username: str) -> secretsmanager.Secret:
    return secretsmanager.Secret(
        scope, construct_id,
        generate_secret_string=secretsmanager.SecretStringGenerator(
            secret_string_template=f'{{"username": "{username}"}}', generate_string_key="password",
            exclude_punctuation=True, password_length=40),
    )


class CloudStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        zone = (route53.HostedZone.from_hosted_zone_attributes(
                    self, "Zone", hosted_zone_id=config.hosted_zone_id, zone_name=config.domain)
                if config.hosted_zone_id else None)
        validation = acm.CertificateValidation.from_dns(zone)   # no zone: add the CNAME shown in the ACM console

        alerts = sns.Topic(self, "Alerts")
        if config.alert_email:
            alerts.add_subscription(subscriptions.EmailSubscription(config.alert_email))

        # --- network ---------------------------------------------------------------
        vpc = ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=0, restrict_default_security_group=False,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="data", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        tasks_sg = ec2.SecurityGroup(self, "TasksSg", vpc=vpc, description="API and worker tasks: no inbound from the internet")
        db_sg = ec2.SecurityGroup(self, "DbSg", vpc=vpc, description="Postgres: only from the tasks", allow_all_outbound=False)
        db_sg.add_ingress_rule(tasks_sg, ec2.Port.tcp(5432), "tasks to Postgres")

        # --- database --------------------------------------------------------------
        engine = rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_18_3)
        database = rds.DatabaseInstance(
            self, "Database", engine=engine,
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO),
            vpc=vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg], publicly_accessible=False, multi_az=False,
            database_name=DB_NAME, credentials=rds.Credentials.from_generated_secret("jhi_owner"),
            allocated_storage=20, max_allocated_storage=100, storage_type=rds.StorageType.GP3, storage_encrypted=True,
            backup_retention=cdk.Duration.days(7), deletion_protection=True, removal_policy=cdk.RemovalPolicy.SNAPSHOT,
            auto_minor_version_upgrade=True,
            parameter_group=rds.ParameterGroup(self, "DbParams", engine=engine, parameters={"rds.force_ssl": "1"}),
        )
        owner_secret = database.secret
        app_login = _login_secret(self, "AppLoginSecret", APP_LOGIN)
        admin_login = _login_secret(self, "AdminLoginSecret", ADMIN_LOGIN)
        resume_key = secretsmanager.Secret(
            self, "ResumeKeySecret", description="Resume text encryption key (hashed into a Fernet key)",
            generate_secret_string=secretsmanager.SecretStringGenerator(exclude_punctuation=True, password_length=64),
            removal_policy=cdk.RemovalPolicy.RETAIN)
        deepseek_key = secretsmanager.Secret(
            self, "DeepSeekApiKey", description="Set once by hand: aws secretsmanager put-secret-value (infra/README.md)")

        # --- resume files ----------------------------------------------------------
        resume_kms = kms.Key(self, "ResumeKey", enable_key_rotation=True, removal_policy=cdk.RemovalPolicy.RETAIN,
                             description="Encrypts resume files in S3")
        resumes = s3.Bucket(
            self, "Resumes", encryption=s3.BucketEncryption.KMS, encryption_key=resume_kms, bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True, versioned=False,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, removal_policy=cdk.RemovalPolicy.RETAIN,
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.POST], allowed_origins=[f"https://{config.app_host}"],
                              allowed_headers=["*"], max_age=3000)],
            lifecycle_rules=[s3.LifecycleRule(abort_incomplete_multipart_upload_after=cdk.Duration.days(1))],
        )

        # --- sign-in ---------------------------------------------------------------
        user_pool = cognito.UserPool(
            self, "Users", self_sign_up_enabled=True, sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(email=cognito.StandardAttribute(required=True, mutable=True)),
            password_policy=cognito.PasswordPolicy(min_length=12, require_symbols=False),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY, deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        user_pool.add_domain("SignInDomain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=config.cognito_domain_prefix))
        web_client = user_pool.add_client(
            "WebClient", generate_secret=False, auth_flows=cognito.AuthFlow(user_srp=True),
            prevent_user_existence_errors=True, id_token_validity=cdk.Duration.hours(1),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=[f"https://{config.app_host}/auth/callback", "http://localhost:5173/auth/callback"],
                logout_urls=[f"https://{config.app_host}/", "http://localhost:5173/"]),
        )

        # --- containers ------------------------------------------------------------
        image = ecs.ContainerImage.from_docker_image_asset(ecr_assets.DockerImageAsset(
            self, "Image", directory=REPO_ROOT, platform=ecr_assets.Platform.LINUX_ARM64,
            ignore_mode=IgnoreMode.DOCKER))   # the repo's allowlist .dockerignore decides what goes in
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.DISABLED)

        db_env = {"DB_HOST": database.db_instance_endpoint_address, "DB_PORT": database.db_instance_endpoint_port,
                  "DB_NAME": DB_NAME, "AWS_REGION": self.region}
        owner_secrets = {"DB_OWNER_USER": ecs.Secret.from_secrets_manager(owner_secret, "username"),
                         "DB_OWNER_PASSWORD": ecs.Secret.from_secrets_manager(owner_secret, "password")}
        login_secrets = {
            "JHI_APP_DB_USER": ecs.Secret.from_secrets_manager(app_login, "username"),
            "JHI_APP_DB_PASSWORD": ecs.Secret.from_secrets_manager(app_login, "password"),
            "JHI_ADMIN_DB_USER": ecs.Secret.from_secrets_manager(admin_login, "username"),
            "JHI_ADMIN_DB_PASSWORD": ecs.Secret.from_secrets_manager(admin_login, "password"),
        }
        llm_secrets = {"DEEPSEEK_API_KEY": ecs.Secret.from_secrets_manager(deepseek_key)}

        def log_driver(name: str) -> ecs.LogDriver:
            # An explicit log group with retention: the log_retention shortcut adds a Lambda.
            group = logs.LogGroup(self, f"{name}Logs", retention=LOG_RETENTION, removal_policy=cdk.RemovalPolicy.DESTROY)
            return ecs.LogDrivers.aws_logs(stream_prefix=name.lower(), log_group=group)

        def task_definition(name: str, cpu: int, memory: int) -> ecs.FargateTaskDefinition:
            return ecs.FargateTaskDefinition(
                self, f"{name}Task", cpu=cpu, memory_limit_mib=memory,
                runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64,
                                                     operating_system_family=ecs.OperatingSystemFamily.LINUX))

        # API: every task runs migrations (and ensures the two logins) in a
        # short-lived container first; the API container starts only if that
        # succeeded, so new code never runs against an old schema.
        api_task = task_definition("Api", cpu=512, memory=1024)
        migrate = api_task.add_container(
            "migrate", image=image, essential=False, command=["python", "-m", "cloud_api.deploy_tasks", "migrate"],
            environment={**db_env, **({"JHI_OWNER_EMAIL": config.owner_email} if config.owner_email else {})}, secrets={**owner_secrets, **login_secrets}, logging=log_driver("Migrate"))
        api = api_task.add_container(
            "api", image=image, essential=True, logging=log_driver("Api"),
            port_mappings=[ecs.PortMapping(container_port=8000)],
            environment={**db_env, "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                         "COGNITO_CLIENT_ID": web_client.user_pool_client_id, "RESUME_BUCKET": resumes.bucket_name,
                         "RESUME_KMS_KEY_ID": resume_kms.key_arn, "CORS_ORIGINS": f"https://{config.app_host}"},
            secrets={**login_secrets, **llm_secrets, "JHI_RESUME_KEY": ecs.Secret.from_secrets_manager(resume_key)},
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)\""],
                start_period=cdk.Duration.seconds(30)))
        api.add_container_dependencies(ecs.ContainerDependency(container=migrate, condition=ecs.ContainerDependencyCondition.SUCCESS))
        resumes.grant_read_write(api_task.task_role)
        resumes.grant_delete(api_task.task_role)
        resume_kms.grant_encrypt_decrypt(api_task.task_role)

        api_cert = acm.Certificate(self, "ApiCert", domain_name=config.api_host, validation=validation)
        alb = elbv2.ApplicationLoadBalancer(self, "Alb", vpc=vpc, internet_facing=True, idle_timeout=cdk.Duration.seconds(180),
                                            drop_invalid_header_fields=True)
        alb.add_redirect()   # HTTP → HTTPS
        service = ecs.FargateService(
            self, "ApiService", cluster=cluster, task_definition=api_task, desired_count=config.api_desired_count,
            assign_public_ip=True, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[tasks_sg], min_healthy_percent=100, max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True), health_check_grace_period=cdk.Duration.seconds(120))
        listener = alb.add_listener("Https", port=443, certificates=[api_cert], ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS)
        target_group = listener.add_targets(
            "Api", port=8000, protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service.load_balancer_target(container_name="api", container_port=8000)],
            health_check=elbv2.HealthCheck(path="/healthz", healthy_http_codes="200"),
            deregistration_delay=cdk.Duration.seconds(30))
        tasks_sg.connections.allow_from(alb, ec2.Port.tcp(8000), "ALB to API")
        if zone:
            route53.ARecord(self, "ApiAlias", zone=zone, record_name="api",
                            target=route53.RecordTarget.from_alias(route53_targets.LoadBalancerTarget(alb)))

        # Workers: scheduled, run once and stop, as the table owner.
        def scheduled_worker(name: str, command: list[str], every: cdk.Duration, extra_secrets: dict) -> None:
            task = task_definition(name, cpu=256, memory=512)
            task.add_container(name.lower(), image=image, command=command, environment=db_env,
                               secrets={**owner_secrets, **extra_secrets}, logging=log_driver(name))
            events.Rule(self, f"{name}Schedule", schedule=events.Schedule.rate(every), targets=[targets.EcsTask(
                cluster=cluster, task_definition=task, assign_public_ip=True, security_groups=[tasks_sg],
                subnet_selection=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC))])

        scheduled_worker("Rescore", ["python", "-m", "cloud_api.rescore_worker", "--once"], cdk.Duration.minutes(5), {})
        scheduled_worker("Expertise", ["python", "-m", "cloud_api.expertise_worker", "--once", "--limit", "50"],
                         cdk.Duration.minutes(30), llm_secrets)

        # --- frontend --------------------------------------------------------------
        site = s3.Bucket(self, "Frontend", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=cdk.RemovalPolicy.RETAIN,
                         object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED)
        app_cert = acm.Certificate(self, "AppCert", domain_name=config.app_host, validation=validation)
        distribution = cloudfront.Distribution(
            self, "Site", domain_names=[config.app_host], certificate=app_cert, default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS),
            error_responses=[cloudfront.ErrorResponse(http_status=code, response_http_status=200, response_page_path="/index.html")
                             for code in (403, 404)],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021)
        if zone:
            route53.ARecord(self, "AppAlias", zone=zone, record_name="app",
                            target=route53.RecordTarget.from_alias(route53_targets.CloudFrontTarget(distribution)))
        # The GitHub deploy role uploads the React build (aws s3 sync) and refreshes CloudFront.
        deploy_role = iam.Role.from_role_name(self, "GithubDeployRole", DEPLOY_ROLE_NAME)
        iam.Policy(self, "FrontendUpload", roles=[deploy_role], statements=[
            iam.PolicyStatement(actions=["s3:ListBucket"], resources=[site.bucket_arn]),
            iam.PolicyStatement(actions=["s3:PutObject", "s3:DeleteObject"], resources=[site.arn_for_objects("*")]),
            iam.PolicyStatement(actions=["cloudfront:CreateInvalidation"],
                                resources=[f"arn:aws:cloudfront::{self.account}:distribution/{distribution.distribution_id}"]),
        ])

        # --- alarms and budget ------------------------------------------------------
        action = cw_actions.SnsAction(alerts)
        for alarm in (
            cloudwatch.Alarm(self, "Api5xx", metric=alb.metrics.http_code_target(elbv2.HttpCodeTarget.TARGET_5XX_COUNT,
                                                                                  period=cdk.Duration.minutes(5)),
                             threshold=5, evaluation_periods=1, treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING),
            cloudwatch.Alarm(self, "ApiUnhealthy", metric=target_group.metrics.unhealthy_host_count(period=cdk.Duration.minutes(1)),
                             threshold=1, evaluation_periods=5,
                             comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD),
            cloudwatch.Alarm(self, "DbCpu", metric=database.metric_cpu_utilization(period=cdk.Duration.minutes(5)),
                             threshold=80, evaluation_periods=3),
            cloudwatch.Alarm(self, "DbStorage", metric=database.metric_free_storage_space(period=cdk.Duration.minutes(5)),
                             threshold=2 * 1024 ** 3, evaluation_periods=1,
                             comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD),
        ):
            alarm.add_alarm_action(action)

        if config.alert_email:
            budgets.CfnBudget(self, "MonthlyBudget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=config.monthly_budget_usd, unit="USD")),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type=kind, comparison_operator="GREATER_THAN", threshold=threshold),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=config.alert_email)])
                    for kind, threshold in (("ACTUAL", 80), ("FORECASTED", 100))])

        # --- outputs ---------------------------------------------------------------
        for name, value in {
            "ApiUrl": f"https://{config.api_host}", "AppUrl": f"https://{config.app_host}",
            "AlbDnsName": alb.load_balancer_dns_name, "CloudFrontDomain": distribution.distribution_domain_name,
            "FrontendBucket": site.bucket_name, "DistributionId": distribution.distribution_id,
            "UserPoolId": user_pool.user_pool_id, "WebClientId": web_client.user_pool_client_id,
            "DeepSeekSecretArn": deepseek_key.secret_arn,
            "ClusterName": cluster.cluster_name, "ApiServiceName": service.service_name,
        }.items():
            cdk.CfnOutput(self, name, value=value)
