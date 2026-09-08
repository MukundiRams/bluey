#!/usr/bin/env python3
"""Invoke a deployed Bluey Harness directly and print its response."""
import argparse
import uuid

import boto3


def harness_arn(region, stage, harness):
    cloudformation = boto3.client("cloudformation", region_name=region)
    stack = cloudformation.describe_stacks(StackName=f"BlueyPlatform-{stage}")["Stacks"][0]
    output_name = {
        "main": "MainHarnessArnOutput",
        "credit": "CreditHarnessArnOutput",
        "account-opening": "AccountOpeningHarnessArnOutput",
    }[harness]
    for output in stack.get("Outputs", []):
        if output["OutputKey"] == output_name:
            return output["OutputValue"]
    raise RuntimeError(f"{output_name} was not found in BlueyPlatform-{stage}")

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',default='dev')
    p.add_argument('--harness',choices=['main','credit','account-opening'],default='main')
    p.add_argument('--prompt',default='Hello. Please tell me briefly what you can help me with.')
    p.add_argument('--region',default='us-east-1')
    a=p.parse_args()
    session = boto3.Session(region_name=a.region)
    ac = session.client("bedrock-agentcore")
    arn = harness_arn(a.region, a.stage, a.harness)
    response = ac.invoke_harness(
        harnessArn=arn,
        runtimeSessionId=str(uuid.uuid4()),
        messages=[{"role": "user", "content": [{"text": a.prompt}]}],
    )
    text = ""
    for chunk in response.get("stream", []):
        delta = chunk.get("contentBlockDelta", {}).get("delta", {})
        text += delta.get("text", "")
    print(text)
if __name__=='__main__': main()
