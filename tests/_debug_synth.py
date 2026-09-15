import json
import sys
from pathlib import Path

_INFRA_DIR = Path(__file__).resolve().parents[1] / "infra"
sys.path.insert(0, str(_INFRA_DIR))

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from stacks.data_stack import BlueyDataStack
from stacks.auth_stack import BlueyAuthStack
from stacks.knowledge_stack import BlueyKnowledgeStack
from stacks.platform_stack import BlueyPlatformStack

env = Environment(account="123456789012", region="us-east-1")
app = App(context={"stage": "test"})
data = BlueyDataStack(app, "BlueyData-test", stage="test", env=env)
auth = BlueyAuthStack(app, "BlueyAuth-test", stage="test", env=env)
knowledge = BlueyKnowledgeStack(app, "BlueyKnowledge-test", stage="test", env=env)
platform = BlueyPlatformStack(app, "BlueyPlatform-test", stage="test",
                              data_stack=data, auth_stack=auth, knowledge_stack=knowledge, env=env)
t = Template.from_stack(platform)
for name in ["bluey-kb-standard-bank", "bluey-kb-financial-advice"]:
    res = t.find_resources("AWS::BedrockAgentCore::GatewayTarget", {"Properties": {"Name": name}})
    props = next(iter(res.values()))["Properties"]
    cfg = props["TargetConfiguration"]["Mcp"]["Connector"]["Configurations"][0]
    pv = cfg["ParameterValues"]
    val = pv["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"]
    print(f"{name}: numberOfResults={val!r} type={type(val).__name__}")
    print("  ParameterValues JSON:", json.dumps(pv))
