from aws_cdk import (
    Duration,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from constructs import Construct


class BlueyApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, data_stack, auth_stack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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
                function_name=name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=lambda_.Code.from_asset(str(__import__("pathlib").Path(__file__).parents[2] / (code_path or f"lambda/{name}"))),
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
                    "HARNESS_ARN": "",
                    "HARNESS_PARAMETER_NAME": "",
                },
            )

        banker_role = execution_role("bluey-banker-api-role")
        document_role = execution_role("bluey-document-api-role")
        tool_role = execution_role("bluey-dynamodb-tool-role")
        chat_role = execution_role("bluey-chat-proxy-role")
        account_opening_role = execution_role("bluey-account-opening-proxy-role")

        # Banker API: sessions/customers/documents + private document reads.
        data_stack.tables["sessions"].grant_read_write_data(banker_role)
        data_stack.tables["customers"].grant_read_data(banker_role)
        data_stack.tables["documents"].grant_read_data(banker_role)
        data_stack.documents_bucket.grant_read(banker_role)

        # Document API: private upload/read + metadata and review flag.
        data_stack.documents_bucket.grant_read_write(document_role)
        data_stack.tables["documents"].grant_read_write_data(document_role)
        data_stack.tables["sessions"].grant_write_data(document_role)

        # Shared Gateway tool role: least privilege across Bluey data tables.
        for key in ("sessions", "customers", "accounts", "transactions", "documents", "credit"):
            data_stack.tables[key].grant_read_write_data(tool_role)
        # The tool Lambda itself never needs S3.

        # Harness proxies: session storage + Cognito token validation + AgentCore invocation.
        data_stack.tables["sessions"].grant_read_write_data(chat_role)
        data_stack.tables["sessions"].grant_read_write_data(account_opening_role)
        chat_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        account_opening_role.add_to_policy(iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"]))
        chat_role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"], resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:harness/bluey_main_*" ]))
        account_opening_role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeHarness"], resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:harness/bluey_account_opening_*" ]))

        self.chat_role = chat_role
        self.account_opening_role = account_opening_role

        self.dynamodb_tool_fn = fn("bluey-dynamodb-tool", tool_role, timeout=30, code_path="lambda/dynamodb_tool")
        self.sessions_fn = fn("bluey-sessions", tool_role, timeout=3, code_path="lambda/dynamodb_tool")
        self.credit_fn = fn("bluey-credit-api", execution_role("bluey-credit-api-role"), timeout=3, code_path="lambda/credit_api")
        data_stack.credit_data_bucket.grant_read(self.credit_fn.role)
        self.credit_fn.add_environment("CREDIT_DATA_BUCKET", data_stack.credit_data_bucket.bucket_name)
        self.chat_fn = fn("bluey-chat-proxy", chat_role, timeout=90)
        self.account_opening_fn = fn("bluey-account-opening-proxy", account_opening_role, timeout=90)
        self.banker_fn = fn("bluey-banker-api", banker_role, timeout=30)
        self.document_fn = fn("bluey-document-api", document_role, timeout=30)

        # Harness-invoking Lambdas deliberately use Function URLs because the documented
        # AgentCore cold-start path can exceed HTTP API integration timeouts.
        self.chat_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
                allowed_headers=["content-type", "authorization"],
            ),
        )
        self.account_opening_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.POST],
                allowed_headers=["content-type", "authorization"],
            ),
        )

        api = apigw.HttpApi(self, "BlueyHttpApi", api_name="bluey-api")
        issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{auth_stack.user_pool.user_pool_id}"
        jwt_authorizer = authorizers.HttpJwtAuthorizer(
            "BlueyJwtAuthorizer",
            issuer=issuer,
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

        from aws_cdk import CfnOutput
        CfnOutput(self, "HttpApiUrl", value=api.api_endpoint)
        CfnOutput(self, "ChatFunctionUrl", value=self.chat_fn.function_url)
        CfnOutput(self, "AccountOpeningFunctionUrl", value=self.account_opening_fn.function_url)
