"""Grounded Knowledge Base retrieval for AgentCore Gateway.

This Lambda deliberately calls the Bedrock Knowledge Base Runtime API directly.
It avoids the AgentCore managed-KB connector's serialization of
managedSearchConfiguration.numberOfResults as a string.
"""

import os

import boto3


REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
KB_ID = os.environ["KNOWLEDGE_BASE_ID"]

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


def _number_of_results(event: dict) -> int:
    retrieval = event.get("retrievalConfiguration") or {}
    managed = retrieval.get("managedSearchConfiguration") or {}
    value = managed.get("numberOfResults", event.get("numberOfResults", 5))
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, 100))


def lambda_handler(event, context):
    query = event.get("retrievalQuery") or {}
    text = query.get("text") or event.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"error": "retrievalQuery.text is required"}

    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": text.strip()},
        retrievalConfiguration={
            "managedSearchConfiguration": {
                "numberOfResults": _number_of_results(event),
            }
        },
    )

    results = []
    for item in response.get("retrievalResults", []):
        results.append(
            {
                "content": item.get("content", {}),
                "location": item.get("location"),
                "score": item.get("score"),
                "metadata": item.get("metadata", {}),
            }
        )

    return {"result": {"retrievalResults": results}}
