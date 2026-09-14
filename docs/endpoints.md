# Bluey — Working Endpoints & Connection Guide (hackathon stage)

Values below are from `python scripts/stack_outputs.py --stage hackathon --region us-east-1`.
Re-run that command after any redeploy — Lambda Function URLs and IDs are stable per-stack but this
file will go stale if the stack is destroyed/recreated.

## Endpoints

| Purpose | URL |
|---|---|
| Customer chat | `https://37yo7j53bjgzt6bccsx4iztqiy0dhvon.lambda-url.us-east-1.on.aws/` |
| Account opening | `https://ttrqvxsolxrjuiycbmmqw6ja5q0gpnph.lambda-url.us-east-1.on.aws/` |
| Credit guidance | `https://fxrnd5tujid2jywddb3yl3pf3m0bwtlw.lambda-url.us-east-1.on.aws/` |
| Financial advice | `https://kmihq6iasagfzxdhrkaolfwfh40pqcwx.lambda-url.us-east-1.on.aws/` |
| Banker API (review/approve/reject) | `https://ta02ckm5gb.execute-api.us-east-1.amazonaws.com/banker` |
| Document API | `https://ta02ckm5gb.execute-api.us-east-1.amazonaws.com/documents` |
| HTTP API base | `https://ta02ckm5gb.execute-api.us-east-1.amazonaws.com` |

All chat-style endpoints (customer chat, account opening, credit, financial advice) take the same
request/response shape — see [frontend-contract.md](frontend-contract.md).

## Request/response shape (chat, account opening, credit, financial advice)

```
POST <endpoint-url>
Authorization: Bearer <Cognito access token>
Content-Type: application/json

{"prompt": "What is my balance?", "session_id": "optional"}
```

```json
{"reply": "...", "session_id": "..."}
```

Save and resend the returned `session_id` on every subsequent message in the same conversation.
Chat responses may also include `chartData` (see [frontend-contract.md](frontend-contract.md) for the full shape).
`user_id` in the request body is not trusted — the backend derives identity from the Cognito
access token.

## Cognito auth

| Setting | Value |
|---|---|
| Region | `us-east-1` |
| User Pool ID | `us-east-1_FT3OTxGPU` |
| App Client ID | `5h5olht3fonq8qmrcf9lm16mhn` |
| Bankers group | `Bankers` |

The app client has no secret (public SPA client) and only supports the `USER_PASSWORD_AUTH` flow
(no hosted UI / OAuth). Sign-in alias is email; `email` and `fullname` are required attributes.

### Get an access token (USER_PASSWORD_AUTH)

```bash
aws cognito-idp initiate-auth \
  --region us-east-1 \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id 5h5olht3fonq8qmrcf9lm16mhn \
  --auth-parameters USERNAME=<email>,PASSWORD=<password>
```

Take `AuthenticationResult.AccessToken` from the response and send it as:

```
Authorization: Bearer <AccessToken>
```

To create a test user (self-sign-up is enabled, so this can also happen via a sign-up call):

```bash
aws cognito-idp admin-create-user \
  --region us-east-1 \
  --user-pool-id us-east-1_FT3OTxGPU \
  --username <email> \
  --user-attributes Name=email,Value=<email> Name=name,Value="Test User" \
  --temporary-password <TempPass123!>

aws cognito-idp admin-set-user-password \
  --region us-east-1 \
  --user-pool-id us-east-1_FT3OTxGPU \
  --username <email> \
  --password <Password123!> \
  --permanent
```

To add a banker (grants access to the Banker API):

```bash
aws cognito-idp admin-add-user-to-group \
  --region us-east-1 \
  --user-pool-id us-east-1_FT3OTxGPU \
  --username <email> \
  --group-name Bankers
```

## Quick smoke test

```bash
export CHAT_URL=https://37yo7j53bjgzt6bccsx4iztqiy0dhvon.lambda-url.us-east-1.on.aws/
export CREDIT_URL=https://fxrnd5tujid2jywddb3yl3pf3m0bwtlw.lambda-url.us-east-1.on.aws/
export FINANCIAL_ADVICE_URL=https://kmihq6iasagfzxdhrkaolfwfh40pqcwx.lambda-url.us-east-1.on.aws/
export COGNITO_ACCESS_TOKEN=<AccessToken from initiate-auth above>
python scripts/smoke_test.py
```
