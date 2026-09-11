"""Bluey platform stack with a safe managed-KB retrieval configuration fix.

The implementation lives in ``platform_stack_original.py`` so this wrapper can
apply a CloudFormation-level compatibility fix without duplicating the large
platform stack. AWS documents ``numberOfResults`` as an integer, but the
AgentCore Gateway managed-Knowledge-Base connector is currently rejecting the
nested value as a string at runtime. We therefore omit the optional
``retrievalConfiguration`` override and let Managed Knowledge Bases use their
default of five results.
"""

from aws_cdk import aws_bedrockagentcore as bedrockagentcore

from stacks.platform_stack_original import BlueyPlatformStack as _BlueyPlatformStack


class BlueyPlatformStack(_BlueyPlatformStack):
    """Original platform stack with the incompatible KB result-count override removed."""

    _KB_TARGETS = {
        "StandardBankKnowledgeTarget",
        "FinancialAdviceKnowledgeTarget",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._remove_invalid_kb_result_count()

    def _remove_invalid_kb_result_count(self) -> None:
        """Remove the optional result-count override from managed-KB targets.

        AWS documents five results as the default for managed Knowledge Base
        retrieval. Omitting the optional override avoids the AgentCore Gateway
        connector serializing ``numberOfResults`` as a string while preserving
        the existing effective result limit.
        """
        for child in self.node.find_all():
            if not isinstance(child, bedrockagentcore.CfnGatewayTarget):
                continue
            if child.node.id not in self._KB_TARGETS:
                continue
            child.add_property_deletion_override(
                "TargetConfiguration.mcp.connector.configurations.0.parameterValues.retrievalConfiguration"
            )
