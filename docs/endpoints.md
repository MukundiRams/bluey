# Bluey — Working Endpoints & Connection Guide (dev stage)

Values below are from `python scripts/stack_outputs.py --stage dev --region us-east-1`.
Re-run that command after any redeploy — Lambda Function URLs and IDs are stable per-stack but this
file will go stale if the stack is destroyed/recreated.

## Endpoints

| Purpose | URL |
|---|---|
| Customer chat | `https://u2oulww7uojmz52okjacg6teyq0thmdg.lambda-url.us-east-1.on.aws/` |
| Account opening | `https://dhtn6d2wqqr6n7z7do52mnesui0eudkn.lambda-url.us-east-1.on.aws/` |
| Credit guidance | `https://s5kvktkeavrcb5rtl6f7hre2xa0tmmdr.lambda-url.us-east-1.on.aws/` |
| Financial advice | `https://tw2x7mtrlcdtjvyzwm6ntx6foa0vwpoo.lambda-url.us-east-1.on.aws/` |
| Banker API (review/approve/reject) | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com/banker` |
| Document API | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com/documents` |
| HTTP API base | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com` |

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
| User Pool ID | `us-east-1_xe8GIecSJ` |
| App Client ID | `rc7ahojg72vag18d84p1eqvmn` |
| Bankers group | `Bankers` |

The app client has no secret (public SPA client) and only supports the `USER_PASSWORD_AUTH` flow
(no hosted UI / OAuth). Sign-in alias is email; `email` and `fullname` are required attributes.

### Get an access token (USER_PASSWORD_AUTH)

```bash
aws cognito-idp initiate-auth \
  --region us-east-1 \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id rc7ahojg72vag18d84p1eqvmn \
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
  --user-pool-id us-east-1_xe8GIecSJ \
  --username test7@gmail.com \
  --user-attributes Name=email,Value=test7@gmail.com Name=name,Value="Test User7" \
  --temporary-password TPass123!

aws cognito-idp admin-set-user-password \
  --region us-east-1 \
  --user-pool-id us-east-1_xe8GIecSJ \
  --username test7@gmail.com \
  --password TPass123% \
  --permanent

  
aws cognito-idp initiate-auth \
  --region us-east-1 \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id rc7ahojg72vag18d84p1eqvmn \
  --auth-parameters 'USERNAME=test7@gmail.com,PASSWORD=TPass123%' \
  --query 'AuthenticationResult.AccessToken' \
  --output text
```

To add a banker (grants access to the Banker API):

```bash
aws cognito-idp admin-add-user-to-group \
  --region us-east-1 \
  --user-pool-id us-east-1_xe8GIecSJ \
  --username <email> \
  --group-name Bankers
```

## Quick smoke test

```bash
export CHAT_URL=https://u2oulww7uojmz52okjacg6teyq0thmdg.lambda-url.us-east-1.on.aws/
export CREDIT_URL=https://s5kvktkeavrcb5rtl6f7hre2xa0tmmdr.lambda-url.us-east-1.on.aws/
export FINANCIAL_ADVICE_URL=https://tw2x7mtrlcdtjvyzwm6ntx6foa0vwpoo.lambda-url.us-east-1.on.aws/
export COGNITO_ACCESS_TOKEN=<AccessToken from initiate-auth above>
python scripts/smoke_test.py
```
