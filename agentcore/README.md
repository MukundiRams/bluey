# AgentCore configuration and migration

## Current live Harnesses

The 2026-09-07 live inventory contains three READY AgentCore runtimes:

- `harness_harness_nxkh1` — version 10 — main Bluey conversation path
- `harness_bluey_credit_harness` — version 3 — credit flow
- `harness_bluey_account_opening_harness` — version 7 — account opening

The original setup notes contain the account-opening system prompt and the main banking tool behaviour, but the live credit Harness was added later and its complete configuration is not present in those notes.

## Preferred migration method: export the live Harness

AWS now provides an AgentCore Harness export command that converts a Harness into Strands-based code while preserving the model, prompt, tools, memory wiring, skills, and container environment.

Install the CLI:

```bash
npm install -g @aws/agentcore
```

Then, from the source account:

```bash
agentcore export harness --arn <MAIN_HARNESS_ARN> --output ./agentcore/exported/main
agentcore export harness --arn <CREDIT_HARNESS_ARN> --output ./agentcore/exported/credit
agentcore export harness --arn <ACCOUNT_OPENING_HARNESS_ARN> --output ./agentcore/exported/account-opening
```

This repository also includes `scripts/capture_harnesses.py`, which uses the AgentCore control-plane API to capture the current Harness JSON configuration before or alongside CLI export.

```bash
python scripts/capture_harnesses.py
```

## Why both capture methods are useful

`get_harness` gives a precise configuration snapshot for auditing and comparison. The CLI export is the migration mechanism: it produces code that can be version-controlled and redeployed as an AgentCore Runtime agent.

## Account switching

Keep AWS account credentials outside the repository. Use profiles:

```bash
AWS_PROFILE=bluey-dev agentcore deploy
AWS_PROFILE=bluey-hackathon agentcore deploy
```

Do not commit real Function URLs, Cognito IDs, access tokens, API keys, OAuth secrets, or signed URLs.
