#!/usr/bin/env python3
"""Test whether Bluey's deployed endpoints are reachable and responding correctly.

Reads endpoint URLs and Cognito settings from --stage/--region via scripts/stack_outputs.py
data (pass explicitly via env vars, or supply --username/--password to fetch a token).

Usage:
    export CHAT_URL=... CREDIT_URL=... ACCOUNT_OPENING_URL=... FINANCIAL_ADVICE_URL=...
    export BANKER_API_URL=... DOCUMENT_API_URL=...
    export COGNITO_USER_POOL_CLIENT_ID=5h5olht3fonq8qmrcf9lm16mhn
    python scripts/test_endpoints.py --username you@example.com --password '...'

Or skip Cognito login and pass a token directly:
    export COGNITO_ACCESS_TOKEN=eyJ...
    python scripts/test_endpoints.py
"""
import argparse
import os
import sys

import boto3
import requests

CHAT_ENDPOINTS = {
    "Customer chat": "https://37yo7j53bjgzt6bccsx4iztqiy0dhvon.lambda-url.us-east-1.on.aws/",
    "Account opening": "https://ttrqvxsolxrjuiycbmmqw6ja5q0gpnph.lambda-url.us-east-1.on.aws/",
    "Credit guidance": "https://fxrnd5tujid2jywddb3yl3pf3m0bwtlw.lambda-url.us-east-1.on.aws/",
    "Financial advice": "https://kmihq6iasagfzxdhrkaolfwfh40pqcwx.lambda-url.us-east-1.on.aws/",
}


def get_token(region: str, client_id: str, username: str, password: str) -> str:
    client = boto3.client("cognito-idp", region_name=region)
    resp = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=client_id,
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return resp["AuthenticationResult"]["AccessToken"]


def check(name, method, url, token=None, json_body=None):
    if not url:
        print(f"[skip] {name}: no URL configured")
        return True
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.request(method, url, json=json_body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        print(f"[FAIL] {name}: request error — {exc}")
        return False
    ok = response.status_code < 500
    status = "ok" if ok else "FAIL"
    print(f"[{status}] {name}: {response.status_code} {response.text[:300]}")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--client-id", default=os.environ.get("COGNITO_USER_POOL_CLIENT_ID"))
    parser.add_argument("--username", default=os.environ.get("COGNITO_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("COGNITO_PASSWORD"))
    args = parser.parse_args()

    token = os.environ.get("COGNITO_ACCESS_TOKEN")
    if not token and args.username and args.password:
        if not args.client_id:
            raise SystemExit("Set --client-id or COGNITO_USER_POOL_CLIENT_ID to log in")
        token = get_token(args.region, args.client_id, args.username, args.password)
    if not token:
        print("No Cognito token available — chat endpoints will be checked unauthenticated (expect 401/403).")

    all_ok = True
    for name, env_var in CHAT_ENDPOINTS.items():
        url = os.environ.get(env_var)
        ok = check(name, "POST", url, token=token, json_body={"prompt": "Hello"})
        all_ok = all_ok and ok

    banker_url = os.environ.get("BANKER_API_URL")
    all_ok = check("Banker API", "GET", banker_url, token=token) and all_ok

    document_url = os.environ.get("DOCUMENT_API_URL")
    all_ok = check("Document API", "GET", document_url, token=token) and all_ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
