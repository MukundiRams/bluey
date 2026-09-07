"""Bluey managed Bedrock Knowledge Bases and their S3 source."""
import json
from aws_cdk import Stack, RemovalPolicy, aws_iam as iam, aws_s3 as s3, CfnResource
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

        def make_kb(logical_id, name, prefix):
            kb = CfnResource(self, logical_id, type="AWS::Bedrock::KnowledgeBase", properties={
                "Name": name,
                "RoleArn": self.role.role_arn,
                "KnowledgeBaseConfiguration": {
                    "Type": "MANAGED",
                    "ManagedKnowledgeBaseConfiguration": {
                        "EmbeddingModelType": "MANAGED",
                        "EmbeddingModelArn": f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0",
                    },
                },
            })
            ds = CfnResource(self, logical_id + "DataSource", type="AWS::Bedrock::DataSource", properties={
                "KnowledgeBaseId": kb.ref,
                "Name": f"{name}-source",
                "DataSourceConfiguration": {
                    "Type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                    "ManagedKnowledgeBaseConnectorConfiguration": {
                        "ConnectorParameters": json.dumps({
                            "type": "S3",
                            "filterConfiguration": {"maxFileSizeInMegaBytes": "10240", "inclusionPrefixes": [prefix]},
                            "connectionConfiguration": {"bucketName": self.bucket.bucket_name, "bucketOwnerAccountId": self.account, "bucketArn": self.bucket.bucket_arn},
                            "aclEnabled": False,
                            "version": "1",
                        }),
                        "MediaExtractionConfiguration": {
                            "ImageExtractionConfiguration": {"ImageExtractionStatus": "ENABLED"},
                            "AudioExtractionConfiguration": {"AudioExtractionStatus": "ENABLED"},
                            "VideoExtractionConfiguration": {"VideoExtractionStatus": "ENABLED"},
                        },
                    },
                },
                "VectorIngestionConfiguration": {"ParsingConfiguration": {"ParsingStrategy": "SMART_PARSING"}},
                "DataDeletionPolicy": "DELETE",
            })
            ds.add_dependency(kb)
            ds.add_dependency(self.bucket.node.default_child)
            return kb, ds

        self.main_kb, self.main_source = make_kb("MainKnowledgeBase", f"bluey-knowledge-{stage}", "files/")
        self.credit_kb, self.credit_source = make_kb("CreditKnowledgeBase", f"bluey-credit-knowledge-{stage}", "Credit/")

        from aws_cdk import CfnOutput
        CfnOutput(self, "KnowledgeBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "MainKnowledgeBaseId", value=self.main_kb.ref)
        CfnOutput(self, "CreditKnowledgeBaseId", value=self.credit_kb.ref)
        CfnOutput(self, "MainDataSourceId", value=self.main_source.ref)
        CfnOutput(self, "CreditDataSourceId", value=self.credit_source.ref)
