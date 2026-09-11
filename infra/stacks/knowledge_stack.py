"""Bluey managed Bedrock Knowledge Bases and their S3 source.

Uses the real typed CfnKnowledgeBase/CfnDataSource constructs (available
in aws-cdk-lib as of this repo's CDK version) rather than the raw
CfnResource escape hatch — this gives correctly-typed .attr_knowledge_base_arn
accessors instead of a nonexistent .attr_arn, and CDK validates the
snake_case-to-CloudFormation-property mapping at synth time.
"""
from pathlib import Path

from aws_cdk import Stack, RemovalPolicy, aws_iam as iam, aws_s3 as s3, aws_s3_deployment as s3deploy
from aws_cdk import aws_bedrock as bedrock
from constructs import Construct


class BlueyKnowledgeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.bucket = s3.Bucket(
            self, "KnowledgeBucket",
            bucket_name=None,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        content_root = Path(__file__).parents[2] / "knowledge_base"
        for logical_id, folder, prefix in (
            ("MainKnowledgeContent", "main", "main/"),
            ("CreditKnowledgeContent", "credit", "credit/"),
            ("AccountOpeningKnowledgeContent", "account-opening", "account-opening/"),
        ):
            s3deploy.BucketDeployment(
                self,
                logical_id,
                sources=[s3deploy.Source.asset(str(content_root / folder))],
                destination_bucket=self.bucket,
                destination_key_prefix=prefix,
                prune=False,
            )

        self.role = iam.Role(
            self, "KnowledgeBaseRole",
            role_name=f"bluey-knowledge-base-role-{stage}",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
        )
        self.bucket.grant_read(self.role)
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0"],
        ))

        def make_kb(logical_id: str, name: str, prefix: str):
            kb = bedrock.CfnKnowledgeBase(
                self, logical_id,
                name=name,
                role_arn=self.role.role_arn,
                knowledge_base_configuration={
                    "type": "MANAGED",
                    "managedKnowledgeBaseConfiguration": {
                        # With a MANAGED knowledge base, Bedrock selects and
                        # operates the embedding model itself. Passing an
                        # explicit embeddingModelArn here is rejected with
                        # "embeddingModelArn must not be specified when
                        # embeddingModelType is MANAGED", so only the type is
                        # set. The KnowledgeBaseRole still grants InvokeModel
                        # on Titan v2, which remains harmless.
                        "embeddingModelType": "MANAGED",
                    },
                },
            )
            ds = bedrock.CfnDataSource(
                self, logical_id + "DataSource",
                knowledge_base_id=kb.attr_knowledge_base_id,
                name=f"{name}-source",
                data_source_configuration={
                    "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                    "managedKnowledgeBaseConnectorConfiguration": {
                        "connectorParameters": {
                            "type": "S3",
                            "filterConfiguration": {"maxFileSizeInMegaBytes": "10240", "inclusionPrefixes": [prefix]},
                            "connectionConfiguration": {
                                "bucketName": self.bucket.bucket_name,
                                "bucketOwnerAccountId": self.account,
                                "bucketArn": self.bucket.bucket_arn,
                            },
                            "aclEnabled": False,
                            "version": "1",
                        },
                        "mediaExtractionConfiguration": {
                            "imageExtractionConfiguration": {"imageExtractionStatus": "ENABLED"},
                            "audioExtractionConfiguration": {"audioExtractionStatus": "ENABLED"},
                            "videoExtractionConfiguration": {"videoExtractionStatus": "ENABLED"},
                        },
                    },
                },
                vector_ingestion_configuration={"parsingConfiguration": {"parsingStrategy": "SMART_PARSING"}},
                data_deletion_policy="DELETE",
            )
            ds.add_dependency(kb)
            ds.node.add_dependency(self.bucket)
            return kb, ds

        # Keep document domains in explicit S3 prefixes. The main prefix is
        # divided into policy/, faqs/, and product-catalogue/ folders.
        self.main_kb, self.main_source = make_kb("MainKnowledgeBase", f"bluey-knowledge-{stage}", "main/")
        self.credit_kb, self.credit_source = make_kb("CreditKnowledgeBase", f"bluey-credit-knowledge-{stage}", "credit/")
        self.account_opening_kb, self.account_opening_source = make_kb(
            "AccountOpeningKnowledgeBase", f"bluey-account-opening-knowledge-{stage}", "account-opening/"
        )

        from aws_cdk import CfnOutput
        CfnOutput(self, "KnowledgeBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "MainKnowledgeBaseId", value=self.main_kb.attr_knowledge_base_id)
        CfnOutput(self, "CreditKnowledgeBaseId", value=self.credit_kb.attr_knowledge_base_id)
        CfnOutput(self, "AccountOpeningKnowledgeBaseId", value=self.account_opening_kb.attr_knowledge_base_id)
        CfnOutput(self, "MainDataSourceId", value=self.main_source.attr_data_source_id)
        CfnOutput(self, "CreditDataSourceId", value=self.credit_source.attr_data_source_id)
        CfnOutput(self, "AccountOpeningDataSourceId", value=self.account_opening_source.attr_data_source_id)
