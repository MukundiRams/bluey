# Bluey — End-to-End Architecture

Bluey is a multi-agent banking assistant built entirely on AWS. It serves two audiences — banking **customers** and **bankers** — through separate frontends, routes conversations to purpose-built AI agents running on Amazon Bedrock AgentCore, and grounds those agents in banking tools and knowledge bases. This document explains the full system as it is implemented in the CDK infrastructure and Lambda code.

> Note on scope: this document reflects the code in `infra/` and `lambda/`. The current implementation has **four** AI agents (main, credit, account-opening, financial-advice), each with its own AgentCore Gateway, Runtime, Harness, and Knowledge Base. Earlier diagrams and docs describe three; the fourth (financial-advice) was added later.

## 1. High-level overview

```text
                         ┌───────────────────┐
  Customer Frontend ───► │  Amazon Cognito    │ ◄─── Banker Frontend
  (web/mobile chat)      │  User Pool + JWT   │      (web portal, Bankers group)
                         └─────────┬─────────┘
                                   │  tokens
        ┌──────────────────────────┼───────────────────────────┐
        │                          │                            │
        ▼ (Function URLs)          ▼ (Function URL)             ▼ (HTTP API + JWT)
  chat / credit /            account-opening              banker-api / document-api
  financial-advice proxies       proxy
        │                          │                            │
        ▼                          ▼                            ▼
  ┌───────────────────────────────────────────┐         DynamoDB tables
  │        Amazon Bedrock AgentCore            │         + S3 documents
  │  4 Runtimes • 4 Harnesses • 4 Gateways     │
  │  each Harness → its Gateway → tools + KB   │
  └───────────────────────────────────────────┘
        │                    │
        ▼                    ▼
  Lambda tools          Bedrock Knowledge Bases
  (DynamoDB, credit)    (managed, S3-backed RAG)
```

The system separates two things deliberately:

- **Infrastructure as code** — all durable AWS primitives (tables, buckets, Cognito, API Gateway, Lambdas, AgentCore resources) are defined in CDK and recreated per account/stage.
- **Data as data** — demo records and knowledge-base content are loaded by explicit scripts, never silently copied between accounts.

## 2. Frontends and authentication

Both frontends authenticate against a single **Amazon Cognito User Pool** (`BlueyAuthStack`). The pool signs in by email, self-service sign-up is enabled, and MFA is off for the demo. It issues JWT access/ID tokens used by every downstream service.

- **Customers** are ordinary pool users.
- **Bankers** belong to a Cognito group named `Bankers`. Banker-only actions check group membership in addition to a valid token.
- A public SPA app client (`user_password` auth flow, no secret) is used by the browser frontend.

The User Pool ID, App Client ID, and group name are emitted as CloudFormation outputs so the frontend can be configured without console digging.

## 3. Entry points: why two different patterns

The system uses **two intentionally different entry-point styles**, chosen by latency characteristics.

### 3a. Harness-invoking proxies → Lambda Function URLs

The agent proxies (`chat`, `credit`, `account-opening`, `financial-advice`) each sit behind a **Lambda Function URL** with `AuthType=NONE`. This is deliberate: AgentCore harness cold starts can exceed the 29-second hard timeout of API Gateway HTTP API integrations (cold starts up to ~60s have been observed). Function URLs avoid that ceiling; these Lambdas have a 90-second timeout.

Because the URL auth type is `NONE`, each proxy **validates the Cognito access token itself** by calling `cognito-idp:GetUser` with the bearer token, extracting the user's `sub`. Requests without a valid token get a `401`.

### 3b. Banker/document APIs → HTTP API + Cognito JWT

The `banker-api` and `document-api` sit behind an **API Gateway HTTP API** with a Cognito JWT authorizer (`/banker`, `/documents`). These are fast, synchronous CRUD-style endpoints that fit comfortably inside the HTTP API timeout, so they use the platform-native JWT authorizer plus banker-group checks.

| Service | Entry point | Auth |
| --- | --- | --- |
| chat proxy | Lambda Function URL | manual Cognito access-token validation |
| credit proxy | Lambda Function URL | manual Cognito access-token validation |
| account-opening proxy | Lambda Function URL | manual Cognito access-token validation |
| financial-advice proxy | Lambda Function URL | manual Cognito access-token validation |
| banker API | HTTP API | Cognito JWT + Bankers group |
| document API | HTTP API | Cognito JWT + banker-only action checks |

CORS is currently permissive (`*`) for the demo and should be tightened before production.

## 4. Conversation flow (the chat proxy)

The customer chat proxy is representative of all four agent proxies. For a single turn it:

