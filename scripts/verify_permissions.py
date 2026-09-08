#!/usr/bin/env python3
"""Read-only verification of Bluey's critical authorization chain."""
import argparse
import json

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    iam = session.client("iam")
    lam = session.client("lambda")
    ac = session.client("bedrock-agentcore-control")

    # Discover every bluey-* role by paginating list_roles (default page size
    # is 100 — a shared/hackathon account can easily have more roles than that,
    # so an unpaginated call can silently miss roles).
    roles = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for r in page["Roles"]:
            if r["RoleName"].startswith("bluey-"):
                roles.append(r["RoleName"])

    result = {"roles": {}}
    for role_name in roles:
        try:
            role = iam.get_role(RoleName=role_name)["Role"]
            policies = []
            for policy_name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                policies.append(iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)["PolicyDocument"])
            result["roles"][role_name] = {"trust": role["AssumeRolePolicyDocument"], "inline": policies}
        except iam.exceptions.NoSuchEntityException:
            result["roles"][role_name] = {"error": "role disappeared between list and get"}

    result["gateways"] = []
    for g in ac.list_gateways().get("items", []):
        gid = g["gatewayId"]
        gd = ac.get_gateway(gatewayIdentifier=gid)
        targets = ac.list_gateway_targets(gatewayIdentifier=gid).get("items", [])
        result["gateways"].append({"id": gid, "roleArn": gd.get("roleArn"), "targets": targets})

    result["lambda_policies"] = {}
    for name in ["bluey-sessions", "bluey-credit-api", "bluey-dynamodb-tool"]:
        try:
            result["lambda_policies"][name] = lam.get_policy(FunctionName=name)["Policy"]
        except lam.exceptions.ResourceNotFoundException:
            result["lambda_policies"][name] = None

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
