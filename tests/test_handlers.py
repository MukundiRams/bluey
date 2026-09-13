import json


def test_banker_forbidden_without_group(monkeypatch):
    import importlib.util
    # Real path is lambda/bluey-banker-api/handler.py (hyphenated, "bluey-"
    # prefixed) — this test previously pointed at a nonexistent
    # lambda/banker_api/handler.py and crashed on import before it could
    # even run an assertion.
    spec = importlib.util.spec_from_file_location("banker", "lambda/bluey-banker-api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = module.lambda_handler({"requestContext": {"authorizer": {"jwt": {"claims": {}}}}}, None)
    assert body["statusCode"] == 403


def test_credit_tool_handler_is_safe_without_gateway_context():
    import importlib.util
    spec = importlib.util.spec_from_file_location("credit", "lambda/credit_api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = module.lambda_handler({}, None)
    assert body["error"].startswith("unknown tool")


def test_credit_recommendation_returns_guidance(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("credit_recommendation", "lambda/credit_api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class CustomerTable:
        def get_item(self, Key):
            return {
                "Item": {
                    "customerId": Key["customerId"],
                    "monthly_income": 68000,
                    "credit_score": 705,
                    "savings_rate": 0.44,
                    "active_products_count": 3,
                    "has_private_banking": 0,
                    "has_platinum_credit_card": 0,
                    "has_home_loan": 1,
                }
            }

    monkeypatch.setattr(module, "customers_table", CustomerTable())

    class Context:
        client_context = type("ClientContext", (), {"custom": {"bedrockAgentCoreToolName": "credit__recommend_products"}})()

    body = module.lambda_handler({"customer_id": "cust-test", "top_n": 2}, Context())
    assert body["result"]["customer_id"] == "cust-test"
    assert body["result"]["recommendations"]
    assert body["result"]["disclaimer"].startswith("Guidance only")


def test_dynamodb_tool_handler_unknown_tool_returns_error():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamodb_tool", "lambda/dynamodb_tool/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Ctx:
        client_context = None

    body = module.lambda_handler({}, _Ctx())
    assert body["error"].startswith("unknown tool")


def test_banker_list_and_detail(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("banker_api", "lambda/bluey-banker-api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class MockTable:
        def __init__(self, items=None):
            self.items = items or []
        def scan(self, **kwargs):
            return {"Items": self.items}
        def query(self, **kwargs):
            return {"Items": self.items}
        def get_item(self, Key):
            for it in self.items:
                if any(it.get(k) == v for k, v in Key.items()):
                    return {"Item": it}
            return {}
        def update_item(self, **kwargs):
            return {}

    sessions = [{"sessionId": "sess-test", "applicantName": "John Doe", "reviewStatus": "pending_review"}]
    applications = [{"reference": "APP-001", "sessionId": "sess-test", "status": "Pending", "applicantData": {"fullName": "John Doe"}}]
    customers = [{"customerId": "cust-001", "fullName": "John Doe", "idNumber": "9001011234089"}]
    docs = [{"sessionId": "sess-test", "docType": "id_document", "status": "uploaded", "s3Key": "docs/id.pdf"}]

    monkeypatch.setattr(module, "sessions_table", MockTable(sessions))
    monkeypatch.setattr(module, "applications_table", MockTable(applications))
    monkeypatch.setattr(module, "customers_table", MockTable(customers))
    monkeypatch.setattr(module, "documents_table", MockTable(docs))
    monkeypatch.setattr(module, "accounts_table", MockTable([]))
    monkeypatch.setattr(module, "credit_table", MockTable([]))

    auth_event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "Bankers"}}}},
        "queryStringParameters": {"action": "list"}
    }
    resp = module.lambda_handler(auth_event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "sessions" in body
    assert len(body["sessions"]) >= 1

    detail_event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "Bankers"}}}},
        "queryStringParameters": {"action": "detail", "sessionId": "sess-test"}
    }
    resp = module.lambda_handler(detail_event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["session"]["sessionId"] == "sess-test"
    assert len(body["documents"]) == 1

