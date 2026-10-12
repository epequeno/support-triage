"""The one stack: a Fargate service running the container, in its own VPC.

Deliberately minimal, per docs/NEXT.md: container, one VPC, the app behind it,
deploy once for real, torn down after the screenshot. No ALB (the task's public
IP is the URL), no NAT (the public subnet reaches OpenRouter through the
internet gateway), no secrets plumbing (the torn-down-in-an-hour task
definition carries the API key as a plain environment variable — see the
caveat in the commit).
"""

from __future__ import annotations

import os

from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr_assets as ecr_assets,
    aws_logs as logs,
)
from constructs import Construct

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SupportTriageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # The triage calls OpenRouter, so the key must exist at synth time —
        # failing here, at synthesis, is better than failing in the cloud.
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY must be set (synth-time check) — the cloud UI "
                "needs it to triage anything."
            )

        vpc = ec2.Vpc(
            self,
            "vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                {"name": "public", "subnetType": ec2.SubnetType.PUBLIC}
            ],
        )

        cluster = ecs.Cluster(self, "cluster", vpc=vpc)

        # CDK builds the Dockerfile at the repo root and pushes it to ECR.
        # Platform pinned: the local Docker builds arm64 (Apple Silicon), Fargate
        # runs x86_64 — without this the tasks crash with `exec format error`
        # (found live in the cloud; nothing local can catch it).
        image = ecr_assets.DockerImageAsset(
            self,
            "image",
            directory=REPO_ROOT,
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        task = ecs.FargateTaskDefinition(
            self, "task", cpu=256, memory_limit_mib=512
        )

        task.add_container(
            "app",
            image=ecs.ContainerImage.from_docker_image_asset(image),
            environment={"OPENROUTER_API_KEY": api_key},
            logging=ecs.AwsLogDriver(
                stream_prefix="triage",
                log_group=logs.LogGroup(
                    self,
                    "logs",
                    retention=logs.RetentionDays.ONE_DAY,
                ),
            ),
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "python -c \"import urllib.request as u; u.urlopen("
                    "'http://localhost:8000/api/cases', timeout=5)\" || exit 1",
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(15),
            ),
            port_mappings=[ecs.PortMapping(container_port=8000)],
        )

        # Port 8000 open to the internet: the UI is the point of the deploy,
        # and the deploy is torn down after the screenshot.
        sg = ec2.SecurityGroup(
            self, "sg", vpc=vpc, description="allow the UI on 8000"
        )
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8000))

        ecs.FargateService(
            self,
            "service",
            cluster=cluster,
            task_definition=task,
            assign_public_ip=True,
            security_groups=[sg],
            desired_count=1,
        )
