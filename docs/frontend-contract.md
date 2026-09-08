# Frontend contract

## Customer chat

`POST <chat-function-url>`

Request:

```json
{"prompt":"What is my balance?","session_id":"optional","user_id":"not trusted; use Cognito identity"}
```

In the final authenticated architecture the backend derives `user_id` from the Cognito access token rather than trusting a browser-supplied identity.

Response:

```json
{"reply":"...","session_id":"..."}
```

The response may additionally contain:

```json
{"chartData":{"type":"bar","title":"Spending by Category","labels":["Food"],"values":[123.45]}}
```

The frontend must retain the returned `session_id` and send it with subsequent messages in the same conversation.

## Account opening

`POST <account-opening-function-url>` uses the same `{prompt, session_id}` pattern. File uploads happen separately through the document API, not through the AgentCore harness request body.

## Credit guidance

`POST <credit-function-url>` uses the same `{prompt, session_id}` pattern and requires
the Cognito access token in the `Authorization: Bearer <token>` header. The endpoint
routes only to the credit Harness and preserves the conversation in `bluey-sessions`.
Credit responses are guidance only; final lending decisions remain outside the Harness.
When a customer asks for personalised product guidance, the credit Harness may use
the recommendation engine with a verified `customer_id`; the response includes a
segment and product suggestions marked guidance-only.

## Banker API

The HTTP API exposes `/banker` for review list/detail/approve/reject operations and `/documents` for document operations. Cognito JWT authentication is required.
