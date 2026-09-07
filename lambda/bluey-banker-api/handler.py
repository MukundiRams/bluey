import json
import os

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = dynamodb.Table(os.environ.get("SESSIONS_TABLE", "bluey-sessions"))
customers_table = dynamodb.Table(os.environ.get("CUSTOMERS_TABLE", "bluey-customers"))
documents_table = dynamodb.Table(os.environ.get("DOCUMENTS_TABLE", "bluey-documents"))
s3 = boto3.client("s3", region_name=REGION)
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")


def lambda_handler(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")
    if "Bankers" not in groups:
        return _response(403, {"error": "forbidden — bankers only"})

    params = event.get("queryStringParameters") or {}
    body = json.loads(event.get("body") or "{}")
    action = params.get("action") or body.get("action")

    if action == "list":
        resp = sessions_table.scan(
            FilterExpression="reviewStatus = :rs",
            ExpressionAttributeValues={":rs": "pending_review"},
        )
        results = []
        for item in resp.get("Items", []):
            customer_id = item.get("customerId")
            if customer_id:
                customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
                full_name = customer.get("fullName", "Unknown")
            else:
                full_name = item.get("applicantName", "New applicant")
            results.append({
                "sessionId": item["sessionId"],
                "customerId": customer_id,
                "fullName": full_name,
                "updatedAt": item.get("updatedAt"),
            })
        return _response(200, {"sessions": results})

    if action == "detail":
        session_id = params.get("sessionId") or body.get("sessionId")
        item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")
        if not item:
            return _response(404, {"error": "not found"})

        customer_id = item.get("customerId")
        if customer_id:
            customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
        else:
            customer = {
                "fullName": item.get("applicantName", "New applicant"),
                "phone": item.get("applicantPhone"),
                "email": item.get("applicantEmail"),
            }

        documents = []
        resp = documents_table.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        for doc in resp.get("Items", []):
            if doc.get("status") == "uploaded" and DOCUMENTS_BUCKET:
                doc["viewUrl"] = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": DOCUMENTS_BUCKET, "Key": doc["s3Key"]},
                    ExpiresIn=600,
                )
            documents.append(doc)

        return _response(200, {"session": item, "customer": customer, "documents": documents})

    if action in ("approve", "reject"):
        session_id = body.get("sessionId")
        sessions_table.update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET reviewStatus = :rs",
            ExpressionAttributeValues={":rs": "approved" if action == "approve" else "rejected"},
        )
        return _response(200, {"result": action})

    return _response(400, {"error": "unknown action"})


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}
