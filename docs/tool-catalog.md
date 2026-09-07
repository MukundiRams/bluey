# Bluey tool catalog

The following tool names and behavioural contracts are established in the supplied setup log and are implemented in `lambda/dynamodb_tool/handler.py`.

| Tool | Purpose | Key inputs |
|---|---|---|
| `get_item` | Get an item from a DynamoDB table | `table_name`, `key` |
| `put_item` | Write an item | `table_name`, `item` |
| `lookup_customer` | Verify SA ID and link customer to session | `idNumber`, `sessionId` |
| `get_accounts` | Return accounts for verified customer | `customerId` |
| `get_transactions` | Return recent transactions | `accountId`, optional `limit` |
| `check_documents_status` | Check required document upload state | `sessionId` |
| `save_applicant_info` | Save new-applicant details | `sessionId`, `name`, `phone`, `email` |
| `get_transaction_chart` | Aggregate spending by category | `accountId`, `sessionId` |

## Important Gateway contract

Gateway target input arrives directly as the Lambda `event`. The tool name arrives in `context.client_context.custom["bedrockAgentCoreToolName"]` and is typically prefixed with the Gateway target name. The implementation strips the first `__` prefix before dispatching.

## DynamoDB numeric rule

Use `Decimal` for numbers written to DynamoDB. The chart tool returns JSON-compatible numbers to its caller, but converts chart values to `Decimal` before writing `pendingChartData` into DynamoDB.
