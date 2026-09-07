import json
import os
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
s3 = boto3.client("s3", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
documents_table = dynamodb.Table(os.environ.get("DOCUMENTS_TABLE", "bluey-documents"))
sessions_table = dynamodb.Table(os.environ.get("SESSIONS_TABLE", "bluey-sessions"))
BUCKET = os.environ.get("DOCUMENTS_BUCKET", "bluey-documents")


def lambda_handler(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")
    is_banker = "Bankers" in groups
    body = json.loads(event.get("body") or "{}")
    params = event.get("queryStringParameters") or {}
    action = params.get("action") or body.get("action")

    if action == "get_upload_url":
        session_id = body["sessionId"]
        doc_type = body["docType"]
        content_type = body.get("contentType", "application/octet-stream")
        s3_key = f"documents/{session_id}/{doc_type}"
        url = s3.generate_presigned_url("put_object", Params={"Bucket": BUCKET, "Key": s3_key, "ContentType": content_type}, ExpiresIn=300)
        documents_table.put_item(Item={"sessionId": session_id, "docType": doc_type, "s3Key": s3_key, "status": "pending", "createdAt": datetime.now(timezone.utc).isoformat()})
        return _response(200, {"uploadUrl": url, "s3Key": s3_key})

    if action == "confirm_upload":
        session_id = body["sessionId"]
        doc_type = body["docType"]
        documents_table.update_item(
            Key={"sessionId": session_id, "docType": doc_type},
            UpdateExpression="SET #s = :s, uploadedAt = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "uploaded", ":u": datetime.now(timezone.utc).isoformat()},
        )
        resp = documents_table.query(KeyConditionExpression="sessionId = :sid", ExpressionAttributeValues={":sid": session_id})
        uploaded = {d["docType"] for d in resp.get("Items", []) if d.get("status") == "uploaded"}
        if {"id_document", "proof_of_address"}.issubset(uploaded):
            sessions_table.update_item(Key={"sessionId": session_id}, UpdateExpression="SET reviewStatus = :rs", ExpressionAttributeValues={":rs": "pending_review"})
        return _response(200, {"result": "confirmed"})

    if action == "list_documents":
        if not is_banker:
            return _response(403, {"error": "forbidden — bankers only"})
        session_id = params.get("sessionId") or body.get("sessionId")
        resp = documents_table.query(KeyConditionExpression="sessionId = :sid", ExpressionAttributeValues={":sid": session_id})
        docs = resp.get("Items", [])
        for doc in docs:
            if doc.get("status") == "uploaded":
                doc["viewUrl"] = s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": doc["s3Key"]}, ExpiresIn=600)
        return _response(200, {"documents": docs})

    return _response(400, {"error": "unknown action"})


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}