1. **Verifies the token** — extracts the bearer token and calls Cognito `get_user`; rejects with `401` if invalid.
2. **Resolves the harness ARN** — read from the `HARNESS_ARN` environment variable (wired directly at deploy time; an SSM fallback exists but is unused in the merged stack).
3. **Loads the session** — reads the existing item from `bluey-sessions` by `sessionId` (a new UUID if none provided) and appends the user message to the stored `messages` list.
4. **Invokes the harness** — calls `invoke_harness` with the prompt (augmented with the `session_id` so tools can correlate) and streams the response, concatenating `contentBlockDelta` text chunks.
5. **Persists the turn** — appends the assistant reply and writes back with `update_item` (not `put_item`), so attributes written by tools during the turn are preserved rather than overwritten. It uses `if_not_exists` for `userId` and `createdAt`.
6. **Returns structured extras** — if a tool wrote temporary `pendingChartData` onto the session, the proxy returns it as `chartData` in the response and removes it from the session in the same update.

This "proxy owns session persistence, tools own their own attributes" split is why `update_item` is used throughout.

## 5. The AI layer: Bedrock AgentCore

All conversational intelligence runs on **Amazon Bedrock AgentCore**, defined in `BlueyPlatformStack`. There are four parallel agents, each a full stack of four resources:

| Agent | Purpose |
| --- | --- |
| **main** | General banking assistant: verification, balances, transactions, spending charts, product/policy/FAQ knowledge |
| **credit** | Credit guidance: loan products, credit scores, eligibility, repayment maths, product recommendations |
| **account-opening** | New-customer onboarding: applicant info, document status, banker-review flagging |
| **financial-advice** | Financial-wellness guidance grounded in responsible-advice knowledge |

Each agent is composed of:

- **Runtime** (`CfnRuntime`) — a public-network container (shared harness image, `us-east-1`) that hosts the agent. Sessions idle out at 900s; max lifetime 8h.
- **Harness** (`CfnHarness`) — the agent definition: Claude Sonnet 4.5 via `converse_stream`, a system prompt loaded from `config/prompts/*.txt`, the assigned Gateway as its tool source, `max_iterations=75`, a 3600s timeout, and a sliding-window truncation of 150 messages.
- **Gateway** (`CfnGateway`) — an MCP gateway (AWS IAM authorizer, semantic search) that exposes the agent's tools and knowledge base.
- **Managed memory** — each harness is created with `managedMemoryConfiguration` (SEMANTIC + SUMMARIZATION strategies, 30-day event expiry), giving the agent its own conversational memory.

Harness creation is serialized (main → credit → account-opening → financial-advice) because AgentCore provisions managed memory during creation and parallel provisioning caused service-side transaction conflicts.

### Model and image

- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Harness image: a public ECR image, pinned in the stack
- Region: `us-east-1` (enforced — the stack raises if deployed elsewhere)

## 6. Gateways, tools, and knowledge (RAG)

Each Gateway routes agent tool calls to two kinds of **targets**:

### Lambda tool targets

Tools are declared with an inline MCP tool schema and backed by Lambda functions:

- **`bluey-sessions`** (the DynamoDB tool) backs the main, account-opening, and financial-advice gateways. Different agents see different subsets of its tools:
  - main / financial-advice: `lookup_customer`, `get_accounts`, `get_transactions`, `get_transaction_chart`
  - account-opening: `check_documents_status`, `save_applicant_info`
- **`bluey-credit-api`** backs the credit gateway: `get_loan_products`, `get_credit_score`, `check_credit_eligibility`, `calculate_repayment`, `recommend_products`.

### Knowledge Base targets (RAG)

Each gateway also has a **Bedrock Knowledge Base connector** target exposing a `Retrieve` operation. The agent supplies only the query text; `numberOfResults` is intentionally omitted from the exposed parameters so Bedrock uses its own default (the connector previously passed it as a string, which the Retrieve API rejected).

Knowledge Bases (`BlueyKnowledgeStack`) are **managed** Bedrock KBs — Bedrock selects and operates the embedding model. Source content lives in a single S3 bucket, split by prefix:

- `main/` — policy, FAQs, product catalogue → main KB (also reused by financial-advice)
- `credit/` — credit FAQs/products → credit KB
- `account-opening/` — onboarding docs → account-opening KB

Content is uploaded to S3 at deploy time via `BucketDeployment`. Bedrock ingestion is run separately afterward because it is asynchronous and cannot run two ingestions concurrently.

## 7. Data layer

### DynamoDB (application data, `BlueyDataStack`)

All tables are on-demand billing, retained on non-dev stages, and destroyed on dev.

| Table | Key | Holds |
| --- | --- | --- |
| `bluey-sessions` | `sessionId` | Conversation state, messages, verification flags, temporary chart data |
| `bluey-customers` | `customerId` | Customer records |
| `bluey-accounts` | `customerId` + `accountId` | Bank accounts |
| `bluey-transactions` | `accountId` + `date#transactionId` | Transaction history |
| `bluey-documents` | `sessionId` + `docType` | Uploaded document metadata |
| `bluey-applications` | `reference` | Account-opening applications |
| `bluey-bankers` | `bankerId` | Banker records |
| `bluey-credit` | `customerId` + `sessionId` | Credit interactions |
| `bluey-messages` | `sessionId` + `createdAt#messageId` | Chat message history |
| `bluey-banker-read-state` | `bankerId` + `itemId` | Banker read/seen tracking |

