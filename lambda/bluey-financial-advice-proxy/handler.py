import json
import os
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
HARNESS_ARN = os.environ.get("HARNESS_ARN", "")
HARNESS_PARAMETER_NAME = os.environ.get("HARNESS_PARAMETER_NAME", "")
ssm = boto3.client("ssm", region_name=REGION)
SESSIONS_TABLE_NAME = os.environ.get("SESSIONS_TABLE", "bluey-sessions")

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)
cognito_client = boto3.client("cognito-idp", region_name=REGION)


def verify_token(event):
    headers = event.get("headers", {}) or {}
    auth_header = headers.get("authorization", "") or headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer "):]
    try:
        response = cognito_client.get_user(AccessToken=token)
        attrs = {item["Name"]: item["Value"] for item in response["UserAttributes"]}
        return attrs.get("sub")
    except cognito_client.exceptions.NotAuthorizedException:
        return None


def lambda_handler(event, context):
    user_id = verify_token(event)
    if not user_id:
        return _response(401, {"error": "unauthorized"})
    harness_arn = HARNESS_ARN
    if not harness_arn and HARNESS_PARAMETER_NAME:
        harness_arn = ssm.get_parameter(Name=HARNESS_PARAMETER_NAME)["Parameter"]["Value"]
    if not harness_arn:
        return _response(500, {"error": "Harness ARN is not configured"})

    body = json.loads(event.get("body") or "{}")
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    existing = sessions_table.get_item(Key={"sessionId": session_id}).get("Item", {})
    messages = existing.get("messages", [])
    messages.append({"role": "user", "text": prompt, "timestamp": now})

    augmented_prompt = f"{prompt}\n\n[session_id: {session_id}]"
    response = agentcore_client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": augmented_prompt}]}],
    )

    full_text = ""
    for chunk in response["stream"]:
        if "contentBlockDelta" in chunk:
            delta = chunk["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                full_text += delta["text"]

    messages.append({
        "role": "assistant",
        "text": full_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    session_check = sessions_table.get_item(Key={"sessionId": session_id}).get("Item", {})
    chart_data = session_check.get("pendingChartData")
    update_expression = (
        "SET messages = :m, updatedAt = :u, userId = if_not_exists(userId, :uid), "
        "createdAt = if_not_exists(createdAt, :c)"
    )
    if chart_data:
        update_expression += " REMOVE pendingChartData"

    sessions_table.update_item(
        Key={"sessionId": session_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues={
            ":m": messages,
            ":u": now,
            ":uid": user_id,
            ":c": existing.get("createdAt") or now,
        },
    )

    response_body = {"reply": full_text, "session_id": session_id}
    if chart_data:
        response_body["chartData"] = chart_data
    return _response(200, response_body)


def _response(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
        "body": json.dumps(body),
    }
