# Bluey authorization model

There are three distinct permission hops. Do not confuse them.

## 1. Proxy Lambda -> Harness

`infra/stacks/api_stack.py` grants the customer-facing proxy roles:

- `bedrock-agentcore:InvokeHarness`
- `bedrock-agentcore:InvokeAgentRuntime`

The resource is restricted to the relevant Harness name prefix. The trailing wildcard is intentional because the runtime endpoint is represented as a sub-resource.

## 2. Harness -> Gateway

`infra/stacks/agentcore_stack.py` creates `bluey-harness-role-*` and grants:

```text
bedrock-agentcore:InvokeGateway
    Resource = the Bluey Gateway ARN
```

Each Harness is configured with that Gateway using IAM outbound authentication.

## 3. Gateway -> backend target

The same AgentCore stack creates a Gateway execution role with:

```text
lambda:InvokeFunction
    -> bluey-sessions
    -> bluey-credit-api

bedrock:GetKnowledgeBase
bedrock:Retrieve
    -> Bluey knowledge bases

bedrock:AgenticRetrieveStream
    -> * (AWS requires this action to be unscoped)
```

The target Lambda functions additionally receive a **resource-based** policy allowing `bedrock-agentcore.amazonaws.com` to invoke them, constrained by the Gateway ARN.

That last resource policy is important: an identity policy on the Gateway role alone is not the complete Lambda authorization chain.

## How to inspect it

```bash
python scripts/verify_permissions.py
```

For deployed stacks, inspect the generated CloudFormation too:

```bash
npx cdk synth --strict
```

Search the synthesized template for `InvokeGateway`, `InvokeFunction`, `InvokeHarness`, and `SourceArn`.
