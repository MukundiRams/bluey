"""Generic AgentCore Gateway -> Lambda DynamoDB tool implementation.

The tool name is supplied through client_context.custom['bedrockAgentCoreToolName'].
Gateway input parameters are passed directly in event.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal
import boto3
import random

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)

CUSTOMERS_TABLE = os.environ.get("CUSTOMERS_TABLE", "bluey-customers")
BANKERS_TABLE = os.environ.get("BANKERS_TABLE", "bluey-bankers")
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "bluey-sessions")
ACCOUNTS_TABLE = os.environ.get("ACCOUNTS_TABLE", "bluey-accounts")
TRANSACTIONS_TABLE = os.environ.get("TRANSACTIONS_TABLE", "bluey-transactions")
DOCUMENTS_TABLE = os.environ.get("DOCUMENTS_TABLE", "bluey-documents")
APPLICATIONS_TABLE = os.environ.get("APPLICATIONS_TABLE", "bluey-applications")
CREDIT_TABLE = os.environ.get("CREDIT_TABLE", "bluey-credit")
MESSAGES_TABLE = os.environ.get("MESSAGES_TABLE", "bluey-messages")


def _tool_name(context):
    tool_name = ""
    if context and context.client_context and context.client_context.custom:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "__" in tool_name:
        tool_name = tool_name.split("__", 1)[1].lstrip("_")
    return tool_name


def _floats_to_decimal(value):
    """Recursively convert float values to Decimal (via str) for DynamoDB writes."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


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

        customers_table = dynamodb.Table(CUSTOMERS_TABLE)
        resp = customers_table.scan(
            FilterExpression="idNumber = :id",
            ExpressionAttributeValues={":id": id_number},
        )
        items = resp.get("Items", [])
        customer = items[0] if items else None

        if customer:
            # Assign + persist a general banker if this customer doesn't have a personal one
            if "personalBanker" not in customer and "assignedBanker" not in customer:
                bankers_table = dynamodb.Table(BANKERS_TABLE)
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
                sessions_table = dynamodb.Table(SESSIONS_TABLE)
                sessions_table.update_item(
                    Key={"sessionId": session_id},
                    UpdateExpression="SET customerId = :cid, verified = :v, reviewStatus = :rs",
                    ExpressionAttributeValues={":cid": customer["customerId"], ":v": True, ":rs": "pending_review"},
                )

        return {"result": customer}

    if tool_name == "get_accounts":
        customer_id = event["customerId"]
        accounts = dynamodb.Table(ACCOUNTS_TABLE)
        try:
            resp = accounts.query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            items = resp.get("Items", [])
        except Exception:
            resp = accounts.scan(
                FilterExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            items = resp.get("Items", [])
        return {"result": items}

    if tool_name == "get_transactions":
        account_id = event["accountId"]
        limit = event.get("limit", 10)
        transactions = dynamodb.Table(TRANSACTIONS_TABLE)
        resp = transactions.query(
            KeyConditionExpression="accountId = :aid",
            ExpressionAttributeValues={":aid": account_id},
            ScanIndexForward=False,
            Limit=limit,
        )
        return {"result": resp.get("Items", [])}

    if tool_name == "check_documents_status":
        session_id = event["sessionId"]
        docs = dynamodb.Table(DOCUMENTS_TABLE)
        resp = docs.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        uploaded = {d["docType"] for d in resp.get("Items", []) if d.get("status") == "uploaded"}
        return {"result": {"idUploaded": "id_document" in uploaded, "proofOfAddressUploaded": "proof_of_address" in uploaded}}

    if tool_name == "save_applicant_info":
        session_id = event["sessionId"]
        now = datetime.now(timezone.utc).isoformat()
        name = event.get("name", "")
        phone = event.get("phone", "")
        email = event.get("email", "")
        account_type = event.get("accountType", "Savings")
        reference = f"APP-{session_id[-8:].upper() if len(session_id) >= 8 else session_id.upper()}"

        dynamodb.Table(SESSIONS_TABLE).update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET applicantName = :n, applicantPhone = :p, applicantEmail = :e, reviewStatus = :rs, reference = :ref, updatedAt = :u",
            ExpressionAttributeValues={":n": name, ":p": phone, ":e": email, ":rs": "pending_review", ":ref": reference, ":u": now},
        )

        try:
            dynamodb.Table(APPLICATIONS_TABLE).put_item(
                Item={
                    "reference": reference,
                    "sessionId": session_id,
                    "accountType": account_type,
                    "status": "Pending",
                    "applicantData": {
                        "fullName": name,
                        "phone": phone,
                        "email": email,
                    },
                    "createdAt": now,
                    "updatedAt": now,
                }
            )
        except Exception:
            pass

        return {"result": "saved"}

    if tool_name == "get_transaction_chart":
        account_id = event.get("accountId")
        customer_id = event.get("customerId")
        session_id = event.get("sessionId")

        if not account_id and not customer_id:
            return {"error": "accountId or customerId is required"}

        # "spending breakdown" style questions are about the whole customer,
        # not one specific account, so aggregate across every account they
        # hold when only a customerId is given. A specific accountId still
        # narrows the chart to that one account.
        if account_id:
            account_ids = [account_id]
        else:
            accounts_resp = dynamodb.Table(ACCOUNTS_TABLE).query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            account_ids = [a["accountId"] for a in accounts_resp.get("Items", []) if a.get("accountId")]
            if not account_ids:
                return {"error": f"no accounts found for customer {customer_id}"}

        transactions_table = dynamodb.Table(TRANSACTIONS_TABLE)
        items = []
        for aid in account_ids:
            resp = transactions_table.query(
                KeyConditionExpression="accountId = :aid",
                ExpressionAttributeValues={":aid": aid},
            )
            items.extend(resp.get("Items", []))

        category_totals: dict = {}
        category_counts: dict = {}
        daily_totals: dict = {}
        total_spend = 0.0
        total_income = 0.0

        for txn in items:
            amount = float(txn.get("amount", 0))
            if amount < 0:
                spend = abs(amount)
                category = txn.get("category", "Other")
                category_totals[category] = category_totals.get(category, 0.0) + spend
                category_counts[category] = category_counts.get(category, 0) + 1
                date_key = str(txn.get("date#transactionId", "")).split("#", 1)[0] or "unknown"
                daily_totals[date_key] = daily_totals.get(date_key, 0.0) + spend
                total_spend += spend
            else:
                total_income += amount

        total_spend = round(total_spend, 2)
        total_income = round(total_income, 2)
        labels = list(category_totals.keys())
        values = [round(category_totals[c], 2) for c in labels]

        bar_chart = {"type": "bar", "title": "Spending by Category", "labels": labels, "values": values}
        pie_chart = {
            "type": "pie",
            "title": "Spending Breakdown (%)",
            "labels": labels,
            "values": values,
            "percentages": [round(v / total_spend * 100, 1) if total_spend else 0 for v in values],
        }
        sorted_days = sorted(daily_totals)
        line_chart = {
            "type": "line",
            "title": "Spending Over Time",
            "labels": sorted_days,
            "values": [round(daily_totals[d], 2) for d in sorted_days],
        }

        category_breakdown = [
            {
                "category": c,
                "amount": round(category_totals[c], 2),
                "percentage": round(category_totals[c] / total_spend * 100, 1) if total_spend else 0,
                "transactionCount": category_counts[c],
            }
            for c in labels
        ]
        summary = {
            "totalSpend": total_spend,
            "totalIncome": total_income,
            "netCashflow": round(total_income - total_spend, 2),
            "transactionCount": len(items),
            "spendingTransactionCount": sum(category_counts.values()),
            "topCategory": max(category_totals, key=category_totals.get) if category_totals else None,
            "averageTransactionAmount": round(total_spend / sum(category_counts.values()), 2) if category_counts else 0,
            "accountsIncluded": account_ids,
            "categoryBreakdown": category_breakdown,
        }

        # Top-level type/title/labels/values stay the original bar-chart shape
        # for backward compatibility; "charts" and "summary" are additive.
        chart_data = {**bar_chart, "charts": [bar_chart, pie_chart, line_chart], "summary": summary}

        if session_id:
            dynamodb.Table(SESSIONS_TABLE).update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET pendingChartData = :c",
                ExpressionAttributeValues={":c": _floats_to_decimal(chart_data)},
            )
        return {"result": chart_data}

    return {"error": f"unknown tool {tool_name}"}
