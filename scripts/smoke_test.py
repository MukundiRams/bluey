"""Non-destructive API smoke-test helper.

Set CHAT_URL / CREDIT_URL / ACCOUNT_OPENING_URL / BANKER_API_URL / DOCUMENT_API_URL in the environment.
Tokens are supplied from the frontend/authentication flow and are never stored in the repo.
"""
import os
import requests


def post(url, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    print(url, response.status_code, response.text[:500])
    return response


if __name__ == "__main__":
    url = os.environ.get("CHAT_URL")
    credit_url = os.environ.get("CREDIT_URL")
    token = os.environ.get("COGNITO_ACCESS_TOKEN")
    if not url or not token:
        raise SystemExit("Set CHAT_URL and COGNITO_ACCESS_TOKEN")
    post(url, {"prompt": "Hello"}, token)
    if credit_url:
        post(credit_url, {"prompt": "What credit products can you explain?"}, token)
