"""Bug condition exploration test for the Knowledge Base numberOfResults type bug.

Property 1: Bug Condition - numberOfResults Serialized As Integer

Validates: Requirements 1.1, 1.2, 2.1, 2.2

For every retrieval-enabled Knowledge Base gateway target, the synthesized
CloudFormation template's
``TargetConfiguration.Mcp.Connector.Configurations[0].ParameterValues
.retrievalConfiguration.managedSearchConfiguration.numberOfResults`` value
MUST be a JSON integer (Python ``int``), NOT a JSON string.

This test is EXPECTED TO FAIL on unfixed code: the nested integer ``5`` is
coerced to the string ``"5"`` during CDK synthesis. The failure confirms the
bug exists.
"""
import os
import sys
from pathlib import Path

import pytest

# cdk.json runs `python infra/app.py` from the bluey/ project root, so the
# stack modules are imported as `from stacks.<x> import ...`. Mirror that by
# putting bluey/infra on sys.path.
_INFRA_DIR = Path(__file__).resolve().parents[1] / "infra"
if str(_INFRA_DIR) not in sys.path:
    sys.path.insert(0, str(_INFRA_DIR))

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402

from stacks.data_stack import BlueyDataStack  # noqa: E402
from stacks.auth_stack import BlueyAuthStack  # noqa: E402
from stacks.knowledge_stack import BlueyKnowledgeStack  # noqa: E402
from stacks.platform_stack import BlueyPlatformStack  # noqa: E402


# The two retrieval-enabled gateway targets (kb_target(..., retrieval=True)).
RETRIEVAL_TARGET_NAMES = ["bluey-kb-standard-bank", "bluey-kb-financial-advice"]

_TEST_ENV = Environment(account="123456789012", region="us-east-1")


def _synthesize_platform_template() -> Template:
    """Build the platform stack (with its dependencies) and return its Template."""
    app = App(context={"stage": "test"})
    data = BlueyDataStack(app, "BlueyData-test", stage="test", env=_TEST_ENV)
    auth = BlueyAuthStack(app, "BlueyAuth-test", stage="test", env=_TEST_ENV)
    knowledge = BlueyKnowledgeStack(app, "BlueyKnowledge-test", stage="test", env=_TEST_ENV)
    platform = BlueyPlatformStack(
        app,
        "BlueyPlatform-test",
        stage="test",
        data_stack=data,
        auth_stack=auth,
        knowledge_stack=knowledge,
        env=_TEST_ENV,
    )
    return Template.from_stack(platform)


def _number_of_results_for_target(template: Template, target_name: str):
    """Extract numberOfResults for a named GatewayTarget from the template."""
    resources = template.find_resources(
        "AWS::BedrockAgentCore::GatewayTarget",
        {"Properties": {"Name": target_name}},
    )
    assert resources, f"No GatewayTarget resource found with Name {target_name!r}"
    assert len(resources) == 1, f"Expected exactly one target named {target_name!r}"

    props = next(iter(resources.values()))["Properties"]
    configurations = props["TargetConfiguration"]["Mcp"]["Connector"]["Configurations"]
    parameter_values = configurations[0]["ParameterValues"]
    return parameter_values["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"]


@pytest.fixture(scope="module")
def platform_template() -> Template:
    return _synthesize_platform_template()


@pytest.mark.parametrize("target_name", RETRIEVAL_TARGET_NAMES)
def test_number_of_results_is_integer_not_string(platform_template, target_name):
    """numberOfResults must synthesize as a JSON integer, not a string.

    Property 1: Bug Condition - numberOfResults Serialized As Integer
    Validates: Requirements 1.1, 1.2, 2.1, 2.2
    """
    value = _number_of_results_for_target(platform_template, target_name)

    assert isinstance(value, int) and not isinstance(value, str), (
        f"Target {target_name!r}: numberOfResults synthesized as "
        f"{value!r} (type {type(value).__name__}); expected JSON integer 5, "
        f"not a string."
    )
    assert value == 5, f"Target {target_name!r}: expected numberOfResults == 5, got {value!r}"
