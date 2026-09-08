#!/usr/bin/env python3
import os
from aws_cdk import App, Environment

from stacks.data_stack import BlueyDataStack
from stacks.auth_stack import BlueyAuthStack
from stacks.knowledge_stack import BlueyKnowledgeStack
from stacks.platform_stack import BlueyPlatformStack

app = App()
stage = app.node.try_get_context("stage") or os.getenv("BLUEY_STAGE", "dev")
region = (
    os.getenv("CDK_DEFAULT_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or os.getenv("AWS_REGION")
    or "us-east-1"
)

# CDK selects the target account from AWS credentials/profile at deploy time.
env = Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=region,
)

data = BlueyDataStack(app, f"BlueyData-{stage}", stage=stage, env=env)
auth = BlueyAuthStack(app, f"BlueyAuth-{stage}", stage=stage, env=env)
knowledge = BlueyKnowledgeStack(app, f"BlueyKnowledge-{stage}", stage=stage, env=env)

# BlueyApi and BlueyAgentCore were originally separate stacks but had a
# genuine circular stack dependency (see platform_stack.py's module
# docstring) — merged into one stack to make deployment possible at all.
platform = BlueyPlatformStack(
    app,
    f"BlueyPlatform-{stage}",
    stage=stage,
    data_stack=data,
    auth_stack=auth,
    knowledge_stack=knowledge,
    env=env,
)

app.synth()
