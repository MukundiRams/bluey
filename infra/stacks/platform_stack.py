"""BlueyPlatformStack — merged API + AgentCore infrastructure.

Originally two separate stacks (BlueyApiStack, BlueyAgentCoreStack).
Merged because they had a genuine circular STACK dependency: the API
stack's harness-invoking Lambdas need the Harness ARN (created in the
AgentCore stack), while the AgentCore stack's Gateway role needs
lambda:InvokeFunction on Lambdas created in the API stack, and the
Gateway-invoking Lambda's resource policy needs the Gateway's ARN.
CloudFormation cannot deploy two stacks that depend on each other in
both directions — `cdk synth` fails outright with a DependencyCycle
error, not a warning.

Within ONE stack this isn't a problem: CloudFormation can order
individual *resources* around each other just fine even with these
references going both directions: it's only the stack-level boundary
that turns it into an unsolvable cycle.

A useful side effect of merging: the SSM-parameter indirection the
original two-stack design needed (to pass the Harness ARN from one
stack to another without a direct cross-stack construct reference) is
no longer necessary at all — the harness-invoking Lambdas can now read
the real ARN directly via a same-stack environment variable
(HARNESS_ARN), and the existing Lambda code already supports that path
via `HARNESS_ARN = os.environ.get("HARNESS_ARN", "")`, so no Lambda
code changes were needed for this simplification.
"""

import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_bedrockagentcore as bedrockagentcore,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from constructs import Construct


HARNESS_IMAGE = "public.ecr.aws/i0n3d3i5/harness-us-east-1:latest"
HARNESS_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
HARNESS_REGION = "us-east-1"

# The Gateway API rejects an in-place searchType change while targets are
# attached ("Search type cannot be updated ... for a gateway with 1 or more
# targets"). First set BLUEY_ATTACH_TARGETS=0 and
# BLUEY_GATEWAY_SEARCH_TYPE=UNSET to detach targets without changing the
# Gateway. Then set BLUEY_GATEWAY_SEARCH_TYPE=SEMANTIC, and finally reattach
# targets in a third deployment.
ATTACH_GATEWAY_TARGETS = os.environ.get("BLUEY_ATTACH_TARGETS", "1") != "0"
GATEWAY_SEARCH_TYPE = os.environ.get("BLUEY_GATEWAY_SEARCH_TYPE", "SEMANTIC")


def _policy(actions, resources, sid):
    return iam.PolicyStatement(sid=sid, actions=actions, resources=resources)


class BlueyPlatformStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, data_stack, auth_stack, knowledge_stack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != HARNESS_REGION:
            raise ValueError(f"Bluey AgentCore Harnesses must be deployed in {HARNESS_REGION}, not {self.region}")

        # ---------------------------------------------------------------
        # Lambdas + IAM roles (formerly api_stack.py)
        # ---------------------------------------------------------------

        def execution_role(name: str) -> iam.Role:
            return iam.Role(
                self,
                name.replace("-", ""),
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )

        def fn(name: str, role: iam.Role, timeout: int = 30, code_path: str | None = None):
            return lambda_.Function(
                self,
                name.replace("-", ""),
                function_name=f"{name}-{stage}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=lambda_.Code.from_asset(str(Path(__file__).parents[2] / (code_path or f"lambda/{name}"))),
                timeout=Duration.seconds(timeout),
                role=role,
                environment={
                    "BLUEY_STAGE": stage,
                    "AWS_REGION_NAME": self.region,
                    "DOCUMENTS_BUCKET": data_stack.documents_bucket.bucket_name,
                    "SESSIONS_TABLE": data_stack.tables["sessions"].table_name,
                    "CUSTOMERS_TABLE": data_stack.tables["customers"].table_name,
                    "DOCUMENTS_TABLE": data_stack.tables["documents"].table_name,
                    "ACCOUNTS_TABLE": data_stack.tables["accounts"].table_name,
                    "TRANSACTIONS_TABLE": data_stack.tables["transactions"].table_name,
                    "CREDIT_TABLE": data_stack.tables["credit"].table_name,
                    "BANKERS_TABLE": data_stack.tables["bankers"].table_name,
                    "APPLICATIONS_TABLE": data_stack.tables["applications"].table_name,
                    "MESSAGES_TABLE": data_stack.tables["messages"].table_name,
                    # Set directly below once the Harness resources exist —
                    # left as "" here only as a placeholder default.
                    "HARNESS_ARN": "",
                    "HARNESS_PARAMETER_NAME": "",
                },
            )

        banker_role = execution_role("bluey-banker-api-role")
        document_role = execution_role("bluey-document-api-role")
        tool_role = execution_role("bluey-dynamodb-tool-role")
        chat_role = execution_role("bluey-chat-proxy-role")
        account_opening_role = execution_role("bluey-account-opening-proxy-role")
        credit_proxy_role = execution_role("bluey-credit-proxy-role")
        financial_advice_role = execution_role("bluey-financial-advice-proxy-role")
        router_role = execution_role("bluey-router-proxy-role")

        # Banker API: sessions/customers/documents/applications + private document reads.
        data_stack.tables["sessions"].grant_read_write_data(banker_role)
        data_stack.tables["customers"].grant_read_data(banker_role)
        data_stack.tables["documents"].grant_read_data(banker_role)
        data_stack.tables["applications"].grant_read_write_data(banker_role)
        data_stack.tables["accounts"].grant_read_data(banker_role)
        data_stack.tables["transactions"].grant_read_data(banker_role)
        data_stack.tables["credit"].grant_read_data(banker_role)
        data_stack.tables["bankers"].grant_read_data(banker_role)
        data_stack.tables["messages"].grant_read_data(banker_role)
        data_stack.tables["read_state"].grant_read_write_data(banker_role)
        data_stack.tables["assignments"].grant_read_write_data(banker_role)
        data_stack.documents_bucket.grant_read(banker_role)

        # Document API: private upload/read + metadata and review flag.
        data_stack.documents_bucket.grant_read_write(document_role)
        data_stack.tables["documents"].grant_read_write_data(document_role)
        data_stack.tables["sessions"].grant_write_data(document_role)
        data_stack.tables["applications"].grant_read_write_data(document_role)

        # Gateway tool role: access across all tables.
        for key in ("sessions", "customers", "accounts", "transactions", "documents", "credit", "bankers", "applications", "messages"):
            data_stack.tables[key].grant_read_write_data(tool_role)

        # Harness proxies: session storage + Cognito token validation + AgentCore invocation.
        for proxy_role in (chat_role, account_opening_role, credit_proxy_role, financial_advice_role, router_role):
            data_stack.tables["sessions"].grant_read_write_data(proxy_role)
            data_stack.tables["messages"].grant_read_write_data(proxy_role)
            data_stack.tables["applications"].grant_read_write_data(proxy_role)
        chat_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        account_opening_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        credit_proxy_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        financial_advice_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        router_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        # Router needs its own fast classifier model call, separate from the Harnesses' own model access.
        router_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                f"arn:aws:bedrock:{self.region}::foundation-model/*",
            ],
        ))

        self.dynamodb_tool_fn = fn("bluey-dynamodb-tool", tool_role, timeout=30, code_path="lambda/dynamodb_tool")
        self.sessions_fn = fn("bluey-sessions", tool_role, timeout=3, code_path="lambda/dynamodb_tool")
        self.credit_fn = fn("bluey-credit-api", execution_role("bluey-credit-api-role"), timeout=3, code_path="lambda/credit_api")
        data_stack.credit_data_bucket.grant_read(self.credit_fn.role)
        data_stack.tables["customers"].grant_read_data(self.credit_fn.role)
        data_stack.tables["accounts"].grant_read_data(self.credit_fn.role)
        self.credit_fn.add_environment("CREDIT_DATA_BUCKET", data_stack.credit_data_bucket.bucket_name)
        self.chat_fn = fn("bluey-chat-proxy", chat_role, timeout=90)
        self.account_opening_fn = fn("bluey-account-opening-proxy", account_opening_role, timeout=90)
        self.credit_proxy_fn = fn("bluey-credit-proxy", credit_proxy_role, timeout=90, code_path="lambda/bluey-credit-proxy")
        self.financial_advice_fn = fn("bluey-financial-advice-proxy", financial_advice_role, timeout=90)
        self.router_fn = fn("bluey-router-proxy", router_role, timeout=90)
        self.banker_fn = fn("bluey-banker-api", banker_role, timeout=30)
        self.banker_fn.add_environment("READ_STATE_TABLE", data_stack.tables["read_state"].table_name)
        self.banker_fn.add_environment("ASSIGNMENTS_TABLE", data_stack.tables["assignments"].table_name)
        self.document_fn = fn("bluey-document-api", document_role, timeout=30)

        # Read-only accounts/transactions endpoint: role only has GetItem/Query/Scan
        # on those two tables, no write access and no access to any other table.
        readonly_data_role = execution_role("bluey-readonly-data-api-role")
        data_stack.tables["accounts"].grant_read_data(readonly_data_role)
        data_stack.tables["transactions"].grant_read_data(readonly_data_role)
        self.readonly_data_fn = fn("bluey-readonly-data-api", readonly_data_role, timeout=10, code_path="lambda/bluey-readonly-data-api")
        self.readonly_data_function_url = self.readonly_data_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.GET],
                allowed_headers=["content-type"],
            ),
        )

        # Harness-invoking Lambdas deliberately use Function URLs because the
        # documented AgentCore cold-start path can exceed HTTP API integration
        # timeouts (29s hard ceiling, harness cold starts observed up to ~60s).
        function_url_cors = lambda_.FunctionUrlCorsOptions(
            allowed_origins=["*"],
            allowed_methods=[lambda_.HttpMethod.POST],
            allowed_headers=["content-type", "authorization"],
        )
        self.chat_function_url = self.chat_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=function_url_cors,
        )
        self.account_opening_function_url = self.account_opening_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=function_url_cors,
        )
        self.credit_function_url = self.credit_proxy_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=function_url_cors,
        )
        self.financial_advice_function_url = self.financial_advice_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=function_url_cors,
        )
        self.router_function_url = self.router_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=function_url_cors,
        )

        api = apigw.HttpApi(
            self,
            "BlueyHttpApi",
            api_name=f"bluey-api-{stage}",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_headers=["authorization", "content-type"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
                max_age=Duration.hours(1),
            ),
        )
        issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{auth_stack.user_pool.user_pool_id}"
        jwt_authorizer = authorizers.HttpJwtAuthorizer(
            "BlueyJwtAuthorizer",
            issuer,
            jwt_audience=[auth_stack.spa_client.user_pool_client_id],
        )

        api.add_routes(
            path="/banker",
            methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("BankerIntegration", self.banker_fn),
            authorizer=jwt_authorizer,
        )
        api.add_routes(
            path="/documents",
            methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("DocumentIntegration", self.document_fn),
            authorizer=jwt_authorizer,
        )

        # ---------------------------------------------------------------
        # AgentCore: Gateway, Gateway targets, Runtimes, Harnesses
        # (formerly agentcore_stack.py)
        # ---------------------------------------------------------------

        sessions_schema = [
            {"name": "get_item", "description": "Retrieve an item from a DynamoDB table by its key", "inputSchema": {"type": "object", "properties": {"table_name": {"type": "string"}, "key": {"type": "object"}}, "required": ["table_name", "key"]}},
            {"name": "put_item", "description": "Save or update an item in a DynamoDB table", "inputSchema": {"type": "object", "properties": {"table_name": {"type": "string"}, "item": {"type": "object"}}, "required": ["table_name", "item"]}},
            {"name": "lookup_customer", "description": "Verify a customer using their SA ID number and link the verification to the current session", "inputSchema": {"type": "object", "properties": {"sessionId": {"type": "string"}, "idNumber": {"type": "string"}}, "required": ["idNumber", "sessionId"]}},
            {"name": "get_accounts", "description": "Retrieve all accounts belonging to a verified customer", "inputSchema": {"type": "object", "properties": {"customerId": {"type": "string"}}, "required": ["customerId"]}},
            {"name": "get_transactions", "description": "Retrieve recent transactions for a specific account", "inputSchema": {"type": "object", "properties": {"accountId": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["accountId"]}},
            {"name": "check_documents_status", "description": "Check whether required documents are uploaded", "inputSchema": {"type": "object", "properties": {"sessionId": {"type": "string"}}, "required": ["sessionId"]}},
            {"name": "save_applicant_info", "description": "Save applicant name, phone and email for banker review", "inputSchema": {"type": "object", "properties": {"phone": {"type": "string"}, "name": {"type": "string"}, "sessionId": {"type": "string"}, "email": {"type": "string"}}, "required": ["sessionId", "name", "phone", "email"]}},
            {"name": "get_transaction_chart", "description": "Generate a detailed spending breakdown (bar, pie, and time-series chart data plus a numeric summary) for one account or, given a customerId, aggregated across all of a customer's accounts", "inputSchema": {"type": "object", "properties": {"accountId": {"type": "string"}, "customerId": {"type": "string"}, "sessionId": {"type": "string"}}, "required": ["sessionId"]}},
        ]
        main_sessions_schema = [
            tool for tool in sessions_schema
            if tool["name"] in {"lookup_customer", "get_accounts", "get_transactions", "get_transaction_chart"}
        ]
        account_opening_schema = [
            tool for tool in sessions_schema
            if tool["name"] in {"check_documents_status", "save_applicant_info"}
        ]
        financial_advice_schema = [
            tool for tool in sessions_schema
            if tool["name"] in {"lookup_customer", "get_accounts", "get_transactions", "get_transaction_chart"}
        ]
        credit_schema = [
            {"name": "get_loan_products", "description": "Returns available Standard Bank loan products including rates and terms in ZAR", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "get_credit_score", "description": "Returns the Standard Bank internal credit score and rating for a customer", "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}},
            {"name": "check_credit_eligibility", "description": "Checks eligibility for a loan based on credit score and account balance", "inputSchema": {"type": "object", "properties": {"loan_amount": {"type": "number"}, "customer_id": {"type": "string"}, "loan_type": {"type": "string"}}, "required": ["customer_id", "loan_amount"]}},
            {"name": "calculate_repayment", "description": "Calculates monthly loan repayment using standard amortisation", "inputSchema": {"type": "object", "properties": {"term_months": {"type": "integer"}, "interest_rate_percent": {"type": "number"}, "loan_amount": {"type": "number"}}, "required": ["loan_amount", "term_months"]}},
            {"name": "recommend_products", "description": "Provides guidance-only Standard Bank product recommendations from a customer's profile and recommendation segment", "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "string"}, "top_n": {"type": "integer"}}, "required": ["customer_id"]}},
        ]

        def make_gateway(logical_id, name, description, lambda_arns, knowledge_base_arns):
            role = iam.Role(
                self,
                logical_id + "Role",
                role_name=f"bluey-agentcore-{name}-role-{stage}",
                assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            )
            role.add_to_policy(_policy(["lambda:InvokeFunction"], lambda_arns, "InvokeAllowedLambdaTargets"))
            role.add_to_policy(
                _policy(
                    ["bedrock:GetKnowledgeBase", "bedrock:Retrieve"],
                    knowledge_base_arns,
                    "ReadAllowedKnowledgeBase",
                )
            )
            role.add_to_policy(_policy(["bedrock:AgenticRetrieveStream"], ["*"], "AgenticRetrieve"))
            gateway = bedrockagentcore.CfnGateway(
                self,
                logical_id,
                name=f"bluey-gateway-{name}-{stage}",
                description=description,
                authorizer_type="AWS_IAM",
                role_arn=role.role_arn,
                protocol_type="MCP",
                protocol_configuration={
                    "mcp": {
                        "supportedVersions": ["2025-11-25", "2026-07-28"],
                        **(
                            {"searchType": GATEWAY_SEARCH_TYPE}
                            if GATEWAY_SEARCH_TYPE != "UNSET"
                            else {}
                        ),
                    }
                },
                exception_level="DEBUG",
            )
            return gateway, role

        main_gateway, main_gateway_role = make_gateway(
            "MainGateway",
            "main",
            "Bluey main banking tools and policy, FAQ, and product knowledge",
            [self.sessions_fn.function_arn],
            [knowledge_stack.main_kb.attr_knowledge_base_arn],
        )
        credit_gateway, credit_gateway_role = make_gateway(
            "CreditGateway",
            "credit",
            "Bluey credit tools and approved credit knowledge",
            [self.credit_fn.function_arn],
            [knowledge_stack.credit_kb.attr_knowledge_base_arn],
        )
        account_opening_gateway, account_opening_gateway_role = make_gateway(
            "AccountOpeningGateway",
            "account-opening",
            "Bluey account-opening tools and document knowledge",
            [self.sessions_fn.function_arn],
            [knowledge_stack.account_opening_kb.attr_knowledge_base_arn],
        )
        financial_advice_gateway, financial_advice_gateway_role = make_gateway(
            "FinancialAdviceGateway",
            "financial-advice",
            "Bluey financial wellness tools and responsible advice knowledge",
            [self.sessions_fn.function_arn],
            [knowledge_stack.main_kb.attr_knowledge_base_arn],
        )

        def _depend_on_role_policy(target, role):
            # AgentCore validates the gateway execution role's access to the
            # target resource at target-creation time. The role's inline
            # policy (its DefaultPolicy) must therefore exist and have
            # propagated in IAM before the target is created, otherwise the
            # target fails to stabilize with "Insufficient permissions to
            # validate the specified resource". CfnGatewayTarget only depends
            # on the gateway by default, not on the role policy, so add that
            # dependency explicitly here.
            default_policy = role.node.try_find_child("DefaultPolicy")
            if default_policy is not None:
                target.add_dependency(default_policy.node.default_child)

        def lambda_target(logical_id, name, function, schema, gateway, role, lambda_permission=None):
            target = bedrockagentcore.CfnGatewayTarget(
                self,
                logical_id,
                gateway_identifier=gateway.attr_gateway_identifier,
                name=name,
                credential_provider_configurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
                target_configuration={"mcp": {"lambda": {"lambdaArn": function.function_arn, "toolSchema": {"inlinePayload": schema}}}},
            )
            target.add_dependency(gateway)
            _depend_on_role_policy(target, role)
            # The Lambda resource-based permission granting the gateway
            # principal lambda:InvokeFunction must also exist before the
            # target validates, or validation sees no invoke permission.
            if lambda_permission is not None:
                target.add_dependency(lambda_permission)
            return target

        main_gateway_permission = self.sessions_fn.add_permission(
            "AllowMainGateway",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=main_gateway.attr_gateway_arn,
        )
        account_opening_gateway_permission = self.sessions_fn.add_permission(
            "AllowAccountOpeningGateway",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=account_opening_gateway.attr_gateway_arn,
        )
        financial_advice_gateway_permission = self.sessions_fn.add_permission(
            "AllowFinancialAdviceGateway",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=financial_advice_gateway.attr_gateway_arn,
        )
        credit_gateway_permission = self.credit_fn.add_permission(
            "AllowCreditGateway",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=credit_gateway.attr_gateway_arn,
        )

        if ATTACH_GATEWAY_TARGETS:
            lambda_target("MainSessionsTarget", "dynamodb-bluey-sessions-tool", self.sessions_fn, main_sessions_schema, main_gateway, main_gateway_role, main_gateway_permission)
            lambda_target("AccountOpeningSessionsTarget", "dynamodb-bluey-account-opening-tool", self.sessions_fn, account_opening_schema, account_opening_gateway, account_opening_gateway_role, account_opening_gateway_permission)
            lambda_target("CreditTarget", "bluey-credit-tool", self.credit_fn, credit_schema, credit_gateway, credit_gateway_role, credit_gateway_permission)
            lambda_target("FinancialAdviceTarget", "dynamodb-bluey-financial-advice-tool", self.sessions_fn, financial_advice_schema, financial_advice_gateway, financial_advice_gateway_role, financial_advice_gateway_permission)

        def kb_target(logical_id, name, kb_id, gateway, role):
            # numberOfResults is intentionally omitted here: the connector was
            # still exposing it to the agent as an overridable parameter (sent
            # back as a string), which the Bedrock Retrieve API rejects with a
            # numberOfResults-must-be-integer validation error. Leaving it out
            # lets the Retrieve API fall back to its own default.
            params = {"knowledgeBaseId": kb_id}
            target = bedrockagentcore.CfnGatewayTarget(
                self,
                logical_id,
                gateway_identifier=gateway.attr_gateway_identifier,
                name=name,
                credential_provider_configurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
                target_configuration={"mcp": {"connector": {"source": {"connectorId": "bedrock-knowledge-bases"}, "configurations": [{
                    "name": "Retrieve",
                    "description": "Search the knowledge base for relevant documents.",
                    "parameterValues": params,
                    "parameterOverrides": [
                        {
                            "path": "$.retrievalQuery.text",
                            "description": "The search query string, e.g. {\"retrievalQuery\": {\"text\": \"your query\"}}.",
                            "visible": True
                        }
                    ],
                }]}}},
            )

            target.add_dependency(gateway)
            # Same IAM-propagation ordering requirement as the Lambda targets:
            # the gateway role must be able to read the Knowledge Base before
            # AgentCore validates this target.
            _depend_on_role_policy(target, role)
            return target

        if ATTACH_GATEWAY_TARGETS:
            kb_target("StandardBankKnowledgeTarget", "bluey-kb-standard-bank", knowledge_stack.main_kb.attr_knowledge_base_id, main_gateway, main_gateway_role)
            kb_target("CreditKnowledgeTarget", "bluey-kb-credit", knowledge_stack.credit_kb.attr_knowledge_base_id, credit_gateway, credit_gateway_role)
            kb_target(
                "AccountOpeningKnowledgeTarget",
                "bluey-kb-account-opening",
                knowledge_stack.account_opening_kb.attr_knowledge_base_id,
                account_opening_gateway,
                account_opening_gateway_role,
            )
            kb_target(
                "FinancialAdviceKnowledgeTarget",
                "bluey-kb-financial-advice",
                knowledge_stack.main_kb.attr_knowledge_base_id,
                financial_advice_gateway,
                financial_advice_gateway_role,
            )

        prompts = {
            "main": (Path(__file__).parents[2] / "config/prompts/main.txt").read_text(encoding="utf-8"),
            "credit": (Path(__file__).parents[2] / "config/prompts/credit.txt").read_text(encoding="utf-8"),
            "account-opening": (Path(__file__).parents[2] / "config/prompts/account-opening.txt").read_text(encoding="utf-8"),
            "financial-advice": (Path(__file__).parents[2] / "config/prompts/financial-advice.txt").read_text(encoding="utf-8"),
        }

        def harness_role(logical_id, name, gateway):
            role = iam.Role(
                self,
                logical_id,
                role_name=f"bluey-harness-{name}-role-{stage}",
                assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            )
            role.add_to_policy(
                _policy(
                    [
                        "ecr-public:GetAuthorizationToken",
                        "ecr-public:BatchCheckLayerAvailability",
                        "ecr-public:GetDownloadUrlForLayer",
                        "ecr-public:BatchGetImage",
                    ],
                    ["*"],
                    "PullHarnessImage",
                )
            )
            role.add_to_policy(
                _policy(
                    [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    [f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*"],
                    "WriteHarnessLogs",
                )
            )
            role.add_to_policy(
                _policy(
                    [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    [
                        f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                        f"arn:aws:bedrock:{self.region}::foundation-model/*",
                        f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "arn:aws:bedrock:::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                    ],
                    "InvokeHarnessModel",
                )
            )
            role.add_to_policy(
                _policy(["bedrock-agentcore:InvokeGateway"], [gateway.attr_gateway_arn], "InvokeAssignedGateway")
            )
            # Each harness is created with a managedMemoryConfiguration, so at
            # runtime it reads and writes its own AgentCore memory. Without
            # these actions the harness fails with AccessDeniedException on
            # bedrock-agentcore:ListEvents (and the related event actions).
            # The memory resource is created by the harness itself, so scope
            # to this account's agentcore memory resources rather than a
            # specific ARN (which would be a circular reference).
            role.add_to_policy(
                _policy(
                    [
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:GetEvent",
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListSessions",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:GetMemoryRecord",
                        "bedrock-agentcore:ListMemoryRecords",
                    ],
                    [f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*"],
                    "AccessHarnessMemory",
                )
            )
            return role

        main_harness_role = harness_role("MainHarnessRole", "main", main_gateway)
        credit_harness_role = harness_role("CreditHarnessRole", "credit", credit_gateway)
        account_opening_harness_role = harness_role(
            "AccountOpeningHarnessRole", "account-opening", account_opening_gateway
        )
        financial_advice_harness_role = harness_role(
            "FinancialAdviceHarnessRole", "financial-advice", financial_advice_gateway
        )

        def runtime(logical_id, name, role):
            return bedrockagentcore.CfnRuntime(
                self,
                logical_id,
                agent_runtime_name=name,
                agent_runtime_artifact={"containerConfiguration": {"containerUri": HARNESS_IMAGE}},
                network_configuration={"networkMode": "PUBLIC"},
                role_arn=role.role_arn,
                environment_variables={
                    "AWS_REGION": self.region,
                    "AWS_STAGE": stage,
                    "AWS_TRUNCATION_MESSAGES_COUNT": "150",
                    "AWS_TRUNCATION_STRATEGY": "sliding_window",
                },
                lifecycle_configuration={"idleRuntimeSessionTimeout": 900, "maxLifetime": 28800},
            )

        runtimes = {
            "main": runtime("MainRuntime", f"bluey_main_{stage}", main_harness_role),
            "credit": runtime("CreditRuntime", f"bluey_credit_{stage}", credit_harness_role),
            "account-opening": runtime(
                "AccountOpeningRuntime", f"bluey_account_opening_{stage}", account_opening_harness_role
            ),
            "financial-advice": runtime(
                "FinancialAdviceRuntime", f"bluey_financial_advice_{stage}", financial_advice_harness_role
            ),
        }

        def harness(logical_id, name, prompt, runtime_resource, model_id, role, gateway):
            h = bedrockagentcore.CfnHarness(
                self,
                logical_id,
                harness_name=name,
                execution_role_arn=role.role_arn,
                model={"bedrockModelConfig": {"modelId": model_id, "apiFormat": "converse_stream"}},
                system_prompt=[{"text": prompt}],
                tools=[{"type": "agentcore_gateway", "name": f"bluey-{name}-gateway", "config": {"agentCoreGateway": {"gatewayArn": gateway.attr_gateway_arn, "outboundAuth": {"awsIam": {}}}}}],
                allowed_tools=["*"],
                timeout_seconds=3600,
                max_iterations=75,
                truncation={"strategy": "sliding_window", "config": {"slidingWindow": {"messagesCount": 150}}},
                environment={"agentCoreRuntimeEnvironment": {"agentRuntimeArn": runtime_resource.attr_agent_runtime_arn, "agentRuntimeName": name, "agentRuntimeId": runtime_resource.attr_agent_runtime_id, "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900, "maxLifetime": 28800}, "networkConfiguration": {"networkMode": "PUBLIC", "filesystemConfigurations": []}}},
                memory={"managedMemoryConfiguration": {"eventExpiryDuration": 30, "strategies": ["SEMANTIC", "SUMMARIZATION"]}},
            )
            h.add_dependency(gateway)
            h.add_dependency(runtime_resource)
            return h

        main_harness = harness(
            "MainHarness", f"bluey_main_{stage}", prompts["main"], runtimes["main"],
            HARNESS_MODEL_ID, main_harness_role, main_gateway,
        )
        credit_harness = harness(
            "CreditHarness", f"bluey_credit_{stage}", prompts["credit"], runtimes["credit"],
            HARNESS_MODEL_ID, credit_harness_role, credit_gateway,
        )
        account_harness = harness(
            "AccountOpeningHarness", f"bluey_account_opening_{stage}", prompts["account-opening"],
            runtimes["account-opening"], HARNESS_MODEL_ID,
            account_opening_harness_role, account_opening_gateway,
        )
        financial_advice_harness = harness(
            "FinancialAdviceHarness", f"bluey_financial_advice_{stage}", prompts["financial-advice"],
            runtimes["financial-advice"], HARNESS_MODEL_ID,
            financial_advice_harness_role, financial_advice_gateway,
        )

        # AgentCore provisions managed memory in DynamoDB while each Harness
        # is created. Serializing creation avoids service-side transaction
        # conflicts when multiple Harnesses provision memory at once.
        credit_harness.add_dependency(main_harness)
        account_harness.add_dependency(credit_harness)
        financial_advice_harness.add_dependency(account_harness)

        # Harness invocation checks a runtime-endpoint subresource, so the
        # generated harness ARN must include a trailing wildcard.
        chat_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"],
            resources=[f"{main_harness.attr_arn}*"],
        ))
        account_opening_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"],
            resources=[f"{account_harness.attr_arn}*"],
        ))
        credit_proxy_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"],
            resources=[f"{credit_harness.attr_arn}*"],
        ))
        financial_advice_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"],
            resources=[f"{financial_advice_harness.attr_arn}*"],
        ))
        # Router proxy fans out to whichever Harness it classifies the message into.
        router_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"],
            resources=[
                f"{main_harness.attr_arn}*",
                f"{account_harness.attr_arn}*",
                f"{credit_harness.attr_arn}*",
                f"{financial_advice_harness.attr_arn}*",
            ],
        ))

        # Now that Harnesses exist, wire their ARNs directly into the proxy
        # Lambdas — same-stack reference, no SSM indirection needed.
        self.chat_fn.add_environment("HARNESS_ARN", main_harness.attr_arn)
        self.account_opening_fn.add_environment("HARNESS_ARN", account_harness.attr_arn)
        self.credit_proxy_fn.add_environment("HARNESS_ARN", credit_harness.attr_arn)
        self.financial_advice_fn.add_environment("HARNESS_ARN", financial_advice_harness.attr_arn)
        self.router_fn.add_environment("MAIN_HARNESS_ARN", main_harness.attr_arn)
        self.router_fn.add_environment("ACCOUNT_OPENING_HARNESS_ARN", account_harness.attr_arn)
        self.router_fn.add_environment("CREDIT_HARNESS_ARN", credit_harness.attr_arn)
        self.router_fn.add_environment("FINANCIAL_ADVICE_HARNESS_ARN", financial_advice_harness.attr_arn)

        self.gateways = {
            "main": main_gateway,
            "credit": credit_gateway,
            "account-opening": account_opening_gateway,
            "financial-advice": financial_advice_gateway,
        }
        self.main_harness = main_harness
        self.credit_harness = credit_harness
        self.account_harness = account_harness
        self.financial_advice_harness = financial_advice_harness

        CfnOutput(self, "HttpApiUrl", value=api.api_endpoint)
        CfnOutput(self, "BankerApiUrl", value=f"{api.api_endpoint}/banker")
        CfnOutput(self, "DocumentApiUrl", value=f"{api.api_endpoint}/documents")
        CfnOutput(self, "ChatFunctionUrl", value=self.chat_function_url.url)
        CfnOutput(self, "AccountOpeningFunctionUrl", value=self.account_opening_function_url.url)
        CfnOutput(self, "CreditFunctionUrl", value=self.credit_function_url.url)
        CfnOutput(self, "FinancialAdviceFunctionUrl", value=self.financial_advice_function_url.url)
        CfnOutput(self, "RouterFunctionUrl", value=self.router_function_url.url)
        CfnOutput(self, "ReadonlyDataFunctionUrl", value=self.readonly_data_function_url.url)
        CfnOutput(self, "MainGatewayArnOutput", value=main_gateway.attr_gateway_arn)
        CfnOutput(self, "CreditGatewayArnOutput", value=credit_gateway.attr_gateway_arn)
        CfnOutput(self, "AccountOpeningGatewayArnOutput", value=account_opening_gateway.attr_gateway_arn)
        CfnOutput(self, "FinancialAdviceGatewayArnOutput", value=financial_advice_gateway.attr_gateway_arn)
        # Backward-compatible alias for scripts or consumers that expected one
        # Gateway output before the per-Harness split.
        CfnOutput(self, "GatewayArnOutput", value=main_gateway.attr_gateway_arn)
        CfnOutput(self, "MainHarnessArnOutput", value=main_harness.attr_arn)
        CfnOutput(self, "CreditHarnessArnOutput", value=credit_harness.attr_arn)
        CfnOutput(self, "AccountOpeningHarnessArnOutput", value=account_harness.attr_arn)
        CfnOutput(self, "FinancialAdviceHarnessArnOutput", value=financial_advice_harness.attr_arn)
