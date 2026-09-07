import json


def test_banker_forbidden_without_group(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("banker", "lambda/banker_api/handler.py")
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
