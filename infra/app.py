#!/usr/bin/env python3
import os
from aws_cdk import App, Environment

from stacks.data_stack import BlueyDataStack
from stacks.auth_stack import BlueyAuthStack
from stacks.api_stack import BlueyApiStack
from stacks.agentcore_stack import BlueyAgentCoreStack
from stacks.knowledge_stack import BlueyKnowledgeStack

app = App()
stage = app.node.try_get_context("stage") or os.getenv("BLUEY_STAGE", "dev")
region = os.getenv("CDK_DEFAULT_REGION", "us-east-1")

# CDK selects the target account from AWS credentials/profile at deploy time.
env = Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=region,
)

data = BlueyDataStack(app, f"BlueyData-{stage}", stage=stage, env=env)
auth = BlueyAuthStack(app, f"BlueyAuth-{stage}", stage=stage, env=env)
api = BlueyApiStack(
    app,
    f"BlueyApi-{stage}",
    stage=stage,
    data_stack=data,
    auth_stack=auth,
    env=env,
)

knowledge = BlueyKnowledgeStack(app, f"BlueyKnowledge-{stage}", stage=stage, env=env)
agentcore = BlueyAgentCoreStack(app, f"BlueyAgentCore-{stage}", stage=stage, api_stack=api, knowledge_stack=knowledge, env=env)
app.synth()
