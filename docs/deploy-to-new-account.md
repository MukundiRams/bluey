# Deploy Bluey to a New AWS Account

This guide deploys Bluey into a different AWS account and region using AWS CDK.
The deployment is account-independent: do not copy ARNs, Lambda URLs, Cognito IDs,
table ARNs, bucket names, Gateway IDs, or Harness IDs from the old account.
CDK creates new resources and connects them using references inside the new account.

## What CDK creates automatically

The deployment creates:

- DynamoDB tables and their Lambda access permissions
- Private S3 buckets for documents and credit data
- Cognito User Pool, public App Client, and `Bankers` group
- Banker and document HTTP API with Cognito JWT authorization
- Chat and account-opening Lambda Function URLs
- Lambda execution roles and CloudWatch basic logging permissions
- AgentCore Gateway, Gateway targets, runtimes, Harnesses, and related IAM roles
- Bedrock Knowledge Bases, data sources, and their S3 source bucket

You do **not** need to create these resources manually in the target account first.

## What still requires account access or manual setup

Before deployment, the target account must have:

- An AWS CLI profile or other credentials with permission to use CDK, CloudFormation,
  IAM, Lambda, S3, DynamoDB, Cognito, Bedrock, and AgentCore
- CDK bootstrap resources in the target account and region. The deployment script
  runs `cdk bootstrap` automatically, but the caller must be allowed to create them.
- AgentCore available in the selected region and account
- Bedrock model access for the models configured in `infra/stacks/platform_stack.py`
- Permission to pull the configured public ECR Harness image
- Sufficient service quotas

After deployment, you still need to:

- Upload knowledge-base source documents and start ingestion
- Create Cognito test users and add banker users to the `Bankers` group
- Load synthetic customer, account, transaction, banker, and credit records
- Deploy the recommendation metadata with the credit Lambda; its customer records
  must contain the recommendation features listed in `scripts/seed_data.py`
- Update the frontend with the new output URLs and Cognito IDs
- Run end-to-end smoke tests

## 1. Install local prerequisites

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
node --version
npx cdk --version
aws --version
```

The deployment script installs the Python requirements too, but installing them
first makes preflight checks clearer.

## 2. Configure the target AWS profile

Use a profile that belongs to the destination account. For example:

```bash
aws configure --profile bluey-target
```

Enter the destination account credentials when prompted. Then verify the account:

```bash
AWS_PROFILE=bluey-target AWS_PAGER='' \
aws sts get-caller-identity
```

Record the returned `Account` value. Never continue if it is the old account or an
unexpected account.

The profile name does not have to be `bluey-target`. You can use any existing profile
by overriding `AWS_PROFILE` in the deployment command.

## 3. Choose a stage and region

The repository includes `dev` and `hackathon` stages in `config/environments.yaml`.
For a new deployment, `hackathon` is usually the safer choice because non-`dev`
tables use `RemovalPolicy.RETAIN`.

The default region is `us-east-1`. AgentCore, Bedrock model access, and all resources
must be available in the same selected region.

You can use the existing stage configuration while overriding the profile:

```bash
export AWS_PROFILE=bluey-target
export AWS_DEFAULT_REGION=us-east-1
export CDK_DEFAULT_REGION=us-east-1
export BLUEY_STAGE=hackathon
```

Alternatively, add a new stage to `config/environments.yaml`:

```yaml
target:
  aws_profile: bluey-target
  region: us-east-1
  stage: target
```

Use a unique stage name if multiple Bluey environments will exist in the same account.

## 4. Verify Bedrock and AgentCore prerequisites

In the AWS console for the target account and selected region, verify:

1. The configured Claude models are available to the account.
2. `amazon.titan-embed-text-v2:0` is available for Knowledge Base embeddings.
3. AgentCore Gateway, Runtime, and Harness features are available.
4. The account has enough quotas for four runtimes and four Harnesses.
5. The public ECR image referenced by `HARNESS_IMAGE` is accessible.

The exact model IDs and image are in:

```text
infra/stacks/platform_stack.py
```

If the account or region does not support these services, CDK can synthesize the
templates but CloudFormation deployment will fail.

## 5. Run the deployment

Use the expected account ID guard to prevent deploying to the wrong account:

```bash
cd /Users/mukundi/projects/bluey-hackathon
source .venv/bin/activate

