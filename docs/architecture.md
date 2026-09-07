# Bluey architecture and migration rationale

## 1. Conversation path

The customer-facing chat path uses a Lambda Function URL rather than API Gateway because AgentCore Harness cold starts can exceed 29 seconds. The documented implementation therefore validates Cognito access tokens inside the proxy Lambda and then calls `invoke_harness`.

The proxy owns session persistence: it reads the existing session, appends the user message, invokes the harness, appends the assistant response, and updates DynamoDB. It deliberately uses `update_item` for the final write so tool-written attributes are not erased.

## 2. Existing-customer verification

Customer identity is verified against `bluey-customers` using the customer's SA ID number. A successful lookup writes `customerId`, `verified`, and `reviewStatus` to the current session.

## 3. Banking tools

Balance and transaction access is available after verification. Accounts are discovered by customer ID and transactions by account ID.

The chart path writes temporary `pendingChartData` into the session so the proxy can return structured chart data to the frontend, then removes the temporary attribute in the same update.

## 4. Account opening

Account opening is a separate AgentCore Harness. It collects applicant information, orchestrates ID/proof-of-address uploads, checks document status, and flags the session for banker review. Documents are stored privately in S3 and are exposed to bankers with short-lived pre-signed URLs.

## 5. Banker workflow

The banker API lists pending-review sessions and supports detail/approve/reject. Existing customers are resolved through `customerId`; new applicants use the applicant fields stored on the session.

## 6. Authentication

The final documented split is:

| Service | Entry point | Auth |
| --- | --- | --- |
| chat proxy | Lambda Function URL | manual Cognito access-token validation |
| account-opening proxy | Lambda Function URL | manual Cognito access-token validation |
| banker API | HTTP API | Cognito JWT + Bankers group |
| document API | HTTP API | Cognito JWT + banker-only action checks |

## 7. Data ownership

The repository treats infrastructure as code, but demo records as data. Tables should be recreated by CDK/CloudFormation, while seeded data is loaded explicitly by a separate script so moving to the hackathon account never silently copies arbitrary source-account data.
