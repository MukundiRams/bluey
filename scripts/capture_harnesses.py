#!/usr/bin/env python3
"""Capture current AgentCore Harness definitions from the authenticated AWS account.

Run this from a machine authenticated to the source account. The resulting JSON files are
configuration snapshots, not secrets, but review them before committing.
"""
import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

DEFAULT_IDS = [
    "harness_nxkh1-5J9xGVZCk5",
    "bluey_credit_harness-47LHHV15B7",
    "bluey_account_opening_harness-3B5upfrRSz",
]


def capture(harness_ids: list[str], output_dir: Path, region: str) -> int:
    client = boto3.client("bedrock-agentcore-control", region_name=region)
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
    parser.add_argument("harness_ids", nargs="*", default=DEFAULT_IDS)
    args = parser.parse_args()
    try:
        return capture(args.harness_ids, Path(args.output), args.region)
    except ClientError as exc:
        print(f"AWS error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
