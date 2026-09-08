# Migration checklist

- [x] Separate infrastructure from account-specific IDs
- [x] Recreate core DynamoDB table schemas
- [x] Recreate private S3 document storage
- [x] Recreate Cognito user pool + Bankers group + public client
- [x] Recreate customer chat proxy pattern
- [x] Recreate account-opening proxy pattern
- [x] Recreate banker/document API logic
- [x] Preserve AgentCore Gateway Lambda event/tool-name behavior
- [x] Preserve session `update_item` overwrite fix
- [x] Preserve Cognito split between access token (Function URL) and ID token/API Gateway
- [x] Preserve chart-data handoff via `pendingChartData`
- [x] `cdk synth` succeeds for both `dev` and `hackathon` stages (verified — see below)
- [x] Merge BlueyApi + BlueyAgentCore into BlueyPlatform to resolve a genuine circular
      stack dependency (see `infra/stacks/platform_stack.py` module docstring)
- [x] Fix `bluey-bankers` IAM grant gap that would have broken `lookup_customer`'s
      general-banker assignment (AccessDeniedException)
- [x] Fix several real CDK API-usage bugs, all confirmed via actual `cdk synth` runs:
      `FunctionUrlOptions` vs `FunctionUrlCorsOptions`, `HttpJwtAuthorizer`'s positional
      `jwt_issuer` arg, `FunctionUrl.url` vs a nonexistent `Function.function_url`,
      raw `CfnResource.attr_arn` vs the real typed `CfnGateway`/`CfnHarness`/
      `CfnRuntime`/`CfnKnowledgeBase` constructs and their actual `attr_*` accessors
- [x] Fix `deploy.sh` never propagating `$STAGE` into CDK context (was silently always
      deploying as `stage=dev`, including that stage's `RemovalPolicy.DESTROY`)
- [x] Fix `tests/test_handlers.py` pointing at a nonexistent Lambda path (crashed on
      import rather than testing anything)
- [x] Fix `verify_permissions.py` (bogus placeholder role name crashed it immediately;
      unpaginated `list_roles()`) and `stack_outputs.py` (was missing 2 of 5 stacks)
      and `auth_stack.py` (no CfnOutput existed for the Cognito User Pool/App Client
      IDs at all)
- [x] Fix `capture_harnesses.py` defaulting to hardcoded harness IDs from the old
      account, contradicting this repo's own no-hardcoded-IDs principle
- [ ] Capture current live Harness definitions via `agentcore export harness`
      (still needed — this repo's Harness *definitions* are written fresh from the
      historical implementation log, not exported from the live account; the live
      account's system prompts, exact tool wiring, and any manual tuning since may
      differ from what's encoded in `config/prompts/*.txt`)
- [ ] Capture current live Gateway target Lambda ARNs/tool schemas
- [ ] Import the live `bluey-credit-api` implementation (a `lambda/credit_api/handler.py`
      exists here, reconstructed from the live-inventory findings, but has not been
      diffed against the actual deployed version)
- [ ] Export/import the current credit Harness configuration
- [ ] Decide which KB/connectors are required for the hackathon account
- [x] Create separate `main/`, `credit/`, and `account-opening/` Knowledge Base
      prefixes and data sources; main content is organized under `policy/`, `faqs/`,
      and `product-catalogue/`
- [x] Enforce per-Harness Gateway isolation with separate Gateway roles, Lambda
      resource policies, Knowledge Base targets, and restricted tool schemas
- [x] Add authenticated credit Harness proxy Function URL and frontend contract
- [ ] Seed synthetic/demo data explicitly in the hackathon account
- [ ] Run full end-to-end chat, verification, account lookup, document, banker, and
      credit smoke tests against a real deployed stack (everything above was verified
      via `cdk synth` + local unit tests only — no live AWS deployment was performed
      or possible from this environment)
- [ ] DECISION NEEDED, not yet made: `bluey-messages` and `bluey-applications` tables
      exist in `data_stack.py` (matching what was observed live) but nothing in any
      Lambda handler reads or writes either one. Two real design questions this repo
      does not answer for you: (1) should message history move from the embedded
      `messages` array on `bluey-sessions` items to one-row-per-message in
      `bluey-messages`? (2) should the banker review queue read from
      `bluey-applications` instead of scanning `bluey-sessions` for
      `reviewStatus=pending_review`? Deliberately left unimplemented rather than
      guessed at, since it changes data flow the live account may already depend on.
