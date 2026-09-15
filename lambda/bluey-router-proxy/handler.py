"""Single-entry-point router: classifies intent and invokes the right Harness.

The frontend previously had to know which of the four Harness Function URLs
(chat/main, credit, account-opening, financial-advice) to call for a given
customer message. This proxy exposes one Function URL that classifies the
customer's message and forwards it to the correct Harness, so the frontend
only ever needs to call one endpoint.

Routing is sticky per session: the first message in a session is classified
and the chosen agent is stored on the session item (`routedAgent`). Later
messages in the same session reuse that agent rather than being reclassified,
because each Harness owns its own AgentCore-managed conversation memory keyed
by session id — switching Harness mid-conversation would silently drop
context. Set `route_override` in the request body (one of "main", "credit",
"account-opening", "financial-advice") to force a specific agent, e.g. from a
frontend tab/menu that already knows the intent.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
SESSIONS_TABLE_NAME = os.environ.get("SESSIONS_TABLE", "bluey-sessions")
CLASSIFIER_MODEL_ID = os.environ.get(
    "CLASSIFIER_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0"
)

HARNESS_ARNS = {
    "main": os.environ.get("MAIN_HARNESS_ARN", ""),
    "credit": os.environ.get("CREDIT_HARNESS_ARN", ""),
    "account-opening": os.environ.get("ACCOUNT_OPENING_HARNESS_ARN", ""),
    "financial-advice": os.environ.get("FINANCIAL_ADVICE_HARNESS_ARN", ""),
}
VALID_AGENTS = set(HARNESS_ARNS)
DEFAULT_AGENT = "main"

CLASSIFIER_SYSTEM_PROMPT = """You are an intent router for a South African bank's chat assistant.
Read the customer's message and reply with exactly one label, nothing else:

- main: general banking, balance/transaction questions, FAQs, complaints, existing-account support
- credit: loans, credit cards, credit score, affordability, repayments, borrowing
- account-opening: opening a new account, applying, onboarding, new customer sign-up
- financial-advice: budgeting, saving goals, financial planning, wellness guidance

Reply with only one of: main, credit, account-opening, financial-advice"""

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)
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


def classify_intent(prompt: str) -> str:
    try:
        response = bedrock_runtime.converse(
            modelId=CLASSIFIER_MODEL_ID,
            system=[{"text": CLASSIFIER_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"].strip().lower()
        for agent in VALID_AGENTS:
            if agent in text:
                return agent
    except Exception:
        pass
    return DEFAULT_AGENT


def lambda_handler(event, context):
    user_id = verify_token(event)
    if not user_id:
        return _response(401, {"error": "unauthorized"})

    body = json.loads(event.get("body") or "{}")
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    existing = sessions_table.get_item(Key={"sessionId": session_id}).get("Item", {})

    agent = body.get("route_override")
    if agent not in VALID_AGENTS:
        agent = existing.get("routedAgent")
    if agent not in VALID_AGENTS:
        agent = classify_intent(prompt)

    harness_arn = HARNESS_ARNS.get(agent)
    if not harness_arn:
        return _response(500, {"error": f"Harness ARN for '{agent}' is not configured"})

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
        "createdAt = if_not_exists(createdAt, :c), routedAgent = if_not_exists(routedAgent, :agent)"
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
            ":agent": agent,
        },
    )

    response_body = {"reply": full_text, "session_id": session_id, "agent": agent}
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
        "body": json.dumps(body, default=str),
    }
