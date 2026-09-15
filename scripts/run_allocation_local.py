#!/usr/bin/env python3
"""Run the REAL banker-api allocation flow locally against mocked DynamoDB.

This is a DEV-ONLY local driver. It exercises the actual
``lambda/bluey-banker-api/handler.py`` (the deployed code, NOT the
``local_banker_stub.py`` dummy server) against ``moto``-mocked DynamoDB, so the
new banker-workload-allocation logic runs end to end with NO live AWS.

It seeds a realistic scenario:
  - Two GENERAL bankers (banker-001, banker-002) and one PREMIUM (banker-003).
  - An assigned application (APP-1) owned by banker-001 via personalBanker.
  - A pending credit item owned by banker-003 (premium).
  - TWO unassigned/walk-in general-pool items with no personalBanker.

Then it calls the real ``list`` action as each banker and prints what happens,
so you can watch the walk-ins get allocated to the least-loaded general banker,
persisted to the new bluey-banker-assignments table, and flip to owned work.

Run (from the bluey/ directory):
    .\\.venv\\Scripts\\python.exe scripts/run_allocation_local.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import boto3
from moto import mock_aws

REGION = "us-east-1"

TABLE_ENV = {
    "SESSIONS_TABLE": "sessions",
    "CUSTOMERS_TABLE": "customers",
    "BANKERS_TABLE": "bankers",
    "DOCUMENTS_TABLE": "documents",
    "APPLICATIONS_TABLE": "applications",
    "ACCOUNTS_TABLE": "accounts",
    "CREDIT_TABLE": "credit",
    "READ_STATE_TABLE": "bluey-banker-read-state",
    "ASSIGNMENTS_TABLE": "bluey-banker-assignments",
}

EMAIL_GENERAL_1 = "lindiwe.dube@standardbank.co.za"   # banker-001, general
EMAIL_GENERAL_2 = "sipho.mthembu@standardbank.co.za"  # banker-002, general
EMAIL_PREMIUM = "themba.ndlovu@standardbank.co.za"    # banker-003, premium

HANDLER_PATH = Path(__file__).resolve().parents[1] / "lambda" / "bluey-banker-api" / "handler.py"


def _create_tables(dynamodb):
    def table(name, keys):
        return dynamodb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": k, "KeyType": kt} for k, kt in keys],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k, _ in keys],
            BillingMode="PAY_PER_REQUEST",
        )

    table(TABLE_ENV["SESSIONS_TABLE"], [("sessionId", "HASH")])
    table(TABLE_ENV["CUSTOMERS_TABLE"], [("customerId", "HASH")])
    table(TABLE_ENV["BANKERS_TABLE"], [("bankerId", "HASH")])
    table(TABLE_ENV["DOCUMENTS_TABLE"], [("sessionId", "HASH"), ("docType", "RANGE")])
    table(TABLE_ENV["APPLICATIONS_TABLE"], [("reference", "HASH")])
    table(TABLE_ENV["ACCOUNTS_TABLE"], [("customerId", "HASH"), ("accountId", "RANGE")])
    table(TABLE_ENV["CREDIT_TABLE"], [("customerId", "HASH"), ("sessionId", "RANGE")])
    table(TABLE_ENV["READ_STATE_TABLE"], [("bankerId", "HASH"), ("itemId", "RANGE")])
    table(TABLE_ENV["ASSIGNMENTS_TABLE"], [("itemId", "HASH")])


def _seed(dynamodb):
    bankers = dynamodb.Table(TABLE_ENV["BANKERS_TABLE"])
    bankers.put_item(Item={"bankerId": "banker-001", "email": EMAIL_GENERAL_1, "tier": "general"})
    bankers.put_item(Item={"bankerId": "banker-002", "email": EMAIL_GENERAL_2, "tier": "general"})
    bankers.put_item(Item={"bankerId": "banker-003", "email": EMAIL_PREMIUM, "tier": "premium"})

    customers = dynamodb.Table(TABLE_ENV["CUSTOMERS_TABLE"])
    # cust-001 is assigned to banker-001 (general).
    customers.put_item(Item={
        "customerId": "cust-001", "fullName": "Thabo Nkosi",
        "personalBanker": {"bankerId": "banker-001", "tier": "general"},
    })
    # cust-002 is assigned to banker-003 (premium).
    customers.put_item(Item={
        "customerId": "cust-002", "fullName": "Priya Singh",
        "personalBanker": {"bankerId": "banker-003", "tier": "premium"},
    })

    sessions = dynamodb.Table(TABLE_ENV["SESSIONS_TABLE"])
    # Two WALK-IN sessions (no customerId) -> general pool, unassigned.
    sessions.put_item(Item={
        "sessionId": "sess-walkin-1", "applicantName": "Nomsa Walk-In",
        "reviewStatus": "pending_review",
        "createdAt": "2026-08-20T09:00:00+00:00", "updatedAt": "2026-08-20T09:00:00+00:00",
    })
    sessions.put_item(Item={
        "sessionId": "sess-walkin-2", "applicantName": "Fresh Walk-In",
        "reviewStatus": "pending_review",
        "createdAt": "2026-08-21T09:00:00+00:00", "updatedAt": "2026-08-21T09:00:00+00:00",
    })

    applications = dynamodb.Table(TABLE_ENV["APPLICATIONS_TABLE"])
    # Assigned application for cust-001 -> banker-001, giving banker-001 a head start.
    applications.put_item(Item={
        "reference": "APP-1", "sessionId": "sess-app-1", "customerId": "cust-001",
        "accountType": "Savings", "status": "Pending",
        "createdAt": "2026-08-24T13:00:00+00:00", "updatedAt": "2026-08-24T13:00:00+00:00",
    })

    credit = dynamodb.Table(TABLE_ENV["CREDIT_TABLE"])
    # Assigned credit item for cust-002 -> banker-003 (premium).
    credit.put_item(Item={
        "customerId": "cust-002", "sessionId": "credit-002", "creditScore": 705,
        "reviewStatus": "pending_review",
        "createdAt": "2026-08-22T10:00:00+00:00", "updatedAt": "2026-08-22T10:00:00+00:00",
    })


def _load_handler():
    spec = importlib.util.spec_from_file_location("banker_handler_local", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _list_event(email):
    return {
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "cognito:groups": "Bankers", "email": email}}}},
        "queryStringParameters": {"action": "list"},
        "body": None,
    }


def _print_list(handler, label, email):
    resp = handler.lambda_handler(_list_event(email), None)
    body = json.loads(resp["body"])
    print(f"\n=== {label} ({email}) ===")
    print(f"  bankerId={body.get('bankerId')} tier={body.get('tier')} "
          f"unread={body.get('unreadCount')} stale={body.get('staleCount')}")
    for s in body.get("sessions", []):
        print(f"    - {s['itemId']:<32} routing={s['routing']:<12} "
              f"category={s['workCategory']}")
    if not body.get("sessions"):
        print("    (no items visible)")
    return body


def main():
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    os.environ["AWS_REGION"] = REGION
    os.environ["AWS_REGION_NAME"] = REGION
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
    for env_name, table_name in TABLE_ENV.items():
        os.environ[env_name] = table_name

    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _create_tables(dynamodb)
        _seed(dynamodb)

        handler = _load_handler()

        print("Seeded: banker-001 (general, owns APP-1), banker-002 (general, idle), "
              "banker-003 (premium).")
        print("Two unassigned walk-ins (sess-walkin-1, sess-walkin-2) start in the general pool.")

        # First list as banker-002 (the idle general banker) — this triggers
        # allocation of the unassigned walk-ins to the least-loaded general banker.
        _print_list(handler, "list #1 as general banker-002", EMAIL_GENERAL_2)
        _print_list(handler, "list #2 as general banker-001", EMAIL_GENERAL_1)
        _print_list(handler, "list #3 as premium banker-003", EMAIL_PREMIUM)

        # Show the persisted allocation records.
        assignments = dynamodb.Table(TABLE_ENV["ASSIGNMENTS_TABLE"])
        records = assignments.scan().get("Items", [])
        print("\n=== Persisted allocation records (bluey-banker-assignments) ===")
        for r in sorted(records, key=lambda x: x["itemId"]):
            print(f"    {r['itemId']:<32} -> {r['assignedBankerId']}")
        if not records:
            print("    (none)")


if __name__ == "__main__":
    main()
