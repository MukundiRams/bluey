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