AWS_PROFILE=bluey-target \
BLUEY_EXPECTED_ACCOUNT_ID=123456789012 \
./scripts/deploy.sh hackathon
```

Replace `123456789012` with the account ID returned by `sts get-caller-identity`.

The script will:

1. Select the AWS profile and region.
2. Verify the current AWS account.
3. Install Python dependencies.
4. Bootstrap CDK in the selected account and region.
5. Synthesize the CloudFormation templates.
6. Show the CDK diff.
7. Deploy all Bluey stacks.
8. Run the read-only permission verification script.

The stacks are:

```text
BlueyData-hackathon
BlueyAuth-hackathon
BlueyKnowledge-hackathon
BlueyPlatform-hackathon
```

Review the printed account, region, stage, and profile before accepting any CDK
diff. Do not use `--context stage=dev` when deploying the hackathon environment.

## 6. Capture new deployment outputs

After deployment:

```bash
AWS_PROFILE=bluey-target \
python scripts/stack_outputs.py \
  --stage hackathon \
  --region us-east-1
```

Save these new values for the frontend and test commands:

- `UserPoolId`
- `AppClientId`
- `HttpApiUrl`
- `BankerApiUrl`
- `DocumentApiUrl`
- `ChatFunctionUrl`
- `AccountOpeningFunctionUrl`
- `CreditFunctionUrl`
- `KnowledgeBucketName`
- `MainKnowledgeBaseId`
- `CreditKnowledgeBaseId`
- `AccountOpeningKnowledgeBaseId`
- `MainDataSourceId`
- `CreditDataSourceId`
- `AccountOpeningDataSourceId`
- `MainGatewayArnOutput`
- `CreditGatewayArnOutput`
- `AccountOpeningGatewayArnOutput`
- `MainHarnessArnOutput`
- `CreditHarnessArnOutput`
- `AccountOpeningHarnessArnOutput`

These values are generated for the new account. Do not reuse values from the old
account.

## 7. Populate the Knowledge Bases

The repository contains a `knowledge_base/` directory. CDK uploads its contents to
the generated S3 bucket during deployment using non-destructive bucket deployments.
The repository layout is:

```text
main/
  policy/
  faqs/
  product-catalogue/
credit/
account-opening/
```

Add approved documents to these folders before running `deploy.sh`. Files already in
the target bucket are preserved because deployment uses `prune=False`.

Put bank policy documents, FAQs, and the product catalogue under the three `main/`
subfolders. Put credit policy, affordability, responsible-lending, and credit-product
documents under `credit/`. Put account-opening requirements, accepted-document
guidance, and application-process documents under `account-opening/`.

The current architecture creates three Knowledge Bases, three data sources, and
three AgentCore Gateways:

| Prefix | Knowledge Base | Intended Harness |
| --- | --- | --- |
| `main/` | Main Knowledge Base | Main banking Harness |
| `credit/` | Credit Knowledge Base | Credit Harness |
| `account-opening/` | Account-opening Knowledge Base | Account-opening Harness |

The main data source includes all three main subfolders through the `main/` prefix.
Do not use the old `files/` or `Credit/` prefixes for new deployments.

If migrating from an old account instead of using repository files:

```bash
AWS_REGION=us-east-1 \
./scripts/migrate_kb_files.sh \
  old-profile \
  bluey-target \
  OLD_KNOWLEDGE_BUCKET \
  NEW_KNOWLEDGE_BUCKET
