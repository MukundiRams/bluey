"""Read-only HTTP endpoint over the accounts and transactions tables.

Exposed via a Lambda Function URL. The execution role backing this function
is granted read-only (GetItem/Query/Scan) access to those two tables only —
no write permissions, no access to any other table.
"""

import json
import os

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)
accounts_table = dynamodb.Table(os.environ.get("ACCOUNTS_TABLE", "bluey-accounts"))
transactions_table = dynamodb.Table(os.environ.get("TRANSACTIONS_TABLE", "bluey-transactions"))


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    action = params.get("action")

    if action == "accounts":
        customer_id = params.get("customerId")
        if not customer_id:
            return _response(400, {"error": "customerId is required"})
        resp = accounts_table.query(
            KeyConditionExpression="customerId = :cid",
            ExpressionAttributeValues={":cid": customer_id},
        )
        return _response(200, {"result": resp.get("Items", [])})

    if action == "transactions":
        account_id = params.get("accountId")
        if not account_id:
            return _response(400, {"error": "accountId is required"})
        try:
            limit = int(params.get("limit", 20))
        except ValueError:
            return _response(400, {"error": "limit must be an integer"})
        resp = transactions_table.query(
            KeyConditionExpression="accountId = :aid",
            ExpressionAttributeValues={":aid": account_id},
            ScanIndexForward=False,
            Limit=limit,
        )
        return _response(200, {"result": resp.get("Items", [])})

    return _response(400, {"error": "action must be 'accounts' or 'transactions'"})
