"""CDK entry point for the support-triage deploy (M6).

One stack: a container of the app, in one VPC, behind nothing but a security
group. The deploy exists to be photographed and torn down, so there is no ALB,
no HTTPS, no autoscaling, no CI — deliberately.
"""

from __future__ import annotations

import os

from aws_cdk import App, Environment

from support_triage_stack import SupportTriageStack

app = App()

SupportTriageStack(
    app,
    "support-triage",
    env=Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
