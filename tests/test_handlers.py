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
        def put_item(self, Item=None, **kwargs):
            if Item is not None:
                self.items.append(Item)
            return {}

    sessions = [{"sessionId": "sess-test", "applicantName": "John Doe", "reviewStatus": "pending_review"}]
    applications = [{"reference": "APP-001", "sessionId": "sess-test", "status": "Pending", "applicantData": {"fullName": "John Doe"}}]
    customers = [{"customerId": "cust-001", "fullName": "John Doe", "idNumber": "9001011234089"}]
    docs = [{"sessionId": "sess-test", "docType": "id_document", "status": "uploaded", "s3Key": "docs/id.pdf"}]
    # The banker-query-triage feature resolves the signed-in banker via the
    # Cognito email claim -> bluey-bankers.email. Seed a matching general-tier
    # banker so the list action resolves an identity (walk-in items route to
    # the general pool, which a general-tier banker can see).
    bankers = [{"bankerId": "banker-001", "email": "banker@standardbank.co.za", "tier": "general"}]

    monkeypatch.setattr(module, "sessions_table", MockTable(sessions))
    monkeypatch.setattr(module, "applications_table", MockTable(applications))
    monkeypatch.setattr(module, "customers_table", MockTable(customers))
    monkeypatch.setattr(module, "documents_table", MockTable(docs))
    monkeypatch.setattr(module, "accounts_table", MockTable([]))
    monkeypatch.setattr(module, "credit_table", MockTable([]))
    monkeypatch.setattr(module, "bankers_table", MockTable(bankers))
    monkeypatch.setattr(module, "read_state_table", MockTable([]))
    # The list action now enriches items via get_allocation(itemId,
    # assignments_table); stub the new assignments table so enrich resolves to
    # None (no allocation) instead of hitting real AWS.
    monkeypatch.setattr(module, "assignments_table", MockTable([]))

    auth_event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "cognito:groups": "Bankers", "email": "banker@standardbank.co.za"}}}},
        "queryStringParameters": {"action": "list"}
    }
    resp = module.lambda_handler(auth_event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "sessions" in body
    assert len(body["sessions"]) >= 1

    detail_event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "cognito:groups": "Bankers", "email": "banker@standardbank.co.za"}}}},
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


def _router_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("router_proxy", "lambda/bluey-router-proxy/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_intent_falls_back_to_default_on_bedrock_error(monkeypatch):
    module = _router_module()

    class FailingBedrock:
        def converse(self, **kwargs):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(module, "bedrock_runtime", FailingBedrock())
    assert module.classify_intent("anything") == module.DEFAULT_AGENT


class _RouterFakeSessionsTable:
    def __init__(self, item=None):
        self.item = item or {}
        self.updates = []

    def get_item(self, Key):
        return {"Item": self.item} if self.item else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        self.item.update({"routedAgent": kwargs["ExpressionAttributeValues"][":agent"]})


def test_router_reuses_previously_routed_agent(monkeypatch):
    module = _router_module()
    monkeypatch.setattr(module, "verify_token", lambda event: "user-1")
    monkeypatch.setattr(module, "HARNESS_ARNS", {**module.HARNESS_ARNS, "credit": "arn:aws:fake:credit"})

    sessions = _RouterFakeSessionsTable({"sessionId": "sess-1", "routedAgent": "credit", "messages": []})
    monkeypatch.setattr(module, "sessions_table", sessions)

    called_arns = []

    class FakeAgentCore:
        def invoke_harness(self, harnessArn, **kwargs):
            called_arns.append(harnessArn)
            return {"stream": []}

    monkeypatch.setattr(module, "agentcore_client", FakeAgentCore())

    def _fail_classify(prompt):
        raise AssertionError("should not reclassify once a session is already routed")

    monkeypatch.setattr(module, "classify_intent", _fail_classify)

    event = {
        "headers": {"authorization": "Bearer token"},
        "body": json.dumps({"prompt": "what's my rate now?", "session_id": "sess-1"}),
    }
    resp = module.lambda_handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["agent"] == "credit"
    assert called_arns == ["arn:aws:fake:credit"]


def test_router_honours_route_override(monkeypatch):
    module = _router_module()
    monkeypatch.setattr(module, "verify_token", lambda event: "user-1")
    monkeypatch.setattr(module, "HARNESS_ARNS", {**module.HARNESS_ARNS, "financial-advice": "arn:aws:fake:fa"})

    sessions = _RouterFakeSessionsTable({})
    monkeypatch.setattr(module, "sessions_table", sessions)
    monkeypatch.setattr(module, "agentcore_client", type("A", (), {"invoke_harness": staticmethod(lambda **kw: {"stream": []})})())

    event = {
        "headers": {"authorization": "Bearer token"},
        "body": json.dumps({
            "prompt": "help me budget",
            "session_id": "sess-2",
            "route_override": "financial-advice",
        }),
    }
    resp = module.lambda_handler(event, None)
    body = json.loads(resp["body"])
    assert body["agent"] == "financial-advice"

