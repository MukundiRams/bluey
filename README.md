# Bluey Banking Bot — Reproducible AWS Backend

This repository converts the working Bluey backend from a console-built AWS environment into a version-controlled, reproducible project.

## What this repo contains

- Python Lambda implementations for the backend services documented in the Bluey setup log.
- AWS CDK infrastructure for the durable AWS primitives: DynamoDB, S3, Cognito, API Gateway, and Lambda entry points.
- AgentCore configuration/migration notes and a clear separation between **source-of-truth code** and **account-specific generated identifiers**.
- Knowledge Base source documents under `knowledge_base/`; CDK uploads these files to the generated S3 bucket during deployment.
- Deployment scripts designed for separate AWS CLI profiles/accounts.
- Tests for the pure Python/business-logic portions that can run without AWS credentials.

## Important source-of-truth rule

The original backend was built in the AWS Console and documented in `docs/bluey-backend-setup.md`. The live account was also inspected on 2026-09-07. The live environment has evolved beyond the document: it currently contains three AgentCore runtimes, including a later-added credit runtime, and some older resource names from the document are no longer present as Lambda functions.

Therefore this repository does **not** hard-code old account IDs, ARNs, Function URLs, Cognito IDs, or AgentCore resource IDs.

## Target architecture

```text
Customer UI
   |
   +--> bluey-chat-proxy (Lambda Function URL)
   |        |
   |        +--> AgentCore Harness: main banking assistant
   |                 |
   |                 +--> AgentCore Gateway / tools
   |                 +--> Knowledge Base / connectors
   |                 +--> DynamoDB
   |
   +--> bluey-account-opening-proxy (Lambda Function URL)
            |
            +--> AgentCore Harness: account opening
                      |
                      +--> document status / applicant tools
                      +--> S3 + DynamoDB

  +--> bluey-credit-proxy (Lambda Function URL)
        |
        +--> AgentCore Harness: credit guidance
               |
               +--> Credit Gateway / tools
               +--> Credit Knowledge Base

Banker UI
   |
   +--> API Gateway + Cognito JWT
            |
            +--> bluey-banker-api
            +--> bluey-document-api

Shared data
   +--> bluey-customers
   +--> bluey-accounts
   +--> bluey-transactions
   +--> bluey-sessions
   +--> bluey-documents
   +--> bluey-applications
   +--> bluey-messages
   +--> bluey-credit
   +--> bluey-bankers
```

The final documented authentication architecture deliberately keeps the harness-invoking Lambdas on Function URLs because AgentCore cold starts can exceed API Gateway HTTP API's timeout; banker/document APIs remain behind API Gateway + Cognito JWT.

## Deployment environments

For the complete cross-account procedure, see [docs/deploy-to-new-account.md](docs/deploy-to-new-account.md).

Use an AWS CLI profile for each account:

```bash
AWS_PROFILE=bluey-dev ./scripts/deploy.sh dev
AWS_PROFILE=bluey-hackathon ./scripts/deploy.sh hackathon
```

The repository is designed so resources are created in the selected account and references are resolved inside that account.
If your local profile has a different name, override it and verify the account before deploying:

```bash
AWS_PROFILE=mukundi-admin BLUEY_EXPECTED_ACCOUNT_ID=123456789012 ./scripts/deploy.sh hackathon
```

The script also sets `CDK_DEFAULT_REGION`, bootstraps the selected account and region, and prints the account, region, stage, and profile it is about to use. Do not continue if those values are unexpected.

## First-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

The AWS CDK app is Python-based. Node.js is required by the CDK CLI (`npx cdk`) and by the optional AgentCore CLI workflow.

## Deployment order

1. Deploy the durable data/auth/API infrastructure with CDK.
2. Deploy or export/import the AgentCore harness configuration.
3. Configure AgentCore Gateway targets so tool Lambdas are reachable with least-privilege IAM.
4. Seed demo records separately; do not copy production/test PII blindly between accounts.
5. Run the backend smoke tests in `scripts/smoke_test.py`.

Knowledge Base files under `knowledge_base/` are uploaded automatically by the
Knowledge stack deployment. Bedrock ingestion remains a separate command after
deployment because ingestion is asynchronous and a second ingestion cannot start
while one is already running.

The credit Gateway also exposes the recommendation engine through
`recommend_products`. It uses the exported segment and product metadata with the
feature-rich customer records seeded by `scripts/seed_data.py`. The scikit-learn
`.joblib` training artifact is kept under `recommendation_engine/`; exact classifier
inference would require packaging scikit-learn and numpy as a Lambda layer or
container image.

## Security notes

The original hackathon setup used permissive CORS and, during early stages, unauthenticated endpoints. The final documented state used Cognito, but the Function URLs still use `AuthType=NONE` and verify Cognito access tokens inside the Lambda because of AgentCore latency constraints. This repo preserves that design while making the security boundary explicit.

Before production, tighten CORS origins, use separate execution roles, add CloudWatch log retention/encryption, scope resource policies with source-account/source-ARN conditions, and avoid Scan-based identity lookup at scale.

## AgentCore permission map

The critical authorization chains are defined in `infra/stacks/platform_stack.py`:

```text
main Harness role
  └─ bedrock-agentcore:InvokeGateway → main Gateway
    ├─ gateway role: lambda:InvokeFunction → bluey-sessions
    ├─ gateway role: bedrock:GetKnowledgeBase/Retrieve → main Knowledge Base
    └─ Lambda resource policy restricted to the main Gateway ARN

credit Harness role
  └─ bedrock-agentcore:InvokeGateway → credit Gateway
    ├─ gateway role: lambda:InvokeFunction → bluey-credit-api
    ├─ gateway role: bedrock:GetKnowledgeBase/Retrieve → credit Knowledge Base
    └─ Lambda resource policy restricted to the credit Gateway ARN

account-opening Harness role
  └─ bedrock-agentcore:InvokeGateway → account-opening Gateway
    ├─ gateway role: lambda:InvokeFunction → bluey-sessions
    ├─ gateway role: bedrock:GetKnowledgeBase/Retrieve → account-opening Knowledge Base
    └─ Lambda resource policy restricted to the account-opening Gateway ARN

chat proxy role
  └─ bedrock-agentcore:InvokeAgentRuntime + InvokeHarness → main Harness

account-opening proxy role
  └─ bedrock-agentcore:InvokeAgentRuntime + InvokeHarness → account-opening Harness
```

This is intentionally split between **identity policies** and **Lambda resource policies**: both sides matter for Gateway → Lambda invocation.

## Verification

```bash
python scripts/verify_permissions.py
python scripts/stack_outputs.py --stage hackathon
```

Then deploy and run:

```bash
AWS_PROFILE=bluey-hackathon ./scripts/deploy.sh hackathon
python scripts/stack_outputs.py --stage hackathon
```

For Knowledge Base content, copy the source S3 bucket into the newly-created KB bucket with `scripts/migrate_kb_files.sh`, then run `scripts/ingest_knowledge_bases.py` with the IDs printed by `stack_outputs.py`.
