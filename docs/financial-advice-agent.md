# Financial Advice Agent - Implementation Guide

## Overview

The Financial Advice Agent (`bluey-financial-advice-proxy`) is a new AI-powered financial wellness advisor that helps Standard Bank customers analyze their spending patterns and receive responsible financial guidance. This agent strictly adheres to South African financial regulations (FAIS, POPIA, FICA, KYC) and emphasizes AI-generated guidance disclaimers.

## Architecture

The financial advice agent follows the same architecture pattern as the existing chat, credit, and account-opening agents:

```
Customer UI
   |
   +--> bluey-financial-advice-proxy (Lambda Function URL)
            |
            +--> AgentCore Harness: financial wellness advisor
                      |
                      +--> AgentCore Gateway
                      +--> Sessions Tools (lookup_customer, get_accounts, get_transactions, get_transaction_chart)
                      +--> Knowledge Base (Standard Bank main KB with retrieval)
                      +--> DynamoDB Sessions Table
```

## Components

### 1. Lambda Function: `bluey-financial-advice-proxy`
**Location**: `lambda/bluey-financial-advice-proxy/handler.py`

- Handles HTTP requests via AWS Lambda Function URL
- Verifies customer tokens via Cognito
- Invokes the AgentCore harness with user prompts
- Stores conversation history in DynamoDB sessions table
- Returns AI-generated advice with transaction chart data if available

**Key Environment Variables**:
- `HARNESS_ARN`: ARN of the financial advice harness
- `SESSIONS_TABLE`: DynamoDB table for session storage
- Cognito verification for token validation

### 2. System Prompt: `config/prompts/financial-advice.txt`
**Key Features**:
- **Regulatory Compliance**: Follows FAIS (Financial Advisory and Intermediary Services Act) principles
- **Safety Disclaimers**: Every substantive response includes:
  - AI-generated guidance disclaimer
  - Not a replacement for licensed financial advisor (FSP)
  - Recommendation to consult certified financial advisor
  - FAIS compliance statement
- **Prohibited Activities**:
  - NO gambling or betting advice
  - NO cryptocurrency or forex trading recommendations
  - NO high-risk derivative products
  - NO penny stocks or day trading advice
  - NO guaranteed return promises
- **Capabilities**:
  - Transaction analysis and spending pattern identification
  - Cost-cutting recommendations
  - Budget management guidance
  - Emergency fund planning
  - Debt management strategies
  - Savings recommendations
  - Safe financial wellness education

### 3. AgentCore Gateway: `financial-advice`
- **Tools**: Sessions lambda (lookup_customer, get_accounts, get_transactions, get_transaction_chart)
- **Knowledge Base**: Main Standard Bank KB (with retrieval enabled)
- **Purpose**: Provides verified customer data and banking knowledge for financial advice

### 4. AgentCore Runtime: `bluey_financial_advice_{stage}`
- Containerized Claude execution environment
- Public network mode (same as other harnesses)
- Session timeout: 900 seconds (15 minutes)
- Max lifetime: 28800 seconds (8 hours)

### 5. AgentCore Harness: `bluey_financial_advice_{stage}`
- **Model**: Claude Sonnet 4.5 (us.anthropic.claude-sonnet-4-5-20250929-v1:0)
- **System Prompt**: Financial advice prompt with regulatory compliance
- **Tools**: Gateway + Knowledge Base access
- **Timeout**: 3600 seconds (1 hour)
- **Max Iterations**: 75

## Integration Points

### Infrastructure (CDK)
**File**: `infra/stacks/platform_stack.py`

Added:
- `financial_advice_role`: Execution role with Cognito, sessions table, and harness invocation permissions
- `self.financial_advice_fn`: Lambda function definition
- `self.financial_advice_function_url`: Public HTTPS endpoint for the lambda
- `financial_advice_gateway`: AgentCore Gateway with sessions tools
- `financial_advice_harness_role`: Harness execution role
- `financial_advice_harness`: Claude harness with financial advice prompt
- `runtimes["financial-advice"]`: AgentCore runtime for the harness

**CloudFormation Outputs**:
- `FinancialAdviceFunctionUrl`: Public endpoint URL
- `FinancialAdviceGatewayArnOutput`: Gateway ARN
- `FinancialAdviceHarnessArnOutput`: Harness ARN

### Configuration
**File**: `config/resource-names.yaml`

Added:
```yaml
lambda:
  financial_advice_proxy: bluey-financial-advice-proxy
```

### Testing Scripts
**Files**: `scripts/smoke_test.py`, `scripts/smoke_agentcore.py`

Updated to support:
- `--harness financial-advice` option in smoke_agentcore.py
- `FINANCIAL_ADVICE_URL` environment variable in smoke_test.py
- Sample financial advice queries for testing

## Usage

### Direct Harness Invocation (for testing)
```bash
python scripts/smoke_agentcore.py --harness financial-advice --prompt "Help me cut my spending"
```

### Via Lambda Function URL (from frontend)
```bash
curl -X POST https://<function-url>/
  -H "Authorization: Bearer <cognito-token>"
  -H "Content-Type: application/json"
  -d '{"prompt": "Analyze my spending and suggest where I can cut costs", "session_id": "abc123"}'
```

**Response**:
```json
{
  "reply": "I can help you analyze your spending! [DISCLAIMER: This is AI-generated guidance...] Let me pull up your recent transactions.",
  "session_id": "abc123",
  "chartData": {...}  // optional spending chart
}
```

