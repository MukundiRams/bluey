#!/usr/bin/env python3
"""Verify each deployed Bluey Managed Knowledge Base can be queried directly."""

import argparse

import boto3


def stack_output(cloudformation, stack_name: str, key: str) -> str:
    response = cloudformation.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    for output in outputs:
        if output["OutputKey"] == key:
            return output["OutputValue"]
    raise RuntimeError(f"{key} was not found in {stack_name}")


def retrieve(runtime, knowledge_base_id: str, query: str) -> dict:
    return runtime.retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "managedSearchConfiguration": {
                "numberOfResults": 5,
            }
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--query",
        default="What credit products are available?",
    )
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    cloudformation = session.client("cloudformation")
    runtime = session.client("bedrock-agent-runtime")
    stack = f"BlueyKnowledge-{args.stage}"

    kb_outputs = {
        "main": "MainKnowledgeBaseId",
        "credit": "CreditKnowledgeBaseId",
        "account-opening": "AccountOpeningKnowledgeBaseId",
    }

    for name, output_key in kb_outputs.items():
        kb_id = stack_output(cloudformation, stack, output_key)
        response = retrieve(runtime, kb_id, args.query)
        results = response.get("retrievalResults", [])
        print(f"{name}: {len(results)} retrieval results")
        if not results:
            raise RuntimeError(f"{name} returned no retrieval results")
        for result in results[:3]:
            print(f"  score={result.get('score')} content={result.get('content', {}).get('text', '')[:160]}")


if __name__ == "__main__":
    main()
