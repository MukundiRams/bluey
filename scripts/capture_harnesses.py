#!/usr/bin/env python3
"""Capture current AgentCore Harness definitions from the authenticated AWS account.

Run this from a machine authenticated to the source account. The resulting JSON files are
configuration snapshots, not secrets, but review them before committing.

No harness IDs are hardcoded here — this repo is meant to move between accounts,
and stale IDs from a previous account would just fail with ResourceNotFoundException.
If no IDs are passed explicitly, every harness currently in the account is captured.
"""
import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def _discover_all_harness_ids(client) -> list[str]:
    ids = []
    paginator = client.get_paginator("list_harnesses")
    for page in paginator.paginate():
        for h in page.get("harnessSummaries", page.get("items", [])):
            harness_id = h.get("harnessId") or h.get("id")
            if harness_id:
                ids.append(harness_id)
    return ids


def capture(harness_ids: list[str], output_dir: Path, region: str) -> int:
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    if not harness_ids:
        harness_ids = _discover_all_harness_ids(client)
        if not harness_ids:
            print("No harnesses found in this account/region.")
            return 0
        print(f"No harness IDs given — discovered {len(harness_ids)}: {harness_ids}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for harness_id in harness_ids:
        try:
            response = client.get_harness(harnessId=harness_id)
        except client.exceptions.ResourceNotFoundException as exc:
            raise RuntimeError(f"Harness not found: {harness_id}") from exc
        output = output_dir / f"{harness_id}.json"
        output.write_text(json.dumps(response, indent=2, default=str) + "\n", encoding="utf-8")
        print(output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", default="agentcore/captured")
    parser.add_argument("harness_ids", nargs="*", default=[], help="Specific harness IDs to capture; omit to capture every harness found in the account.")
    args = parser.parse_args()
    try:
        return capture(args.harness_ids, Path(args.output), args.region)
    except ClientError as exc:
        print(f"AWS error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