### Via Smoke Test
```bash
export FINANCIAL_ADVICE_URL=https://<function-url>/
export COGNITO_ACCESS_TOKEN=<token>
python scripts/smoke_test.py
```

## Key Differences from Other Agents

| Feature | Main Chat | Credit | Financial Advice |
|---------|-----------|--------|-----------------|
| **Purpose** | General banking assistance | Credit products & eligibility | Spending analysis & wellness |
| **Knowledge Base** | Main KB (FAQ, policy, products) | Credit KB | Main KB (with retrieval) |
| **Tools** | lookup_customer, get_accounts, get_transactions, get_transaction_chart | credit-specific tools | lookup_customer, get_accounts, get_transactions, get_transaction_chart |
| **Regulatory Focus** | General banking | Credit regulations | FAIS, POPIA, FICA compliance |
| **Disclaimers** | Professional bank assistant | Standard credit guidance | Strong AI limitations & legal disclaimers |
| **Prohibited Actions** | None (general assistance) | Credit misconduct | Gambling, trading, risky investments |

## Safety & Compliance

### Regulatory Framework
- **FAIS** (Financial Advisory and Intermediary Services Act): Principles of care and suitability
- **POPIA** (Protection of Personal Information Act): Customer data privacy
- **FICA** (Financial Intelligence Centre Act): AML/KYC awareness
- **Standard Bank Policy**: Responsible lending and financial wellness

### Built-in Safeguards
1. **AI Disclaimer Required**: Every financial advice response must include the AI-generated guidance disclaimer
2. **Licensed Advisor Referral**: Always recommend professional consultation for major decisions
3. **Activity Blocklist**: Hard refuse gambling, trading, and risky financial activities
4. **Verification Gate**: Customer identity verified before accessing transaction data
5. **Data Privacy**: Sessions isolated by customer ID, no cross-customer data sharing

### Example Conversation Flow
```
Customer: "I want to make money quick. Can I trade crypto?"
Agent: "I appreciate your interest in growing your wealth, but I can't provide guidance on cryptocurrency trading or any high-risk speculative activities. This is AI-generated advice and not a replacement for speaking with a licensed financial advisor at Standard Bank who can understand your full financial picture.

Instead, I'd recommend speaking with our banking team about proven wealth-building strategies like:
- Emergency fund establishment
- Regular savings plans
- Standard Bank investment products registered with appropriate authorities

Would you like help analyzing your current spending or creating a savings plan?"
```

## Deployment

### Prerequisites
- AWS CDK environment set up
- Cognito user pool configured
- DynamoDB sessions table created
- Standard Bank main knowledge base deployed

### Deploy
```bash
cd infra
cdk deploy BlueyPlatform-{stage}
```

The financial advice harness and related resources will be created automatically.

### Verification
```bash
# Check outputs
aws cloudformation describe-stacks --stack-name BlueyPlatform-dev --query 'Stacks[0].Outputs[?OutputKey==`FinancialAdviceFunctionUrl`]'

# Test the endpoint
FINANCIAL_ADVICE_URL=$(aws cloudformation describe-stacks --stack-name BlueyPlatform-dev --query 'Stacks[0].Outputs[?OutputKey==`FinancialAdviceFunctionUrl`].OutputValue' --output text)
curl -X POST "$FINANCIAL_ADVICE_URL" -H "Authorization: Bearer $TOKEN" -d '{"prompt":"Hello"}'
```

## Monitoring & Debugging

### CloudWatch Logs
- Lambda logs: `/aws/lambda/bluey-financial-advice-proxy`
- Harness logs: AgentCore runtime logs (check CloudWatch for harness activity)

### Session History
All conversations are stored in DynamoDB:
```bash
aws dynamodb get-item --table-name bluey-sessions --key '{"sessionId": {"S": "session-id"}}'
```

### Testing Directly with AgentCore
```bash
python scripts/smoke_agentcore.py --harness financial-advice --prompt "Should I invest in stocks?"
```

## Future Enhancements

1. **Transaction Classification**: Improve automated spending category detection
2. **Budget Templates**: Provide South African-specific budget templates
3. **Integrated Financial Education**: Link to Standard Bank resources and certification programs
4. **Personalized Recommendations**: Based on account type and customer segment
5. **Multi-Language Support**: Add Zulu, Xhosa, Afrikaans support for broader reach
6. **Savings Goal Tracking**: Help customers set and monitor financial goals

## Troubleshooting

### Issue: "Harness ARN is not configured"
- Ensure CDK deployment completed successfully
- Check CloudFormation outputs for FinancialAdviceHarnessArnOutput
- Verify Lambda environment variables include HARNESS_ARN

### Issue: "Unauthorized" response
- Verify Cognito token is valid and not expired
- Ensure Authorization header format: "Bearer {token}"
- Check Cognito user pool configuration

### Issue: Agent refusing to answer financial questions
- Review system prompt in `config/prompts/financial-advice.txt`
- Verify customer is verified via lookup_customer tool
- Check CloudWatch logs for tool invocation errors

### Issue: Cold start timeouts (>60 seconds)
- Expected behavior for AgentCore harnesses
- Function URL timeouts set to 90 seconds to accommodate
- Consider warming strategies for production

---

**Last Updated**: 2025-09-09
**Responsible Agent Addition**: Follows all best practices for financial advisory compliance in South Africa
