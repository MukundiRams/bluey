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


class _ChartFakeTable:
    """Minimal DynamoDB Table stand-in: query() returns items whose values
    contain the single ExpressionAttributeValues value (good enough for the
    accountId / customerId lookups get_transaction_chart performs)."""

    def __init__(self, items):
        self.items = items
        self.updates = []

    def query(self, KeyConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        value = next(iter(ExpressionAttributeValues.values()))
        return {"Items": [i for i in self.items if value in i.values()]}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


def _chart_module_and_context():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamodb_tool_chart", "lambda/dynamodb_tool/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Ctx:
        client_context = type("ClientContext", (), {"custom": {"bedrockAgentCoreToolName": "main__get_transaction_chart"}})()

    return module, Ctx()


def test_transaction_chart_requires_account_or_customer_id():
    module, ctx = _chart_module_and_context()
    body = module.lambda_handler({"sessionId": "sess-1"}, ctx)
    assert "error" in body


def test_transaction_chart_single_account_produces_bar_pie_line_and_summary(monkeypatch):
    from decimal import Decimal

    module, ctx = _chart_module_and_context()

    transactions = _ChartFakeTable([
        {"accountId": "acc-001", "date#transactionId": "2026-08-25#t1",
         "amount": Decimal("1000.00"), "category": "Income"},
        {"accountId": "acc-001", "date#transactionId": "2026-08-26#t2",
         "amount": Decimal("-200.00"), "category": "Food"},
        {"accountId": "acc-001", "date#transactionId": "2026-08-27#t3",
         "amount": Decimal("-100.00"), "category": "Transport"},
    ])
    sessions = _ChartFakeTable([])
    tables = {module.TRANSACTIONS_TABLE: transactions, module.SESSIONS_TABLE: sessions}

    class FakeDynamo:
        def Table(self, name):
            return tables[name]

    monkeypatch.setattr(module, "dynamodb", FakeDynamo())

    body = module.lambda_handler({"accountId": "acc-001", "sessionId": "sess-1"}, ctx)
    result = body["result"]

    # Backward-compatible top-level bar chart shape is unchanged.
    assert result["type"] == "bar"
    assert result["title"] == "Spending by Category"
    assert set(result["labels"]) == {"Food", "Transport"}

    chart_types = {c["type"] for c in result["charts"]}
    assert chart_types == {"bar", "pie", "line"}

    summary = result["summary"]
    assert summary["totalSpend"] == 300.0
    assert summary["totalIncome"] == 1000.0
    assert summary["netCashflow"] == 700.0
    assert summary["topCategory"] == "Food"
    assert sessions.updates, "pendingChartData should have been written to the session"


def test_transaction_chart_aggregates_all_accounts_for_customer_id(monkeypatch):
    from decimal import Decimal

    module, ctx = _chart_module_and_context()

    accounts = _ChartFakeTable([
        {"customerId": "cust-001", "accountId": "acc-001"},
        {"customerId": "cust-001", "accountId": "acc-002"},
    ])
    transactions = _ChartFakeTable([
        {"accountId": "acc-001", "date#transactionId": "2026-08-25#t1",
         "amount": Decimal("-100.00"), "category": "Food"},
        {"accountId": "acc-002", "date#transactionId": "2026-08-26#t2",
         "amount": Decimal("-50.00"), "category": "Transport"},
    ])
    sessions = _ChartFakeTable([])
    tables = {
        module.ACCOUNTS_TABLE: accounts,
        module.TRANSACTIONS_TABLE: transactions,
        module.SESSIONS_TABLE: sessions,
    }

    class FakeDynamo:
        def Table(self, name):
            return tables[name]

    monkeypatch.setattr(module, "dynamodb", FakeDynamo())

    body = module.lambda_handler({"customerId": "cust-001", "sessionId": "sess-2"}, ctx)
    result = body["result"]

    assert sorted(result["summary"]["accountsIncluded"]) == ["acc-001", "acc-002"]
    assert result["summary"]["totalSpend"] == 150.0