```

Avoid copying confidential or personal data unless it is approved for the target
  account. The target bucket name is printed by `stack_outputs.py`. The migration
  script preserves the source bucket's prefixes, so the source bucket should already
  use the layout above.

Uploading files and ingesting them are separate operations. After deployment, start
ingestion using the new Knowledge Base and data-source IDs:

```bash
AWS_PROFILE=bluey-target \
python scripts/ingest_knowledge_bases.py \
  --region us-east-1 \
  --main-kb MAIN_KNOWLEDGE_BASE_ID \
  --main-source MAIN_DATA_SOURCE_ID \
  --credit-kb CREDIT_KNOWLEDGE_BASE_ID \
  --credit-source CREDIT_DATA_SOURCE_ID \
  --account-opening-kb ACCOUNT_OPENING_KNOWLEDGE_BASE_ID \
  --account-opening-source ACCOUNT_OPENING_DATA_SOURCE_ID
```

Check all three ingestion jobs in the Bedrock console before testing Knowledge Base
questions. An S3 folder by itself does not make documents searchable; ingestion must
complete successfully.

### Gateway isolation

Each Harness now has its own Gateway, execution role, Knowledge Base target, and
Lambda target:

| Harness | Gateway targets | Lambda resource permission |
| --- | --- | --- |
| Main banking | Main banking session tools and main Knowledge Base | `bluey-sessions` from the main Gateway ARN |
| Credit | Credit tools and credit Knowledge Base | `bluey-credit-api` from the credit Gateway ARN |
| Account opening | Document-status/applicant tools and account-opening Knowledge Base | `bluey-sessions` from the account-opening Gateway ARN |

The account-opening and main targets use the same `bluey-sessions` Lambda code, but
their Gateway tool schemas expose different operations. Gateway roles can invoke
only their assigned Lambda functions and retrieve only their assigned Knowledge Base.
Do not manually recreate these roles or Lambda policies; CDK creates them from the
generated ARNs during deployment.

### Recommendation engine

The credit Gateway exposes `recommend_products`. The deployed credit Lambda uses the
exported recommendation segment profiles and product catalogue in
`lambda/credit_api/recommendation_metadata.json`, reads the customer's feature fields
from `bluey-customers`, and returns guidance-only recommendations. The seed script
populates those fields for all demo customers.

The original scikit-learn `.joblib` classifier remains a training artifact under
`recommendation_engine/`. It is not loaded inside the standard Lambda ZIP because
scikit-learn and numpy are not part of the Lambda runtime dependencies. Deploying the
exact classifier would require a Lambda layer or container image with pinned ML
dependencies; the current metadata inference path is the lightweight deployable
integration.

## 8. Create test Cognito users

The User Pool is created automatically, but users are not. Create at least:

- One customer test user
- One banker test user

Add the banker user to the `Bankers` group. Use the new User Pool ID from the stack
outputs. Do not copy users, passwords, tokens, or personal data from the old account.

The frontend must authenticate against the new User Pool and App Client. The chat,
credit, and account-opening Function URLs validate Cognito access tokens inside
Lambda. The banker frontend uses the new `BankerApiUrl` with a Cognito **ID token**;
the banker and document APIs use Cognito JWT authorization through API Gateway.

Open `index.html` locally, enter the deployed `BankerApiUrl`, and paste an ID token
from a Cognito user who belongs to the `Bankers` group. The page stores these values
in browser local storage for the next session and sends the token on every request.

## 9. Seed synthetic application data

The tables are created empty. The repository includes `scripts/seed_data.py`, which
loads deterministic synthetic records into the operational tables. It creates:

- 6 customers, each with an `idNumber` usable by customer verification
- 7 accounts, with at least one account for every customer
- 21 transactions, with at least three transactions per account
- 6 chat/account-opening sessions matching the current Lambda persistence fields
- 3 bankers, including general bankers used by customer assignment
- 6 credit records and 1 pending application

Preview the records without writing anything:

```bash
AWS_PROFILE=bluey-target \
python scripts/seed_data.py \
  --region us-east-1 \
  --dry-run
