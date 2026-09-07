from aws_cdk import Stack, RemovalPolicy, aws_dynamodb as dynamodb, aws_iam as iam, aws_s3 as s3
from constructs import Construct


class BlueyDataStack(Stack):
    """Durable Bluey data resources.

    Tables are created without seed data. Seed data belongs in scripts/seed_data.py so that
    infrastructure deployment and demo-data loading remain separate, repeatable operations.
    """

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.tables: dict[str, dynamodb.Table] = {}

        def table(name: str, pk: str, sk: str | None = None) -> dynamodb.Table:
            kwargs = dict(
                table_name=name,
                partition_key=dynamodb.Attribute(name=pk, type=dynamodb.AttributeType.STRING),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.RETAIN if stage != "dev" else RemovalPolicy.DESTROY,
            )
            if sk:
                kwargs["sort_key"] = dynamodb.Attribute(name=sk, type=dynamodb.AttributeType.STRING)
            return dynamodb.Table(self, name.replace("-", ""), **kwargs)

        self.tables["sessions"] = table("bluey-sessions", "sessionId")
        self.tables["customers"] = table("bluey-customers", "customerId")
        self.tables["accounts"] = table("bluey-accounts", "customerId", "accountId")
        self.tables["transactions"] = table("bluey-transactions", "accountId", "date#transactionId")
        self.tables["documents"] = table("bluey-documents", "sessionId", "docType")
        self.tables["applications"] = table("bluey-applications", "reference")
        self.tables["bankers"] = table("bluey-bankers", "bankerId")
        self.tables["credit"] = table("bluey-credit", "customerId", "sessionId")
        self.tables["messages"] = table("bluey-messages", "sessionId", "createdAt#messageId")

        self.credit_data_bucket = s3.Bucket(
            self, "CreditDataBucket",
            bucket_name=None, block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True, versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.documents_bucket = s3.Bucket(
            self,
            "BlueyDocumentsBucket",
            bucket_name=None,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.document_api_role = iam.Role(
            self,
            "DocumentApiRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
        )
        self.documents_bucket.grant_read_write(self.document_api_role)
        self.tables["documents"].grant_read_write_data(self.document_api_role)
        self.tables["sessions"].grant_write_data(self.document_api_role)
