# Bluey — Working Endpoints & Connection Guide (dev stage)

Values below are from `python scripts/stack_outputs.py --stage dev --region us-east-1`.
Re-run that command after any redeploy — Lambda Function URLs and IDs are stable per-stack but this
file will go stale if the stack is destroyed/recreated.

## Endpoints

| Purpose | URL |
|---|---|
| **Router (recommended default)**  | `https://sd6e7murbwgbpfxumuojyqutpq0vckbt.lambda-url.us-east-1.on.aws/` |
| Customer chat | `https://u2oulww7uojmz52okjacg6teyq0thmdg.lambda-url.us-east-1.on.aws/` |
| Account opening | `https://dhtn6d2wqqr6n7z7do52mnesui0eudkn.lambda-url.us-east-1.on.aws/` |
| Credit guidance | `https://s5kvktkeavrcb5rtl6f7hre2xa0tmmdr.lambda-url.us-east-1.on.aws/` |
| Financial advice | `https://tw2x7mtrlcdtjvyzwm6ntx6foa0vwpoo.lambda-url.us-east-1.on.aws/` |
| Banker API (review/approve/reject) | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com/banker` |
| Document API | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com/documents` |
| HTTP API base | `https://30cxlzxl00.execute-api.us-east-1.amazonaws.com` |
| Read-only accounts/transactions | `ReadonlyDataFunctionUrl` output of `BlueyPlatform-<stage>` |

The frontend previously had to pick which of the four chat-style Function URLs to call for a given
message. The **router** endpoint (`bluey-router-proxy`) solves this: it's a single Function URL that
classifies the customer's message and forwards it to the correct Harness (main/credit/account-opening/
financial-advice) automatically. New frontend integrations should call the router instead of picking
an agent endpoint themselves — see [frontend-contract.md](frontend-contract.md#router-recommended).
The four individual endpoints remain unchanged and still work directly (e.g. for a UI that already
knows the intent, such as a dedicated "Open an account" screen).

All chat-style endpoints (router, customer chat, account opening, credit, financial advice) take the
same request/response shape — see [frontend-contract.md](frontend-contract.md).

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

## Read-only accounts/transactions endpoint

A single Function URL (unauthenticated, public) fronts one Lambda with read-only
(`GetItem`/`Query`/`Scan` only, no write access) IAM permissions on the `bluey-accounts` and
`bluey-transactions` tables. It's one URL for both tables because the same Lambda routes on an
`action` query parameter rather than needing a separate function/URL per table. Get the current
URL with `python scripts/stack_outputs.py --stage dev` (`ReadonlyDataFunctionUrl` under
`[BlueyPlatform-dev]`).

Get a customer's accounts:

```bash
curl "<ReadonlyDataFunctionUrl>?action=accounts&customerId=<customerId>"
```

```json
{"result": [{"customerId": "...", "accountId": "...", "accountType": "Savings", "balance": "..."}]}
```

Get an account's recent transactions (`limit` optional, defaults to 20, newest first):

```bash
curl "<ReadonlyDataFunctionUrl>?action=transactions&accountId=<accountId>&limit=10"
```

```json
{"result": [{"accountId": "...", "date#transactionId": "...", "amount": "...", "description": "..."}]}
```

`customerId` / `accountId` are required for their respective actions; omitting them, or sending
any other `action`, returns a `400` with an `{"error": "..."}` body.

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
export ROUTER_URL=https://sd6e7murbwgbpfxumuojyqutpq0vckbt.lambda-url.us-east-1.on.aws/
export CHAT_URL=https://u2oulww7uojmz52okjacg6teyq0thmdg.lambda-url.us-east-1.on.aws/
export CREDIT_URL=https://s5kvktkeavrcb5rtl6f7hre2xa0tmmdr.lambda-url.us-east-1.on.aws/
export FINANCIAL_ADVICE_URL=https://tw2x7mtrlcdtjvyzwm6ntx6foa0vwpoo.lambda-url.us-east-1.on.aws/
export COGNITO_ACCESS_TOKEN=<AccessToken from initiate-auth above>
python scripts/smoke_test.py
```
