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
- [ ] Capture current live Harness definitions via `agentcore export harness`
- [ ] Capture current live Gateway target Lambda ARNs/tool schemas
- [ ] Import the live `bluey-credit-api` implementation
- [ ] Export/import the current credit Harness configuration
- [ ] Decide which KB/connectors are required for the hackathon account
- [ ] Seed synthetic/demo data explicitly in the hackathon account
- [ ] Run full end-to-end chat, verification, account lookup, document, banker, and credit smoke tests
