# Repository provenance

This repository is intentionally split into three evidence levels.

1. **Live-account inventory**: resource names, statuses, table key schemas, Function URL settings, Cognito pool/group presence, and AgentCore runtime/gateway presence were inspected through the connected AWS account on 2026-09-07.
2. **Historical implementation source**: Lambda logic, tool schemas, system-prompt decisions, and troubleshooting discoveries come from `bluey-backend-setup.md`, the supplied Bluey integration log.
3. **Migration inference**: CDK structure, environment separation, and capture/export helpers are new repository structure intended to make the system reproducible. They are not claimed to be an export of the old console-created CloudFormation stack.

Where evidence conflicts, the live account wins for current resource names/configuration; the historical log is preserved for behavioural intent and known fixes.
