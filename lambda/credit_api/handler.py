"""Bluey credit and recommendation tools Lambda.

Exposes four Gateway tools: get_loan_products, get_credit_score,
check_credit_eligibility, calculate_repayment, plus recommend_products.
All customer data comes directly from bluey-customers/bluey-accounts
DynamoDB tables — no S3 export-file dependency, and no CamelCase/
snake_case field-name mismatch between functions (both read
"credit_score", matching how bluey-customers is actually seeded).
"""
import json
import os
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)
customers_table = dynamodb.Table(os.environ.get("CUSTOMERS_TABLE", "bluey-customers"))
accounts_table = dynamodb.Table(os.environ.get("ACCOUNTS_TABLE", "bluey-accounts"))
RECOMMENDATION_METADATA = json.loads((Path(__file__).parent / "recommendation_metadata.json").read_text())

PRODUCTS = [
    {"id": "personal-loan", "name": "Personal Loan", "loan_type": "personal", "interest_rate_percent": 15.5, "term_months": [12, 24, 36, 48, 60]},
    {"id": "home-loan", "name": "Home Loan", "loan_type": "home", "interest_rate_percent": 11.0, "term_months": [120, 180, 240, 300]},
    {"id": "vehicle-finance", "name": "Vehicle Finance", "loan_type": "vehicle", "interest_rate_percent": 13.5, "term_months": [24, 36, 48, 60, 72]},
    {"id": "credit-card", "name": "Credit Card", "loan_type": "credit_card", "interest_rate_percent": 19.5, "term_months": []},
]


def _customer(customer_id):
    return customers_table.get_item(Key={"customerId": customer_id}).get("Item")


def _balance(customer_id):
    resp = accounts_table.query(
        KeyConditionExpression="customerId = :cid",
        ExpressionAttributeValues={":cid": customer_id},
    )
    return sum(float(a.get("balance", 0)) for a in resp.get("Items", []))


def _score(customer_id):
    customer = _customer(customer_id)
    if not customer:
        return None
    return customer.get("credit_score")


def _recommendations(customer_id, top_n=3):
    customer = _customer(customer_id)
    if not customer:
        return {"error": "customer not found"}

    numeric = ("monthly_income", "credit_score", "savings_rate", "active_products_count")
    scales = {"monthly_income": 60000.0, "credit_score": 550.0, "savings_rate": 1.0, "active_products_count": 6.0}
    distance_by_cluster = {}
    for cluster_id, profile in RECOMMENDATION_METADATA["clusters"].items():
        distance_by_cluster[cluster_id] = sum(
            abs(float(customer.get(field, 0)) - float(profile[field])) / scales[field]
            for field in numeric
        )
    cluster_id = min(distance_by_cluster, key=distance_by_cluster.get)
    cluster_name = RECOMMENDATION_METADATA["clusters"][cluster_id]["name"]
    income = float(customer.get("monthly_income", 0))
    credit_score = float(customer.get("credit_score", 0))

    candidates = []
    for product_name, product in RECOMMENDATION_METADATA["products"].items():
        if cluster_name not in product["segments"]:
            continue
        if income < product["min_income"] or credit_score < product["min_credit_score"]:
            continue
        if customer.get(product["column"], 0):
            continue
        candidates.append({
            "product": product_name,
            "description": product["description"],
            "gap_score": round(1.0 + (product["min_income"] / max(income, 1)), 4),
            "guidance_only": True,
        })
    candidates.sort(key=lambda item: item["gap_score"])
    return {
        "customer_id": customer_id,
        "cluster_id": int(cluster_id),
        "cluster_name": cluster_name,
        "recommendations": candidates[:top_n],
        "disclaimer": "Guidance only; this does not guarantee approval. Final decisions require formal assessment.",
    }


def lambda_handler(event, context):
    name = ""
    if context is not None and context.client_context and context.client_context.custom:
        name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "__" in name:
        name = name.split("__", 1)[1].lstrip("_")

    if name == "get_loan_products":
        return {"result": PRODUCTS}

    if name == "get_credit_score":
        score = _score(event["customer_id"])
        return {"result": {
            "customer_id": event["customer_id"],
            "credit_score": score,
            "rating": "unavailable" if score is None else "provided-by-customer-record",
        }}

    if name == "check_credit_eligibility":
        customer_id = event["customer_id"]
        amount = float(event["loan_amount"])
        score = _score(customer_id)
        balance = _balance(customer_id)
        return {"result": {
            "customer_id": customer_id,
            "loan_amount": amount,
            "loan_type": event.get("loan_type", "personal"),
            "eligible": None if score is None else score >= 650 and amount <= max(balance * 5, 50000),
            "reason": "Guidance only; final eligibility requires formal credit assessment.",
        }}

    if name == "calculate_repayment":
        amount = float(event["loan_amount"])
        months = int(event["term_months"])
        rate = float(event.get("interest_rate_percent", 15.5))
        monthly = rate / 100 / 12
        payment = amount / months if monthly == 0 else amount * monthly * (1 + monthly) ** months / ((1 + monthly) ** months - 1)
        return {"result": {
            "loan_amount": amount, "term_months": months, "interest_rate_percent": rate,
            "monthly_repayment_zar": round(payment, 2),
        }}

    if name == "recommend_products":
        return {"result": _recommendations(event["customer_id"], int(event.get("top_n", 3)))}

    return {"error": f"unknown tool {name}"}