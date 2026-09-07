"""Generic AgentCore Gateway -> Lambda DynamoDB tool implementation.

The tool name is supplied through client_context.custom['bedrockAgentCoreToolName'].
Gateway input parameters are passed directly in event.
"""

import os
from decimal import Decimal
import boto3
import random

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)


def _tool_name(context):
    tool_name = ""
    if context.client_context and context.client_context.custom:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "__" in tool_name:
        tool_name = tool_name.split("__", 1)[1].lstrip("_")
    return tool_name


def lambda_handler(event, context):
    tool_name = _tool_name(context)

    if tool_name == "get_item":
        table = dynamodb.Table(event["table_name"])
        resp = table.get_item(Key=event["key"])
        return {"result": resp.get("Item", {})}

    if tool_name == "put_item":
        table = dynamodb.Table(event["table_name"])
        table.put_item(Item=event["item"])
        return {"result": "saved"}


    if tool_name == "lookup_customer":
        id_number = event["idNumber"]
        session_id = event.get("sessionId")

        customers_table = dynamodb.Table("bluey-customers")
        resp = customers_table.scan(
            FilterExpression="idNumber = :id",
            ExpressionAttributeValues={":id": id_number},
        )
        items = resp.get("Items", [])
        customer = items[0] if items else None

        if customer:
            # Assign + persist a general banker if this customer doesn't have a personal one
            if "personalBanker" not in customer and "assignedBanker" not in customer:
                bankers_table = dynamodb.Table("bluey-bankers")
                bankers_resp = bankers_table.scan(
                    FilterExpression="tier = :t",
                    ExpressionAttributeValues={":t": "general"},
                )
                general_bankers = bankers_resp.get("Items", [])
                if general_bankers:
                    chosen = random.choice(general_bankers)
                    customers_table.update_item(
                        Key={"customerId": customer["customerId"]},
                        UpdateExpression="SET assignedBanker = :b",
                        ExpressionAttributeValues={":b": {"name": chosen["name"], "email": chosen["email"]}},
                    )
                    customer["assignedBanker"] = {"name": chosen["name"], "email": chosen["email"]}

            if session_id:
                sessions_table = dynamodb.Table("bluey-sessions")
                sessions_table.update_item(
                    Key={"sessionId": session_id},
                    UpdateExpression="SET customerId = :cid, verified = :v, reviewStatus = :rs",
                    ExpressionAttributeValues={":cid": customer["customerId"], ":v": True, ":rs": "pending_review"},
                )

        return {"result": customer}

    if tool_name == "get_accounts":
        customer_id = event["customerId"]
        accounts = dynamodb.Table("bluey-accounts")
        resp = accounts.scan(FilterExpression="customerId = :cid", ExpressionAttributeValues={":cid": customer_id})
        return {"result": resp.get("Items", [])}

    if tool_name == "get_transactions":
        account_id = event["accountId"]
        limit = event.get("limit", 10)
        transactions = dynamodb.Table("bluey-transactions")
        resp = transactions.query(KeyConditionExpression="accountId = :aid", ExpressionAttributeValues={":aid": account_id}, ScanIndexForward=False, Limit=limit)
        return {"result": resp.get("Items", [])}

    if tool_name == "check_documents_status":
        session_id = event["sessionId"]
        docs = dynamodb.Table("bluey-documents")
        resp = docs.query(KeyConditionExpression="sessionId = :sid", ExpressionAttributeValues={":sid": session_id})
        uploaded = {d["docType"] for d in resp.get("Items", []) if d.get("status") == "uploaded"}
        return {"result": {"idUploaded": "id_document" in uploaded, "proofOfAddressUploaded": "proof_of_address" in uploaded}}

    if tool_name == "save_applicant_info":
        session_id = event["sessionId"]
        dynamodb.Table("bluey-sessions").update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET applicantName = :n, applicantPhone = :p, applicantEmail = :e",
            ExpressionAttributeValues={":n": event.get("name", ""), ":p": event.get("phone", ""), ":e": event.get("email", "")},
        )
        return {"result": "saved"}

    if tool_name == "get_transaction_chart":
        account_id = event["accountId"]
        session_id = event.get("sessionId")
        resp = dynamodb.Table("bluey-transactions").query(KeyConditionExpression="accountId = :aid", ExpressionAttributeValues={":aid": account_id})
        totals = {}
        for txn in resp.get("Items", []):
            amount = float(txn.get("amount", 0))
            if amount < 0:
                category = txn.get("category", "Other")
                totals[category] = totals.get(category, 0.0) + abs(amount)
        chart_data = {"type": "bar", "title": "Spending by Category", "labels": list(totals), "values": [round(v, 2) for v in totals.values()]}
        if session_id:
            dynamo_safe = {**chart_data, "values": [Decimal(str(v)) for v in chart_data["values"]]}
            dynamodb.Table("bluey-sessions").update_item(Key={"sessionId": session_id}, UpdateExpression="SET pendingChartData = :c", ExpressionAttributeValues={":c": dynamo_safe})
        return {"result": chart_data}

    return {"error": f"unknown tool {tool_name}"}
