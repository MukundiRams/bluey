"""CDK synth smoke check for the banker-query-triage read-state table.

The ``bluey-banker-read-state`` table is created in the DATA stack
(``BlueyDataStack``). This is a smoke/snapshot-style check (NOT a
property-based test) asserting that the synthesized CloudFormation template
contains the expected DynamoDB table with the right key schema and billing
mode.

Validates: Requirements 4.1, 4.4
"""
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


READ_STATE_TABLE_NAME = "bluey-banker-read-state-test"

_TEST_ENV = Environment(account="123456789012", region="us-east-1")


def _synthesize_data_template() -> Template:
    """Build the data stack and return its Template."""
    app = App(context={"stage": "test"})
    data = BlueyDataStack(app, "BlueyData-test", stage="test", env=_TEST_ENV)
    return Template.from_stack(data)


@pytest.fixture(scope="module")
def data_template() -> Template:
    return _synthesize_data_template()


def test_read_state_table_exists_with_expected_schema(data_template):
    """The read-state table synthesizes with the correct key schema and billing mode.

    Validates: Requirements 4.1, 4.4
    """
    data_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": READ_STATE_TABLE_NAME,
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "bankerId", "KeyType": "HASH"},
                {"AttributeName": "itemId", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "bankerId", "AttributeType": "S"},
                {"AttributeName": "itemId", "AttributeType": "S"},
            ],
        },
    )


def test_exactly_one_read_state_table_resource(data_template):
    """There is exactly one DynamoDB table named for the read-state store.

    Validates: Requirements 4.1
    """
    resources = data_template.find_resources(
        "AWS::DynamoDB::Table",
        {"Properties": {"TableName": READ_STATE_TABLE_NAME}},
    )
    assert resources, f"No DynamoDB table found with TableName {READ_STATE_TABLE_NAME!r}"
    assert len(resources) == 1, (
        f"Expected exactly one table named {READ_STATE_TABLE_NAME!r}, "
        f"found {len(resources)}"
    )
