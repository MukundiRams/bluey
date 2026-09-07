"""AgentCore Harness + Gateway infrastructure.

The Gateway is the authorization boundary between Harnesses and backend tools.

Harness -> InvokeGateway -> Gateway -> Lambda target
                         -> Knowledge Base target

CloudFormation L1 resources are used because AgentCore Gateway/Harness features evolve faster
than the CDK L2 surface. The properties mirror the current AWS CloudFormation contract.
"""

from pathlib import Path

from aws_cdk import Stack, aws_iam as iam, aws_ssm as ssm
from aws_cdk import CfnResource
from constructs import Construct


REGION = "us-east-1"
HARNESS_IMAGE = "public.ecr.aws/i0n3d3i5/harness-us-east-1:latest"


def _policy(actions, resources, sid):
    return iam.PolicyStatement(sid=sid, actions=actions, resources=resources)


class BlueyAgentCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, api_stack, knowledge_stack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Gateway service role: AgentCore assumes this role to execute targets.
        gateway_role = iam.Role(
            self,
            "GatewayExecutionRole",
            role_name=f"bluey-agentcore-gateway-role-{stage}",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        gateway_role.add_to_policy(
            _policy(
                ["lambda:InvokeFunction"],
                [api_stack.sessions_fn.function_arn, api_stack.credit_fn.function_arn],
                "InvokeBlueyLambdaTargets",
            )
        )
        gateway_role.add_to_policy(
            _policy(
                ["bedrock:GetKnowledgeBase", "bedrock:Retrieve"],
                [
                    knowledge_stack.credit_kb.attr_arn,
                    knowledge_stack.main_kb.attr_arn,
                ],
                "ReadBlueyKnowledgeBases",
            )
        )
        gateway_role.add_to_policy(_policy(["bedrock:AgenticRetrieveStream"], ["*"], "AgenticRetrieve"))

        gateway = CfnResource(
            self,
            "BlueyGateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": f"bluey-gateway-{stage}",
                "Description": "Bluey banking tools and approved knowledge bases",
                "AuthorizerType": "AWS_IAM",
                "RoleArn": gateway_role.role_arn,
                "ProtocolType": "MCP",
                "ProtocolConfiguration": {"Mcp": {"SupportedVersions": ["2025-11-25", "2026-07-28"]}},
                "ExceptionLevel": "DEBUG",
            },
        )

        # Gateway targets are deliberately explicit. This is the easiest place to inspect
        # exactly which tools an agent can see.
        sessions_schema = [
            {"Name": "get_item", "Description": "Retrieve an item from a DynamoDB table by its key", "InputSchema": {"Type": "object", "Properties": {"table_name": {"Type": "string"}, "key": {"Type": "object"}}, "Required": ["table_name", "key"]}},
            {"Name": "put_item", "Description": "Save or update an item in a DynamoDB table", "InputSchema": {"Type": "object", "Properties": {"table_name": {"Type": "string"}, "item": {"Type": "object"}}, "Required": ["table_name", "item"]}},
            {"Name": "lookup_customer", "Description": "Verify a customer using their SA ID number and link the verification to the current session", "InputSchema": {"Type": "object", "Properties": {"sessionId": {"Type": "string"}, "idNumber": {"Type": "string"}}, "Required": ["idNumber", "sessionId"]}},
            {"Name": "get_accounts", "Description": "Retrieve all accounts belonging to a verified customer", "InputSchema": {"Type": "object", "Properties": {"customerId": {"Type": "string"}}, "Required": ["customerId"]}},
            {"Name": "get_transactions", "Description": "Retrieve recent transactions for a specific account", "InputSchema": {"Type": "object", "Properties": {"accountId": {"Type": "string"}, "limit": {"Type": "integer"}}, "Required": ["accountId"]}},
            {"Name": "check_documents_status", "Description": "Check whether required documents are uploaded", "InputSchema": {"Type": "object", "Properties": {"sessionId": {"Type": "string"}}, "Required": ["sessionId"]}},
            {"Name": "save_applicant_info", "Description": "Save applicant name, phone and email for banker review", "InputSchema": {"Type": "object", "Properties": {"phone": {"Type": "string"}, "name": {"Type": "string"}, "sessionId": {"Type": "string"}, "email": {"Type": "string"}}, "Required": ["sessionId", "name", "phone", "email"]}},
            {"Name": "get_transaction_chart", "Description": "Generate spending-by-category chart data", "InputSchema": {"Type": "object", "Properties": {"accountId": {"Type": "string"}, "sessionId": {"Type": "string"}}, "Required": ["accountId", "sessionId"]}},
        ]
        credit_schema = [
            {"Name": "get_loan_products", "Description": "Returns available BlueBlood Bank loan products including rates and terms in ZAR", "InputSchema": {"Type": "object", "Properties": {}}},
            {"Name": "get_credit_score", "Description": "Returns the BlueBlood Bank internal credit score and rating for a customer", "InputSchema": {"Type": "object", "Properties": {"customer_id": {"Type": "string"}}, "Required": ["customer_id"]}},
            {"Name": "check_credit_eligibility", "Description": "Checks eligibility for a loan based on credit score and account balance", "InputSchema": {"Type": "object", "Properties": {"loan_amount": {"Type": "number"}, "customer_id": {"Type": "string"}, "loan_type": {"Type": "string"}}, "Required": ["customer_id", "loan_amount"]}},
            {"Name": "calculate_repayment", "Description": "Calculates monthly loan repayment using standard amortisation", "InputSchema": {"Type": "object", "Properties": {"term_months": {"Type": "integer"}, "interest_rate_percent": {"Type": "number"}, "loan_amount": {"Type": "number"}}, "Required": ["loan_amount", "term_months"]}},
        ]

        def lambda_target(logical_id, name, function, schema):
            target = CfnResource(
                self,
                logical_id,
                type="AWS::BedrockAgentCore::GatewayTarget",
                properties={
                    "GatewayIdentifier": gateway.ref,
                    "Name": name,
                    "CredentialProviderConfigurations": [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
                    "TargetConfiguration": {"Mcp": {"Lambda": {"LambdaArn": function.function_arn, "ToolSchema": {"InlinePayload": schema}}}},
                },
            )
            target.add_dependency(gateway)
            return target

        for fn in (api_stack.sessions_fn, api_stack.dynamodb_tool_fn, api_stack.credit_fn):
            fn.add_permission(
                "AllowAgentCoreGateway",
                principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
                action="lambda:InvokeFunction",
                source_arn=gateway.attr_arn,
            )

        sessions_target = lambda_target("SessionsTarget", "dynamodb-bluey-sessions-tool", api_stack.sessions_fn, sessions_schema)
        credit_target = lambda_target("CreditTarget", "bluey-credit-tool", api_stack.credit_fn, credit_schema)

        def kb_target(logical_id, name, kb_id, retrieval=False):
            params = {"knowledgeBaseId": kb_id}
            if retrieval:
                params = {"retrievalConfiguration": {"managedSearchConfiguration": {"numberOfResults": 5}}, "knowledgeBaseId": kb_id}
            target = CfnResource(
                self,
                logical_id,
                type="AWS::BedrockAgentCore::GatewayTarget",
                properties={
                    "GatewayIdentifier": gateway.ref,
                    "Name": name,
                    "CredentialProviderConfigurations": [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
                    "TargetConfiguration": {"Mcp": {"Connector": {"Source": {"ConnectorId": "bedrock-knowledge-bases", "Version": "1.0.0"}, "Configurations": [{"Name": "Retrieve", "ParameterValues": params}]}}},
                },
            )
            target.add_dependency(gateway)
            return target

        kb_target("BlueBloodKnowledgeTarget", "bluey-kb-blueblood", knowledge_stack.main_kb.ref, retrieval=True)
        kb_target("CreditKnowledgeTarget", "bluey-kb-credit", knowledge_stack.credit_kb.ref)

        # The Harness execution role is the caller of InvokeGateway.
        harness_role = iam.Role(
            self,
            "HarnessExecutionRole",
            role_name=f"bluey-harness-role-{stage}",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        harness_role.add_to_policy(
            _policy(
                ["bedrock-agentcore:InvokeGateway"],
                [gateway.attr_arn],
                "InvokeBlueyGateway",
            )
        )

        prompts = {
            "main": (Path(__file__).parents[2] / "config/prompts/main.txt").read_text(encoding="utf-8"),
            "credit": (Path(__file__).parents[2] / "config/prompts/credit.txt").read_text(encoding="utf-8"),
            "account-opening": (Path(__file__).parents[2] / "config/prompts/account-opening.txt").read_text(encoding="utf-8"),
        }

        def runtime(logical_id, name):
            return CfnResource(
                self,
                logical_id,
                type="AWS::BedrockAgentCore::Runtime",
                properties={
                    "AgentRuntimeName": name,
                    "AgentRuntimeArtifact": {"ContainerConfiguration": {"ContainerUri": HARNESS_IMAGE}},
                    "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
                    "RoleArn": harness_role.role_arn,
                    "EnvironmentVariables": {
                        "AWS_REGION": self.region,
                        "AWS_STAGE": stage,
                        "AWS_TRUNCATION_MESSAGES_COUNT": "150",
                        "AWS_TRUNCATION_STRATEGY": "sliding_window",
                    },
                    "LifecycleConfiguration": {"IdleRuntimeSessionTimeout": 900, "MaxLifetime": 28800},
                },
            )

        runtimes = {
            "main": runtime("MainRuntime", f"bluey_main_{stage}"),
            "credit": runtime("CreditRuntime", f"bluey_credit_{stage}"),
            "account-opening": runtime("AccountOpeningRuntime", f"bluey_account_opening_{stage}"),
        }

        def harness(logical_id, name, prompt, runtime_resource, memory_name, model_id):
            h = CfnResource(
                self,
                logical_id,
                type="AWS::BedrockAgentCore::Harness",
                properties={
                    "HarnessName": name,
                    "ExecutionRoleArn": harness_role.role_arn,
                    "Model": {"BedrockModelConfig": {"ModelId": model_id, "ApiFormat": "converse_stream"}},
                    "SystemPrompt": [{"Text": prompt}],
                    "Tools": [{"Type": "agentcore_gateway", "Name": "bluey-gateway", "Config": {"AgentCoreGateway": {"GatewayArn": gateway.attr_arn, "OutboundAuth": {"AwsIam": {}}}}}],
                    "AllowedTools": ["*"],
                    "TimeoutSeconds": 3600,
                    "MaxIterations": 75,
                    "Truncation": {"Strategy": "sliding_window", "Config": {"SlidingWindow": {"MessagesCount": 150}}},
                    "Environment": {"AgentCoreRuntimeEnvironment": {"AgentRuntimeArn": runtime_resource.attr_arn, "AgentRuntimeName": name, "AgentRuntimeId": runtime_resource.ref, "LifecycleConfiguration": {"IdleRuntimeSessionTimeout": 900, "MaxLifetime": 28800}, "NetworkConfiguration": {"NetworkMode": "PUBLIC", "FilesystemConfigurations": []}}},
                    "Memory": {"ManagedMemoryConfiguration": {"EventExpiryDuration": 30, "Strategies": ["SEMANTIC", "SUMMARIZATION"]}},
                },
            )
            h.add_dependency(gateway)
            h.add_dependency(runtime_resource)
            return h

        main_harness = harness("MainHarness", f"bluey_main_{stage}", prompts["main"], runtimes["main"], "bluey-main-memory", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
        credit_harness = harness("CreditHarness", f"bluey_credit_{stage}", prompts["credit"], runtimes["credit"], "bluey-credit-memory", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        account_harness = harness("AccountOpeningHarness", f"bluey_account_opening_{stage}", prompts["account-opening"], runtimes["account-opening"], "bluey-account-opening-memory", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

        # Lambda proxies consume the generated Harness ARNs through SSM. This avoids a
        # circular CloudFormation dependency between API and AgentCore stacks.
        main_param = ssm.StringParameter(self, "MainHarnessArn", parameter_name=f"/bluey/{stage}/agentcore/main-harness-arn", string_value=main_harness.attr_arn)
        credit_param = ssm.StringParameter(self, "CreditHarnessArn", parameter_name=f"/bluey/{stage}/agentcore/credit-harness-arn", string_value=credit_harness.attr_arn)
        account_param = ssm.StringParameter(self, "AccountOpeningHarnessArn", parameter_name=f"/bluey/{stage}/agentcore/account-opening-harness-arn", string_value=account_harness.attr_arn)
        for role in (api_stack.chat_role, api_stack.account_opening_role):
            role.add_to_policy(_policy(["ssm:GetParameter"], [main_param.parameter_arn, account_param.parameter_arn], "ReadHarnessParameters"))
        api_stack.chat_fn.add_environment("HARNESS_PARAMETER_NAME", main_param.parameter_name)
        api_stack.account_opening_fn.add_environment("HARNESS_PARAMETER_NAME", account_param.parameter_name)

        self.gateway = gateway
        self.gateway_role = gateway_role
        self.harness_role = harness_role
        self.main_harness = main_harness
        self.credit_harness = credit_harness
        self.account_harness = account_harness
        self.main_harness_arn = main_harness.attr_arn
        self.account_harness_arn = account_harness.attr_arn

        from aws_cdk import CfnOutput
        CfnOutput(self, "GatewayArn", value=gateway.attr_arn)
        CfnOutput(self, "MainHarnessArn", value=main_harness.attr_arn)
        CfnOutput(self, "CreditHarnessArn", value=credit_harness.attr_arn)
        CfnOutput(self, "AccountOpeningHarnessArn", value=account_harness.attr_arn)