### S3

- **Documents bucket** — private (all public access blocked, SSE, TLS-enforced, versioned). Identity/proof documents are stored here and exposed to bankers only via short-lived pre-signed URLs.
- **Credit data bucket** — private, read by the credit API for recommendation metadata.
- **Knowledge bucket** — private, holds KB source content by prefix.

## 8. Banker and account-opening workflows

- **Account opening**: the account-opening agent collects applicant info, orchestrates ID/proof-of-address uploads, checks document status, and flags the session for banker review. Documents stay private in S3.
- **Banker workflow**: the `banker-api` (HTTP API + JWT + Bankers group) lists pending-review sessions and supports detail/approve/reject. Existing customers resolve via `customerId`; new applicants use the applicant fields stored on the session. The `document-api` handles private uploads/reads plus metadata and the review flag.

## 9. IAM and permissions

Authorization is intentionally split between **identity policies** and **resource policies**, because Gateway→Lambda invocation requires both sides.

```text
proxy role  ──InvokeAgentRuntime + InvokeHarness──►  its Harness (scoped ARN + wildcard)

Harness role ──InvokeGateway──► its Gateway
   Gateway role ──lambda:InvokeFunction──► its tool Lambda(s)
   Gateway role ──bedrock:GetKnowledgeBase / Retrieve──► its Knowledge Base
   tool Lambda resource policy ──allows bedrock-agentcore principal, source-ARN = that Gateway──
```

Additional least-privilege grants:

- Harness roles: pull the ECR-public image, write runtime logs, invoke the Claude model, and access their own AgentCore managed memory (`memory/*` scoped to the account).
- Lambda execution roles get only the table/bucket grants they need (e.g. banker role reads most tables; document role read-writes documents + applications; tool role read-writes all tables; proxy roles read-write sessions/messages/applications and can call `cognito-idp:GetUser`).

Because AgentCore validates a gateway role's access to a target at creation time, the stack adds explicit dependencies so the role's inline policy and the Lambda resource permission exist (and have propagated in IAM) before targets are created.

## 10. Why one merged platform stack

API and AgentCore resources live in a single `BlueyPlatformStack`. They were originally two stacks but had a genuine **circular stack dependency**: the proxy Lambdas need the Harness ARN, while the Gateway role needs `InvokeFunction` on those Lambdas and the Lambda resource policy needs the Gateway ARN. CloudFormation cannot deploy two mutually dependent stacks (`cdk synth` fails with a DependencyCycle). Within one stack, CloudFormation orders the individual resources fine. A bonus: the SSM indirection that previously passed the Harness ARN across stacks is no longer needed — the ARN is now a direct same-stack environment variable.

Stack layout:

- `BlueyDataStack` — DynamoDB tables + S3 buckets
- `BlueyAuthStack` — Cognito User Pool, Bankers group, SPA client
- `BlueyKnowledgeStack` — Knowledge bucket, managed KBs, data sources
- `BlueyPlatformStack` — Lambdas, HTTP API, Function URLs, Gateways, Runtimes, Harnesses, IAM wiring

## 11. Deployment and environments

Two stages (`dev`, `hackathon`) map to separate AWS profiles/accounts in `config/environments.yaml`. Deployment uses per-account CLI profiles:

```bash
AWS_PROFILE=bluey-dev ./scripts/deploy.sh dev
AWS_PROFILE=bluey-hackathon ./scripts/deploy.sh hackathon
```

The deploy script sets the region, bootstraps the account, and prints account/region/stage/profile before proceeding. Recommended order: deploy CDK infra → deploy/wire AgentCore harnesses → confirm Gateway targets → seed demo data separately → run smoke tests.

## 12. Design principles

- **Secure by boundary** — Cognito everywhere; private S3 with pre-signed access; least-privilege IAM split across identity and resource policies.
- **Multi-agent** — four specialized agents, each isolated to its own Gateway, tools, KB, and memory.
- **Latency-aware** — Function URLs for slow harness invocation, HTTP API for fast CRUD.
- **AWS-native** — Cognito, API Gateway, Lambda, DynamoDB, S3, Bedrock AgentCore + Knowledge Bases.
- **Reproducible** — infrastructure recreated by CDK per account; data loaded explicitly, never copied blindly.

### Hardening before production

Tighten CORS to specific origins, add CloudWatch log retention/encryption, scope resource policies with source-account/source-ARN conditions, and replace any Scan-based identity lookups with indexed queries.