```

Write them to the target account:

```bash
AWS_PROFILE=bluey-target \
python scripts/seed_data.py \
  --region us-east-1
```

The script writes only its deterministic keys. It does not scan, delete, or clear
the tables, so rerunning it is safe for the demo records. To seed only one dataset,
repeat `--table`:

```bash
AWS_PROFILE=bluey-target \
python scripts/seed_data.py \
  --region us-east-1 \
  --table customers \
  --table accounts \
  --table transactions
```

The tables populated by the script are:

```text
bluey-customers
bluey-accounts
bluey-transactions
bluey-bankers
bluey-credit
```

Do not copy production or test-account PII into the new environment.

## 10. Verify IAM and resource policies

Run:

```bash
AWS_PROFILE=bluey-target \
python scripts/verify_permissions.py --region us-east-1
```

The important permission chains are:

```text
Chat Lambda -> Main Harness -> Main Gateway -> bluey-sessions + Main Knowledge Base
Credit Harness -> Credit Gateway -> bluey-credit-api + Credit Knowledge Base
Account-opening Lambda -> Account-opening Harness -> Account-opening Gateway -> bluey-sessions + Account-opening Knowledge Base
```

Both IAM identity policies and Lambda resource policies are required for Gateway to
invoke Lambda targets. The CDK stack creates both sides.

## 11. Run smoke tests

Set the new outputs and a Cognito access token:

```bash
export CHAT_URL='NEW_CHAT_FUNCTION_URL'
export ACCOUNT_OPENING_URL='NEW_ACCOUNT_OPENING_FUNCTION_URL'
export CREDIT_URL='NEW_CREDIT_FUNCTION_URL'
export COGNITO_ACCESS_TOKEN='ACCESS_TOKEN_FROM_NEW_COGNITO_USER'
```

Run the basic chat test:

```bash
AWS_PROFILE=bluey-target python scripts/smoke_test.py
```

You can also invoke a Harness directly using its CloudFormation output:

```bash
AWS_PROFILE=bluey-target \
python scripts/smoke_agentcore.py \
  --stage hackathon \
  --region us-east-1 \
  --harness main
```

Then test, in order:

1. Customer chat and session continuation
2. Credit guidance and credit Knowledge Base retrieval
3. Customer verification and account lookup
4. Transaction lookup and chart data
5. Account-opening conversation
6. Document upload and status
7. Banker review and approval/rejection

Check CloudWatch logs for `AccessDeniedException`, `ResourceNotFoundException`,
missing Harness endpoints, and DynamoDB validation errors.

## 12. What to do when deployment fails

If CDK fails before CloudFormation deployment:

- Confirm the virtual environment is active.
- Confirm `AWS_PROFILE` and `AWS_DEFAULT_REGION`.
- Run `aws sts get-caller-identity` again.
- Confirm CDK bootstrap completed.

If CloudFormation fails while creating AgentCore resources:

- Confirm AgentCore is enabled in the target account and region.
- Confirm Bedrock model access and quotas.
- Confirm the configured public ECR image is accessible.
- Check the failed resource event in CloudFormation.

If the chat endpoint returns `AccessDeniedException`:

- Run `scripts/verify_permissions.py`.
- Compare the generated Harness ARN outputs with the proxy Lambda role policy.
- Confirm the Lambda and Harness are in the same region.

If chat works but Knowledge Base answers are empty:

- Confirm files exist in the new Knowledge Base bucket.
- Confirm both ingestion jobs completed successfully.
- Confirm the Gateway Knowledge Base targets are available.

## Short answer

You do not need to manually create the CDK-managed resources or manually replace
their ARNs. Deploying with the correct AWS profile causes CDK and CloudFormation to
create them in the selected account with generated account-local references and IAM
permissions. You must still prepare account-level service access, then populate and
test the newly-created environment after deployment.