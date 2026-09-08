"""Bluey credit and recommendation tools Lambda.

The live account exposes four Gateway tools. Product/risk data is intentionally kept in this
Lambda so the Gateway has no direct access to the underlying S3 data bucket.
"""
import json
import os
from pathlib import Path
from decimal import Decimal
import boto3

REGION=os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
s3=boto3.client("s3",region_name=REGION)
BUCKET=os.environ.get("CREDIT_DATA_BUCKET","bluey-tables")
dynamodb=boto3.resource("dynamodb",region_name=REGION)
customers_table=dynamodb.Table(os.environ.get("CUSTOMERS_TABLE","bluey-customers"))
RECOMMENDATION_METADATA=json.loads((Path(__file__).parent / "recommendation_metadata.json").read_text())

PRODUCTS=[
 {"id":"personal-loan","name":"Personal Loan","loan_type":"personal","interest_rate_percent":15.5,"term_months":[12,24,36,48,60]},
 {"id":"home-loan","name":"Home Loan","loan_type":"home","interest_rate_percent":11.0,"term_months":[120,180,240,300]},
 {"id":"vehicle-finance","name":"Vehicle Finance","loan_type":"vehicle","interest_rate_percent":13.5,"term_months":[24,36,48,60,72]},
 {"id":"credit-card","name":"Credit Card","loan_type":"credit_card","interest_rate_percent":19.5,"term_months":[]},
]


def _recommendations(customer_id, top_n=3):
    customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item")
    if not customer:
        return {"error": "customer not found"}

    # The exported model artifacts define four segment profiles. This lightweight
    # inference path uses the same profile and catalogue rules without requiring
    # scikit-learn inside the Lambda runtime.
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
    credit_score = float(customer.get("credit_score", customer.get("creditScore", 0)))

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

def _load_json(key):
    obj=s3.get_object(Bucket=BUCKET,Key=key)
    return json.loads(obj["Body"].read())

def _items(key, table):
    data=_load_json(key).get(table,[])
    return [{k: next(iter(v.values())) if isinstance(v,dict) and len(v)==1 else v for k,v in x.get("PutRequest",{}).get("Item",{}).items()} for x in data]

def _customer(cid):
    return next((x for x in _items("bluey-customers.json","bluey-customers") if x.get("customerId")==cid),None)

def _balance(cid):
    return sum(Decimal(str(x.get("balance",0))) for x in _items("bluey-accounts.json","bluey-accounts") if x.get("customerId")==cid)

def _score(cid):
    c=_customer(cid)
    if not c: return None
    # The current demo data may not contain a score; return a deterministic guidance score only
    # for the demo profile rather than claiming a real underwriting score.
    return c.get("creditScore")

def lambda_handler(event, context):
    name=(context.client_context.custom.get("bedrockAgentCoreToolName","") if context is not None and context.client_context and context.client_context.custom else "")
    if "__" in name: name=name.split("__",1)[1].lstrip("_")
    if name=="get_loan_products": return {"result":PRODUCTS}
    if name=="get_credit_score": return {"result": {"customer_id":event["customer_id"],"credit_score":_score(event["customer_id"]),"rating":"unavailable" if _score(event["customer_id"]) is None else "provided-by-customer-record"}}
    if name=="check_credit_eligibility":
        cid=event["customer_id"]; amount=float(event["loan_amount"]); score=_score(cid); balance=float(_balance(cid))
        return {"result":{"customer_id":cid,"loan_amount":amount,"loan_type":event.get("loan_type","personal"),"eligible":None if score is None else score>=650 and amount<=max(balance*5,50000),"reason":"Guidance only; final eligibility requires formal credit assessment."}}
    if name=="calculate_repayment":
        amount=float(event["loan_amount"]); months=int(event["term_months"]); rate=float(event.get("interest_rate_percent",15.5)); monthly=rate/100/12
        payment=amount/months if monthly==0 else amount*monthly*(1+monthly)**months/((1+monthly)**months-1)
        return {"result":{"loan_amount":amount,"term_months":months,"interest_rate_percent":rate,"monthly_repayment_zar":round(payment,2)}}
    if name=="recommend_products":
        return {"result": _recommendations(event["customer_id"], int(event.get("top_n", 3)))}
    return {"error":f"unknown tool {name}"}
