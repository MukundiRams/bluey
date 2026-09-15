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

The response may additionally contain `chartData`. The top-level `type`/`title`/`labels`/`values` are always the spending-by-category bar chart (unchanged, for backward compatibility). `charts` is an array with that same bar chart plus a pie chart (with `percentages`) and a spending-over-time line chart. `summary` has aggregate numbers (totals, net cashflow, top category, per-category amount/percentage/transactionCount) for a richer, non-chart display:

```json
{
  "chartData": {
    "type": "bar", "title": "Spending by Category", "labels": ["Food"], "values": [123.45],
    "charts": [
      {"type": "bar", "title": "Spending by Category", "labels": ["Food"], "values": [123.45]},
      {"type": "pie", "title": "Spending Breakdown (%)", "labels": ["Food"], "values": [123.45], "percentages": [100.0]},
      {"type": "line", "title": "Spending Over Time", "labels": ["2026-08-30"], "values": [123.45]}
    ],
    "summary": {
      "totalSpend": 123.45, "totalIncome": 0, "netCashflow": -123.45,
      "transactionCount": 1, "spendingTransactionCount": 1, "topCategory": "Food",
      "averageTransactionAmount": 123.45, "accountsIncluded": ["acc-001"],
      "categoryBreakdown": [{"category": "Food", "amount": 123.45, "percentage": 100.0, "transactionCount": 1}]
    }
  }
}
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
