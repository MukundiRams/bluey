# Live AWS inventory snapshot

Inspected through the connected AWS account on 2026-09-07. Identifiers below are names only; account IDs and resource ARNs are intentionally excluded so this repository can be moved between accounts.

## AgentCore

### Runtimes seen

- `harness_harness_nxkh1` — READY, version 10
- `harness_bluey_credit_harness` — READY, version 3
- `harness_bluey_account_opening_harness` — READY, version 7

### Memories seen

- `memory_ygumt-2zAanN2NuP`
- `bluey_account_opening_harness-oJgeaz7Bom`
- `bluey_credit_harness-1MKuIqF2HC`

### Gateways seen

- `gateway-kb-tool` — READY — MCP — AWS IAM authorizer
- `gateway-quick-start-0cf763` — READY — MCP — custom JWT authorizer

The `gateway-kb-tool` gateway currently exposes Lambda targets including `bluey-credit-tool` and `dynamodb-bluey-sessions-tool`, plus knowledge-base connector targets.

## Lambda functions seen in us-east-1

- `bluey-chat-proxy` — Python 3.12 — 90s timeout — Function URL
- `bluey-account-opening-proxy` — Python 3.12 — 60s timeout — Function URL
- `bluey-banker-api` — Python 3.12 — 30s timeout
- `bluey-document-api` — Python 3.12 — 3s timeout
- `bluey-credit-api` — Python 3.12 — 3s timeout
- `bluey-sessions` — Python 3.12 — 3s timeout

The original setup document refers to `bluey-dynamodb-tool`, but that exact Lambda name did not appear in the current function list. The current Gateway target name is `dynamodb-bluey-sessions-tool`. Treat the live Gateway target configuration as authoritative when resolving the current tool Lambda ARN.

## DynamoDB tables seen in us-east-1

- `bluey-accounts`
- `bluey-applications`
- `bluey-bankers`
- `bluey-credit`
- `bluey-customers`
- `bluey-documents`
- `bluey-messages`
- `bluey-sessions`
- `bluey-transactions`

Key schemas observed:

- `bluey-accounts`: `customerId` + `accountId`
- `bluey-applications`: `reference`
- `bluey-bankers`: `bankerId`
- `bluey-credit`: `customerId` + `sessionId`
- `bluey-customers`: `customerId`
- `bluey-documents`: `sessionId` + `docType`
- `bluey-messages`: `sessionId` + `createdAt#messageId`
- `bluey-sessions`: `sessionId`
- `bluey-transactions`: `accountId` + `date#transactionId`

All observed tables use on-demand billing.

## Authentication / APIs

- Cognito user pool: `bluey-user-pool`
- Cognito group: `Bankers`
- Public SPA client(s) exist in the live pool
- HTTP API: `bluey-api`

## S3 buckets seen

- `bluey-documents`
- `bluey-tables`
- `kb-bluey`
- `physicslens-data-2026`
- `web-activity-store-1`

Only the Bluey-specific buckets should be included in a migration. The others were observed in the account but are unrelated to this backend.

## What still must be captured from AgentCore before a truly identical re-deploy

The live inventory available here exposed runtime metadata but not the complete current Harness definitions (current system prompts, allowed tools, exact Gateway references, and current tool schemas). AWS now provides an AgentCore CLI `export` workflow that is explicitly designed to export a harness to Strands code while preserving model, prompt, tools, memory wiring, skills, and container environment.

Run the one-time capture described in `agentcore/README.md` from a machine authenticated to the source account, then commit the exported harness projects here. This is preferable to trying to reconstruct evolved Harness state from old console notes.

## Migration interpretation

The repository should not be treated as a byte-for-byte export of the current account yet. It is a reproducible infrastructure/application baseline grounded in the supplied implementation log, with explicit capture points for the live Harness definitions and the later credit implementation. This distinction is intentional: using an old ARN or stale tool configuration would make an apparent "migration" less reliable than the current working environment.
