# Security and hackathon boundaries

This project handles simulated banking data and documents. Treat it as a hackathon/demo system, not a production banking platform.

## Authentication

Customer and banker clients authenticate with Cognito. The harness-invoking Function URLs are deliberately public at the transport layer (`AuthType=NONE`) because the Lambda performs Cognito access-token validation before calling AgentCore. Banker/document APIs are protected by an API Gateway JWT authorizer and an additional `Bankers` group check in the banker-facing handlers.

The supplied setup notes specifically document an API Gateway 29-second ceiling interacting badly with AgentCore Harness cold starts, which is why the customer-facing harness calls use Function URLs.

## Data separation

Infrastructure code recreates empty tables. Demo/customer data is intentionally not embedded in the infrastructure stack and should be seeded explicitly.

Do not copy source-account users, tokens, uploaded identity documents, or real secrets into the hackathon account.

## Resource-based permissions

AgentCore Gateway Lambda targets need a Lambda resource-based permission allowing Gateway invocation. The exact modern AWS guidance should be followed when the target is recreated: scope the permission to the Gateway role/source account/source ARN rather than using an unrestricted principal where possible.

## S3

The document bucket blocks public access, requires SSL, and uses versioning. Documents are exposed to bankers only through short-lived pre-signed GET URLs.

## Before production

Replace wildcard CORS with the real frontend origins; require stronger authentication; add log retention and encryption; use least-privilege IAM; eliminate Scan-based lookups where appropriate; add audit logging and monitoring; validate all agent-generated tool parameters; and review the document-upload threat model.
