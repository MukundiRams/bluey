"""Bluey platform stack with reliable Managed Knowledge Base retrieval.

The original stack is kept in ``platform_stack_original.py``. This wrapper
replaces the AgentCore managed-KB connector targets with Lambda targets that
call the Bedrock Retrieve API directly. The managed connector currently
serializes ``managedSearchConfiguration.numberOfResults`` incorrectly as a
string for the failing agent path; the Bedrock Runtime SDK sends the integer
type correctly.
"""

from pathlib import Path

from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_

from stacks.platform_stack_original import BlueyPlatformStack as _BlueyPlatformStack


class BlueyPlatformStack(_BlueyPlatformStack):
    """Original platform stack with a typed Lambda-based KB retrieval path."""

    def __init__(self, *args, **kwargs):
        self._stage = kwargs.get("stage", "dev")
        super().__init__(*args, **kwargs)
        self._replace_managed_kb_targets()

    def _replace_managed_kb_targets(self) -> None:
        """Replace connector targets with directly typed Bedrock Retrieve calls."""
        kb_specs = {
            "StandardBankKnowledgeTarget": (
                "BlueyMainKnowledgeRetrieval",
                "MainGateway",
                "MainGatewayRole",
                "bluey-kb-standard-bank-retrieval",
            ),
            "CreditKnowledgeTarget": (
                "BlueyCreditKnowledgeRetrieval",
                "CreditGateway",
                "CreditGatewayRole",
                "bluey-kb-credit-retrieval",
            ),
            "AccountOpeningKnowledgeTarget": (
                "BlueyAccountOpeningKnowledgeRetrieval",
                "AccountOpeningGateway",
                "AccountOpeningGatewayRole",
                "bluey-kb-account-opening-retrieval",
            ),
            "FinancialAdviceKnowledgeTarget": (
                "BlueyFinancialAdviceKnowledgeRetrieval",
                "FinancialAdviceGateway",
                "FinancialAdviceGatewayRole",
                "bluey-kb-financial-advice-retrieval",
            ),
        }

        # Retrieve the KB IDs from the existing KnowledgeStack construct. It is
        # a sibling of this stack, so locate it through the root App.
        knowledge_stack = None
        for construct in self.node.root.node.find_all():
            if construct.node.id.startswith("BlueyKnowledge-"):
                knowledge_stack = construct
                break
        if knowledge_stack is None:
            raise RuntimeError("BlueyKnowledge stack was not found")

        kb_ids = {
            "StandardBankKnowledgeTarget": knowledge_stack.main_kb.attr_knowledge_base_id,
            "CreditKnowledgeTarget": knowledge_stack.credit_kb.attr_knowledge_base_id,
            "AccountOpeningKnowledgeTarget": knowledge_stack.account_opening_kb.attr_knowledge_base_id,
            "FinancialAdviceKnowledgeTarget": knowledge_stack.main_kb.attr_knowledge_base_id,
        }
        kb_arns = {
            "StandardBankKnowledgeTarget": knowledge_stack.main_kb.attr_knowledge_base_arn,
            "CreditKnowledgeTarget": knowledge_stack.credit_kb.attr_knowledge_base_arn,
            "AccountOpeningKnowledgeTarget": knowledge_stack.account_opening_kb.attr_knowledge_base_arn,
            "FinancialAdviceKnowledgeTarget": knowledge_stack.main_kb.attr_knowledge_base_arn,
        }

        for target_id, (function_id, gateway_id, gateway_role_id, target_name) in kb_specs.items():
            target = self.node.find_child(target_id)
            gateway = self.node.find_child(gateway_id)
            gateway_role = self.node.find_child(gateway_role_id)
            function_name = f"{target_name}-{self._stage}"

            function_role = iam.Role(
                self,
                function_id + "Role",
                role_name=f"{function_name}-role",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )
            function_role.add_to_policy(
                iam.PolicyStatement(
                    sid="RetrieveAssignedKnowledgeBase",
                    actions=["bedrock:Retrieve"],
                    resources=[kb_arns[target_id]],
                )
            )

            function = lambda_.Function(
                self,
                function_id,
                function_name=function_name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=lambda_.Code.from_asset(
                    str(Path(__file__).parents[2] / "lambda/knowledge_retrieval")
                ),
                timeout=30,
                role=function_role,
                environment={
                    "AWS_REGION_NAME": self.region,
                    "KNOWLEDGE_BASE_ID": kb_ids[target_id],
                },
            )

            permission = function.add_permission(
                function_id + "GatewayPermission",
                principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
                action="lambda:InvokeFunction",
                source_account=self.account,
                source_arn=gateway.attr_gateway_arn,
            )

            gateway_role.add_to_policy(
                iam.PolicyStatement(
                    sid=function_id + "Invoke",
                    actions=["lambda:InvokeFunction"],
                    resources=[function.function_arn],
                )
            )

            # Keep the existing target identity and replace only its target
            # implementation. This avoids duplicate Gateway targets during an
            # update and removes the managed-KB connector from the call path.
            target.add_property_override(
                "TargetConfiguration",
                {
                    "mcp": {
                        "lambda": {
                            "lambdaArn": function.function_arn,
                            "toolSchema": {
                                "inlinePayload": [
                                    {
                                        "name": "Retrieve",
                                        "description": (
                                            "Retrieve grounded information from the assigned "
                                            "Standard Bank knowledge base."
                                        ),
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {
                                                "retrievalQuery": {
                                                    "type": "object",
                                                    "properties": {
                                                        "text": {"type": "string"}
                                                    },
                                                    "required": ["text"],
                                                },
                                                "retrievalConfiguration": {
                                                    "type": "object",
                                                    "properties": {
                                                        "managedSearchConfiguration": {
                                                            "type": "object",
                                                            "properties": {
                                                                "numberOfResults": {
                                                                    "type": "integer",
                                                                    "minimum": 1,
                                                                    "maximum": 100,
                                                                }
                                                            },
                                                        }
                                                    },
                                                },
                                            },
                                            "required": ["retrievalQuery"],
                                        },
                                    }
                                ]
                            },
                        }
                    }
                },
            )
            target.add_dependency(permission)
            target.add_dependency(function)
