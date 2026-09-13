import json
import os
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = dynamodb.Table(os.environ.get("SESSIONS_TABLE", "bluey-sessions"))
customers_table = dynamodb.Table(os.environ.get("CUSTOMERS_TABLE", "bluey-customers"))
documents_table = dynamodb.Table(os.environ.get("DOCUMENTS_TABLE", "bluey-documents"))
applications_table = dynamodb.Table(os.environ.get("APPLICATIONS_TABLE", "bluey-applications"))
accounts_table = dynamodb.Table(os.environ.get("ACCOUNTS_TABLE", "bluey-accounts"))
credit_table = dynamodb.Table(os.environ.get("CREDIT_TABLE", "bluey-credit"))
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

    if action in ("list", "list_applications", "applications"):
        session_resp = sessions_table.scan(
            FilterExpression="reviewStatus = :rs",
            ExpressionAttributeValues={":rs": "pending_review"},
        )
        app_resp = applications_table.scan()

        seen_sessions = set()
        results = []

        # Process session pending reviews
        for item in session_resp.get("Items", []):
            sid = item.get("sessionId")
            seen_sessions.add(sid)
            customer_id = item.get("customerId")
            if customer_id:
                customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
                full_name = customer.get("fullName", "Unknown")
            else:
                full_name = item.get("applicantName", "New applicant")

            results.append({
                "sessionId": sid,
                "reference": item.get("reference") or f"APP-{sid[-8:].upper() if sid else 'NEW'}",
                "customerId": customer_id,
                "fullName": full_name,
                "accountType": item.get("accountType", "Savings"),
                "status": item.get("reviewStatus", "pending_review"),
                "updatedAt": item.get("updatedAt") or item.get("createdAt"),
            })

        # Process applications table
        app_list = []
        for app in app_resp.get("Items", []):
            sid = app.get("sessionId")
            app_list.append(app)
            if app.get("status") in ("Pending", "pending", "pending_review") and sid not in seen_sessions:
                applicant_data = app.get("applicantData", {})
                results.append({
                    "sessionId": sid or app.get("reference"),
                    "reference": app.get("reference"),
                    "customerId": app.get("customerId"),
                    "fullName": applicant_data.get("fullName") or app.get("applicantName", "New applicant"),
                    "accountType": app.get("accountType", "Savings"),
                    "status": app.get("status", "Pending"),
                    "updatedAt": app.get("updatedAt") or app.get("createdAt"),
                })

        return _response(200, {"sessions": results, "applications": app_list})

    if action == "detail":
        session_id = params.get("sessionId") or body.get("sessionId")
        reference = params.get("reference") or body.get("reference")

        item = None
        if session_id:
            item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")

        # Also find matching application
        application = None
        if reference:
            application = applications_table.get_item(Key={"reference": reference}).get("Item")
        elif session_id:
            # Look up application by sessionId if not keyed directly
            app_scan = applications_table.scan(
                FilterExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": session_id},
            )
            apps = app_scan.get("Items", [])
            if apps:
                application = apps[0]

        if not item and application:
            session_id = application.get("sessionId", session_id)
            if session_id:
                item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")

        if not item and not application:
            return _response(404, {"error": "not found"})

        item = item or {}
        customer_id = item.get("customerId") or (application.get("customerId") if application else None)
        customer = {}
        accounts = []
        credit_info = {}

        if customer_id:
            customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
            acc_resp = accounts_table.query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            accounts = acc_resp.get("Items", [])
            cred_resp = credit_table.query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            credit_info = cred_resp.get("Items", [{}])[0] if cred_resp.get("Items") else {}
        else:
            applicant_data = (application.get("applicantData", {}) if application else {})
            customer = {
                "fullName": applicant_data.get("fullName") or item.get("applicantName", "New applicant"),
                "phone": applicant_data.get("phone") or item.get("applicantPhone"),
                "email": applicant_data.get("email") or item.get("applicantEmail"),
                "idNumber": applicant_data.get("idNumber"),
                "address": applicant_data.get("address"),
            }

        documents = []
        doc_sid = session_id or (application.get("sessionId") if application else None)
        if doc_sid:
            resp = documents_table.query(
                KeyConditionExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": doc_sid},
            )
            for doc in resp.get("Items", []):
                s3_key = doc.get("s3Key") or f"documents/{doc_sid}/{doc.get('docType')}"
                if DOCUMENTS_BUCKET:
                    doc["viewUrl"] = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": DOCUMENTS_BUCKET, "Key": s3_key},
                        ExpiresIn=3600,
                    )
                documents.append(doc)

        return _response(200, {
            "session": item,
            "customer": customer,
            "documents": documents,
            "application": application,
            "accounts": accounts,
            "credit": credit_info,
        })

    if action in ("approve", "reject"):
        session_id = body.get("sessionId")
        reference = body.get("reference")
        now = datetime.now(timezone.utc).isoformat()
        decision_status = "approved" if action == "approve" else "rejected"
        app_status = "Approved" if action == "approve" else "Rejected"

        if session_id:
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET reviewStatus = :rs, reviewedAt = :ra",
                ExpressionAttributeValues={":rs": decision_status, ":ra": now},
            )

        if reference:
            applications_table.update_item(
                Key={"reference": reference},
                UpdateExpression="SET #s = :s, reviewedAt = :ra",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": app_status, ":ra": now},
            )
        elif session_id:
            # Also update any application linked to this session
            app_scan = applications_table.scan(
                FilterExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": session_id},
            )
            for app in app_scan.get("Items", []):
                applications_table.update_item(
                    Key={"reference": app["reference"]},
                    UpdateExpression="SET #s = :s, reviewedAt = :ra",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": app_status, ":ra": now},
                )

        return _response(200, {"result": action, "sessionId": session_id, "reference": reference})

    return _response(400, {"error": "unknown action"})


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}
