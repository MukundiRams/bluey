#!/usr/bin/env python3
import argparse

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    client = boto3.client("cloudformation", region_name=args.region)
    # All 5 stacks — BlueyData and BlueyAuth were previously missing here,
    # which meant the Cognito User Pool ID / App Client ID (needed for .env
    # and the frontend contract) were never printed anywhere.
    # BlueyApi and BlueyAgentCore were merged into BlueyPlatform — see
    # infra/stacks/platform_stack.py for why.
    stacks = [
        f"BlueyData-{args.stage}",
        f"BlueyAuth-{args.stage}",
        f"BlueyKnowledge-{args.stage}",
        f"BlueyPlatform-{args.stage}",
    ]
    for stack in stacks:
        try:
            outputs = client.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
            print(f"[{stack}]")
            for o in outputs:
                print(f"{o['OutputKey']}={o['OutputValue']}")
        except client.exceptions.ClientError:
            print(f"[{stack}] not found (not deployed yet?)")


if __name__ == "__main__":
    main()
