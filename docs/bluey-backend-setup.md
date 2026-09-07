# Bluey Backend Setup — AgentCore Integration Log

Reproducible steps for connecting a Bedrock AgentCore agent to DynamoDB (via Gateway + Lambda) and exposing it to a frontend (via a chat-proxy Lambda).

Region used throughout: `af-south-1`. Replace `ACCOUNT_ID`, table names, and ARNs with your actual values.

---

## Part 1: Generic DynamoDB Tool (Lambda + Gateway Target)

Purpose: let the agent read/write any DynamoDB table via two generic tools, `get_item` and `put_item`.

### 1.1 IAM Role for the Lambda

1. IAM console → Roles → Create role → **AWS service** → **Lambda** → Next → Next
2. Name: `bluey-dynamodb-lambda-role` → Create role
3. Open the role → Add permissions → Create inline policy → JSON tab:
   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "dynamodb:GetItem",
                   "dynamodb:PutItem",
                   "dynamodb:Query",
                   "dynamodb:UpdateItem"
               ],
               "Resource": [
                   "arn:aws:dynamodb:af-south-1:ACCOUNT_ID:table/bluey-sessions",
                   "arn:aws:dynamodb:af-south-1:ACCOUNT_ID:table/TABLE_2",
                   "arn:aws:dynamodb:af-south-1:ACCOUNT_ID:table/TABLE_3"
               ]
           }
       ]
   }
   ```
   (Get exact table ARNs from DynamoDB console → Tables → select table → Overview tab.)
4. Name policy `dynamodb-session-access` → Create policy
5. Add permissions → Attach policies → attach `AWSLambdaBasicExecutionRole`

**Note:** every new table the agent needs access to must be added to this policy's `Resource` array — no other changes needed since the tool is generic.

### 1.2 Create the Lambda

1. Lambda console → Create function → Author from scratch
2. Name: `bluey-dynamodb-tool`, Runtime: Python 3.12
3. Execution role → Use existing role → `bluey-dynamodb-lambda-role`
4. Confirm region is `af-south-1` → Create function

### 1.3 Lambda Code

```python
import boto3

dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    tool_name = ""
    if context.client_context and context.client_context.custom:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "__" in tool_name:
        tool_name = tool_name.split("__", 1)[1].lstrip("_")

    table = dynamodb.Table(event["table_name"])

    if tool_name == "get_item":
        resp = table.get_item(Key=event["key"])
        return {"result": resp.get("Item", {})}

    if tool_name == "put_item":
        table.put_item(Item=event["item"])
        return {"result": "saved"}

    return {"error": f"unknown tool {tool_name}"}
```

Paste into Code tab → **Deploy**.

**Important AgentCore Gateway Lambda contract** (learned via troubleshooting, not obvious from AWS docs):
- The Gateway passes tool input parameters **directly as `event`** — no `"input"` or `"name"` wrapper.
- The tool name is **not** in `event` — it's in `context.client_context.custom["bedrockAgentCoreToolName"]`, prefixed with the Gateway target's name (e.g. `dynamodb-tool__put_item`). Strip everything before and including the `__` (plus any stray leading `_`) to get the plain tool name.
- The console **Test** button does not simulate `client_context` (it's `None`) — guard against this (`if context.client_context and context.client_context.custom:`) or console tests will throw `AttributeError`. Real verification must happen through the agent, not the console Test button.

### 1.4 Resource-Based Policy (let Gateway invoke this Lambda)

1. Lambda console → `bluey-dynamodb-tool` → Configuration → Permissions
2. Resource-based policy statements → Add permissions → AWS service
3. Service: Other (or search "bedrock-agentcore")
4. Statement ID: `allow-gateway-invoke`
5. Principal: `bedrock-agentcore.amazonaws.com`
6. Action: `lambda:InvokeFunction`
7. **Source ARN**: your Gateway's ARN — find via AgentCore console → Gateways → [your gateway] → Overview → Gateway ARN
   (format: `arn:aws:bedrock-agentcore:af-south-1:ACCOUNT_ID:gateway/your-gateway-id`)
8. Save

### 1.5 Gateway Target

1. AgentCore console → Gateways → [your existing gateway, same one used for the Knowledge Base] → Targets tab → Add target
2. Target type: Lambda function
3. Name: `dynamodb-tool`
4. Select function: `bluey-dynamodb-tool`
5. Tool schema → Define inline:
   ```json
   [
     {
       "name": "get_item",
       "description": "Retrieve an item from a DynamoDB table by its key",
       "inputSchema": {
         "type": "object",
         "properties": {
           "table_name": {"type": "string", "description": "Name of the DynamoDB table"},
           "key": {"type": "object", "description": "Key object matching the table's partition (and sort) key names"}
         },
         "required": ["table_name", "key"]
       }
     },
     {
       "name": "put_item",
       "description": "Save or update an item in a DynamoDB table",
       "inputSchema": {
         "type": "object",
         "properties": {
           "table_name": {"type": "string", "description": "Name of the DynamoDB table"},
           "item": {"type": "object", "description": "Full item to write, including its key fields"}
         },
         "required": ["table_name", "item"]
       }
     }
   ]
   ```
6. Credential provider: Use Gateway IAM role
7. Create target — wait for status **Available**

### 1.6 Testing

Through the agent (not the Lambda console): prompt it to save a session, then retrieve it. Confirm in DynamoDB console → your table → **Explore table items**.

Note: table partition/sort keys in this project use **camelCase** (e.g. `sessionId`, not `session_id`) — match this exactly in prompts/schemas, since the Lambda passes `key`/`item` straight through to DynamoDB with no translation.

---

## Part 2: Chat Proxy Lambda (Frontend-Facing Endpoint)

Purpose: give any frontend (in this project's case, a separately-built Figma-based UI) a single HTTP endpoint to POST a message to and get the agent's full response back. Non-streaming (full response returned at once) — chosen for build speed over token-by-token streaming.

**STATUS: WORKING** (confirmed end-to-end via `python3 test_chat.py`).

### 2.1 Find the harness's actual invocation details first

Before writing any code, get the exact API call AWS expects for your specific harness:

1. AgentCore console → your harness → **View invocation code** (collapsed accordion, above the Endpoints section)
2. Expand it and copy the generated snippet. This tells you the true operation name, parameter names, and response shape — these can differ from generic docs/examples. In this project it turned out to be `invoke_harness` (not `invoke_agent_runtime`), using a `messages`/`content` payload shape and a streamed `contentBlockDelta` response — not the payload/response shape used elsewhere in AWS's Bedrock docs.
3. Also note down, from the harness's **Overview** page: the exact **Harness ARN**, its **region**, and confirm an **Endpoint** (e.g. `DEFAULT`) exists and shows status **Ready**.

### 2.2 IAM Role

1. IAM console → Roles → Create role → AWS service → Lambda → Next → Next
2. Name: `bluey-chat-proxy-role` → Create role
3. Add permissions → Create inline policy → JSON:
   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "bedrock-agentcore:InvokeAgentRuntime",
                   "bedrock-agentcore:InvokeHarness"
               ],
               "Resource": "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:harness/YOUR_HARNESS_ID*"
           }
       ]
   }
   ```
   **Important quirks confirmed by troubleshooting:**
   - Even though the API you call is `invoke_harness`, AWS's IAM permission check is on `bedrock-agentcore:InvokeAgentRuntime` (a naming mismatch between the API operation and the underlying IAM action) — include **both** actions to be safe.
   - The trailing `*` on the Resource ARN is required — the exact harness ARN alone is rejected because the actual permission check happens against a sub-resource path (e.g. `.../runtime-endpoint/DEFAULT`), which the exact ARN doesn't match.
4. Name policy `invoke-agentcore-access` → Create policy
5. Attach `AWSLambdaBasicExecutionRole`

### 2.3 Create the Lambda

1. Lambda console → Create function → Author from scratch
2. Name: `bluey-chat-proxy`, Runtime: Python 3.12
3. Execution role → Use existing role → `bluey-chat-proxy-role`
4. **Region: match your harness's region** (in this project, the harness is in `us-east-1`, not `af-south-1` where the DynamoDB tool Lambda lives — check this explicitly, don't assume it matches your default working region)
5. Create function

### 2.4 Code

```python
import boto3, json, uuid

agentcore_client = boto3.client("bedrock-agentcore", region_name="us-east-1")
HARNESS_ARN = "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:harness/YOUR_HARNESS_ID"

def lambda_handler(event, context):
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or str(uuid.uuid4())

    response = agentcore_client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[
            {"role": "user", "content": [{"text": prompt}]}
        ],
    )

    full_text = ""
    for chunk in response["stream"]:
        if "contentBlockDelta" in chunk:
            delta = chunk["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                full_text += delta["text"]

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps({"reply": full_text, "session_id": session_id}),
    }
```

Note: `invoke_harness` always returns a stream of `contentBlockDelta` chunks (that's just the shape of the API) — since we chose non-streaming for the frontend contract, we accumulate all chunks into one string (`full_text`) before returning, rather than forwarding the stream.

### 2.5 Increase the Lambda timeout

Default Lambda timeout (3 seconds) is too short — waiting on the harness to think and stream a full reply takes longer.

1. Lambda console → `bluey-chat-proxy` → Configuration tab → General configuration → Edit
2. Timeout: set to **30 sec** (increase further, e.g. 60s, if the agent's tool calls — KB lookups, DynamoDB reads — add noticeable latency)
3. Save

### 2.6 Function URL

1. Configuration tab → Function URL → Create function URL
2. Auth type: NONE *(hackathon/testing only — switch to IAM auth or a Cognito authorizer before any real deployment)*
3. Configure CORS → Allow origin `*`, Allow methods `POST` → Save
4. Copy the generated Function URL — this is the contract endpoint for the frontend team

### 2.7 Testing

Prefer a Python script over `curl` in the VS Code terminal — copy-pasted `curl` commands are prone to smart-quote/invisible-character corruption from paste, which produces confusing "malformed URL" errors that have nothing to do with the actual endpoint.

`test_chat.py`:
```python
import requests

url = "https://YOUR-FUNCTION-URL.lambda-url.us-east-1.on.aws/"
payload = {"prompt": "What is my account balance?"}

response = requests.post(url, json=payload)
print(response.status_code)
print(response.text)
```
```bash
pip3 install requests
python3 test_chat.py
```

### 2.8 Troubleshooting log (for reference — issues hit in order, and root cause)

| Symptom | Root cause |
|---|---|
| `{"Message":null}` | Unhandled exception in Lambda (turned out to be the wrong invocation API entirely — see below) |
| `zsh: command not found: -H` / `-d` | curl line-continuation backslashes stripped on paste — put command on one line |
| `curl: (3) URL rejected: Malformed input` | Smart quotes / invisible characters from copy-paste corrupting the URL — switched to Python `requests` instead |
| `502` from Function URL | Generic Lambda-crashed-unhandled response; check CloudWatch, not the 502 itself |
| `AccessDeniedException: ...InvokeAgentRuntime... harness/...` | IAM policy had placeholder ARN never replaced, and wrong region client (`af-south-1` default instead of harness's actual `us-east-1`) |
| `AccessDeniedException: ...harness/.../runtime-endpoint/DEFAULT` | Resource ARN needs trailing `*` — exact harness ARN doesn't cover the sub-resource path actually checked |
| `ResourceNotFoundException: No endpoint or agent found with qualifier 'DEFAULT'` | Was calling the wrong API (`invoke_agent_runtime`) for a harness resource — harnesses use `invoke_harness`, found via the console's "View invocation code" panel |
| `AccessDeniedException` again after switching to `invoke_harness` | The API operation name and the IAM action it checks aren't the same — `invoke_harness` still checks `bedrock-agentcore:InvokeAgentRuntime` under the hood. Fix: grant both action names in the policy. |
| `Status: timeout` (no error) | Default 3-second Lambda timeout too short — increased to 30s |

**Key lesson:** when integrating with a specific AgentCore harness, always pull the exact API call from that harness's own "View invocation code" panel rather than assuming a general `invoke_agent_runtime` pattern applies — the operation name, payload shape, and response shape are resource-specific.

---

## Part 3: Automatic Session Persistence

Purpose: guarantee every chat turn is saved to `bluey-sessions`, without depending on the agent choosing to call a save tool. The **chat-proxy Lambda itself** now owns reading/writing session state on every request — not the agent.

**STATUS: WORKING** (confirmed via DynamoDB console — new items show `messages`, `userId`, `createdAt`, `updatedAt` populated correctly).

### 3.1 Add DynamoDB permission to the proxy's role

`bluey-chat-proxy-role` inline policy — add a second statement alongside the existing harness-invoke one. **Region must match where `bluey-sessions` actually lives (`us-east-1` in this project)** — a region mismatch here was the actual root cause of a `403`/`AccessDeniedException` we hit (see note below).

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:InvokeHarness"
            ],
            "Resource": "arn:aws:bedrock-agentcore:us-east-1:957014951500:harness/harness_nxkh1-5J9xGVZCk5*"
        },
        {
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
            "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions"
        }
    ]
}
```

### 3.2 Updated Lambda code (`bluey-chat-proxy`)

```python
import boto3, json, uuid
from datetime import datetime, timezone

agentcore_client = boto3.client("bedrock-agentcore", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
sessions_table = dynamodb.Table("bluey-sessions")

HARNESS_ARN = "arn:aws:bedrock-agentcore:us-east-1:957014951500:harness/harness_nxkh1-5J9xGVZCk5"

def lambda_handler(event, context):
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or str(uuid.uuid4())
    user_id = body.get("user_id", "anonymous")

    now = datetime.now(timezone.utc).isoformat()

    existing = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")
    messages = existing.get("messages", []) if existing else []
    messages.append({"role": "user", "text": prompt, "timestamp": now})

    response = agentcore_client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    full_text = ""
    for chunk in response["stream"]:
        if "contentBlockDelta" in chunk:
            delta = chunk["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                full_text += delta["text"]

    messages.append({"role": "assistant", "text": full_text, "timestamp": datetime.now(timezone.utc).isoformat()})

    sessions_table.put_item(Item={
        "sessionId": session_id,
        "userId": user_id,
        "messages": messages,
        "createdAt": existing.get("createdAt", now) if existing else now,
        "updatedAt": now,
    })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"reply": full_text, "session_id": session_id}),
    }
```

Frontend contract note: `user_id` is now an accepted (optional, defaults to `"anonymous"`) field in the request body, alongside `prompt` and `session_id`.

### 3.3 Testing

```python
import requests

url = "https://YOUR-FUNCTION-URL.lambda-url.us-east-1.on.aws/"

r1 = requests.post(url, json={"prompt": "Hi, my name is Test User", "user_id": "user_001"})
print(r1.status_code, r1.text)
session_id = r1.json()["session_id"]

r2 = requests.post(url, json={"prompt": "What did I just tell you?", "session_id": session_id, "user_id": "user_001"})
print(r2.status_code, r2.text)
```
Confirm the second reply shows recall, then check DynamoDB console → `bluey-sessions` → Explore table items for a row with `messages`, `userId`, `createdAt`, `updatedAt` populated.

### 3.4 Troubleshooting notes

| Symptom | Root cause |
|---|---|
| No error, but no new item appears in table | New code pasted but **Deploy** button not clicked — always confirm the "Changes deployed" banner |
| `403` / `{"Message":null}`, log shows `AccessDeniedException` on `GetItem` | IAM policy's DynamoDB statement had the wrong region in its ARN (`af-south-1` instead of where the table actually is, `us-east-1`) — **the region must match across the IAM ARN, the `boto3.resource()` call, and the table's actual region**, all three, not just one |

**Key lesson (recurring theme all session):** in a project spanning `us-east-1` (harness) and originally `af-south-1` (Dynamo tool Lambda), region mismatches were the single most common root cause of errors — check the region in the IAM ARN, the boto3 client/resource call, and the actual resource itself as a matched trio whenever something fails with Access Denied or Not Found.

---

## Part 3: Automatic Session Saving

Problem with the original design: relying on the agent to *decide* to call `put_item` to save a session is unreliable for a banking use case. Fix: `bluey-chat-proxy` now owns session persistence directly — every request automatically loads prior history (if any), appends the new turn, calls the harness, appends the reply, and writes the full session back. No dependency on the agent choosing to save.

**STATUS: WORKING** (confirmed via CloudWatch + DynamoDB console — see below).

### 3.1 Add DynamoDB permission to `bluey-chat-proxy-role`

Add a second statement to the existing inline policy (keep the harness one):
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:InvokeHarness"
            ],
            "Resource": "arn:aws:bedrock-agentcore:us-east-1:957014951500:harness/harness_nxkh1-5J9xGVZCk5*"
        },
        {
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
            "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions"
        }
    ]
}
```

**Region gotcha (hit twice):** every ARN/region in this policy and in the Lambda's boto3 clients must point at wherever your resources actually live. In this project everything — harness, Lambda, and `bluey-sessions` table — ended up consolidated in `us-east-1`. If your table is in a different region than your harness, use the correct region per client and don't assume they match.

### 3.2 Updated Lambda code

```python
import boto3, json, uuid
from datetime import datetime, timezone

agentcore_client = boto3.client("bedrock-agentcore", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
sessions_table = dynamodb.Table("bluey-sessions")

HARNESS_ARN = "arn:aws:bedrock-agentcore:us-east-1:957014951500:harness/harness_nxkh1-5J9xGVZCk5"

def lambda_handler(event, context):
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "")
    session_id = body.get("session_id") or str(uuid.uuid4())
    user_id = body.get("user_id", "anonymous")

    now = datetime.now(timezone.utc).isoformat()

    existing = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")
    messages = existing.get("messages", []) if existing else []

    messages.append({"role": "user", "text": prompt, "timestamp": now})

    response = agentcore_client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    full_text = ""
    for chunk in response["stream"]:
        if "contentBlockDelta" in chunk:
            delta = chunk["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                full_text += delta["text"]

    messages.append({"role": "assistant", "text": full_text, "timestamp": datetime.now(timezone.utc).isoformat()})

    sessions_table.put_item(Item={
        "sessionId": session_id,
        "userId": user_id,
        "messages": messages,
        "createdAt": existing.get("createdAt", now) if existing else now,
        "updatedAt": now,
    })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"reply": full_text, "session_id": session_id}),
    }
```

### 3.3 Frontend contract — important detail for the Figma team

The proxy generates a new random `session_id` (UUID) whenever one isn't supplied. **The frontend must capture the `session_id` returned in the first response and send it back on every subsequent message in the same conversation** — otherwise each message starts a brand-new, disconnected session with no memory of prior turns.

Contract:
- Request: `POST {"prompt": string, "session_id"?: string, "user_id"?: string}`
- Response: `{"reply": string, "session_id": string}`
- First message: omit `session_id`. Every message after: pass the `session_id` from the previous response.

### 3.4 Testing

```python
import requests

url = "https://YOUR-FUNCTION-URL.lambda-url.us-east-1.on.aws/"

r1 = requests.post(url, json={"prompt": "Hi, my name is Test User", "user_id": "user_001"})
print(r1.status_code, r1.text)
session_id = r1.json()["session_id"]

r2 = requests.post(url, json={"prompt": "What did I just tell you?", "session_id": session_id, "user_id": "user_001"})
print(r2.status_code, r2.text)
```
Confirm in DynamoDB console → `bluey-sessions` → Explore table items: a new item keyed by the generated `sessionId`, with `userId`, a growing `messages` list, `createdAt`, `updatedAt`.

### 3.5 Troubleshooting notes

| Symptom | Root cause |
|---|---|
| Session saved fine, but table showed no new items | DynamoDB client in the Lambda code was pointed at the wrong region (leftover `af-south-1` from an earlier code version, while the table is actually in `us-east-1`) |
| `403 {"Message":null}` + CloudWatch `AccessDeniedException on GetItem` | IAM policy's DynamoDB statement had the table ARN in the wrong region — region in the ARN must match where the table actually lives |
| **Fields written by a tool mid-conversation (e.g. `customerId`, `verified`, `reviewStatus` from `lookup_customer`) silently disappeared after the turn completed** | `bluey-chat-proxy`'s final session write used `put_item`, which **replaces the entire item** rather than merging fields. Any attribute a tool wrote to the session record during the agent's turn was wiped out when the proxy did its own end-of-turn write with only `{sessionId, userId, messages, createdAt, updatedAt}`. **Fix: switched the proxy's final write from `put_item` to `update_item`** (see 3.6). General lesson: once more than one Lambda writes to the same DynamoDB item, `put_item` anywhere in that flow is a hazard — prefer `update_item` with explicit `SET` expressions so each writer only touches its own fields.

### 3.6 Current (fixed) final-write code

```python
    sessions_table.update_item(
        Key={"sessionId": session_id},
        UpdateExpression="SET messages = :m, updatedAt = :u, userId = if_not_exists(userId, :uid), createdAt = if_not_exists(createdAt, :c)",
        ExpressionAttributeValues={
            ":m": messages,
            ":u": now,
            ":uid": user_id,
            ":c": (existing.get("createdAt") if existing else now) or now,
        },
    )
```
Replaces the earlier `put_item` call shown in 3.2 — the rest of that code (loading `existing`, calling the harness, appending to `messages`) is unchanged. **STATUS: fix applied, not yet re-confirmed with a fresh end-to-end test** — verify next session by checking that `customerId`/`verified`/`reviewStatus` survive a full turn after `lookup_customer` runs.

---

## Part 4: Customer Identity Verification

Design decision: **no real auth (Cognito) for the hackathon** — two-tier identification instead:
1. **Anonymous/new users** (e.g. opening an account): frontend generates a random `user_id` client-side (e.g. `crypto.randomUUID()`), persists it locally, sends it on every request. No backend changes needed — `bluey-chat-proxy` already accepts `user_id` as-is.
2. **Existing customers**: verified via SA ID number, matched against `bluey-customers`, then linked to their session.

**STATUS: WORKING** end-to-end — verification, customer lookup, and writing the verified `customerId` back into the session record.

### 4.1 Add `idNumber` to the customer table

`bluey-customers` didn't originally have an ID number field (only `customerId`, `email`, `fullName`, `isDemo`, `personalBanker`, `phone`). Added manually via DynamoDB console → `bluey-customers` → each item → Edit → add attribute `idNumber` (String) for all 4 seeded customers.

### 4.2 IAM — allow Scan on customers table

`idNumber` is not the partition key (`customerId` is), so lookup requires `Scan`, not `GetItem`. Updated `bluey-dynamodb-lambda-role`'s inline policy:
```json
{
    "Effect": "Allow",
    "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:Scan"],
    "Resource": [
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions",
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-customers"
    ]
}
```

### 4.3 `lookup_customer` tool — verifies AND links to session

Added to `bluey-dynamodb-tool` Lambda (alongside existing `get_item`/`put_item` branches):
```python
    if tool_name == "lookup_customer":
        id_number = event["idNumber"]
        session_id = event.get("sessionId")

        customers_table = dynamodb.Table("bluey-customers")
        resp = customers_table.scan(
            FilterExpression="idNumber = :id",
            ExpressionAttributeValues={":id": id_number},
        )
        items = resp.get("Items", [])
        customer = items[0] if items else None

        if customer and session_id:
            sessions_table = dynamodb.Table("bluey-sessions")
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET customerId = :cid, verified = :v",
                ExpressionAttributeValues={":cid": customer["customerId"], ":v": True},
            )

        return {"result": customer}
```
On successful match, this writes `customerId` and `verified: true` directly onto the `bluey-sessions` record — so the rest of the conversation (and later, the banker interface) can see this session belongs to a confirmed real customer.

### 4.4 Gateway tool schema addition

AgentCore console → Gateways → your gateway → Targets → `dynamodb-tool` → Edit → add to the tool schema array:
```json
{
    "name": "lookup_customer",
    "description": "Verify a customer's identity using their SA ID number, and link the verification to the current session. Always pass the current sessionId (found in the hidden [session_id: ...] tag in the conversation context) along with the idNumber.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "idNumber": {"type": "string", "description": "The customer's SA ID number"},
            "sessionId": {"type": "string", "description": "The current conversation's session ID"}
        },
        "required": ["idNumber", "sessionId"]
    }
}
```

### 4.5 Giving the agent its own session ID

The agent doesn't know its own `runtimeSessionId` by default — it has to be passed in-band. `bluey-chat-proxy` now appends a hidden tag to the prompt sent to the harness (the clean prompt is still what gets stored in `messages` history):
```python
    messages.append({"role": "user", "text": prompt, "timestamp": now})

    augmented_prompt = f"{prompt}\n\n[session_id: {session_id}]"

    response = agentcore_client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": augmented_prompt}]}],
    )
```

### 4.6 System prompt addition

Added to the harness's system prompt/instructions:
> "User messages may end with a hidden tag like `[session_id: xyz]` — this is internal context, not part of what the user said. Never mention it to the user. When calling `lookup_customer`, always extract this value and pass it as the `sessionId` parameter."

### 4.7 Testing

Prompt: *"My ID number is 9001015800086, please verify me"* → agent returns matched customer details. Confirmed in DynamoDB console → `bluey-sessions` → Explore items: the relevant session now shows `customerId` and `verified: true` added to the record.

---

## Part 5: Banker Review Interface

Purpose: let a banker see sessions flagged for review (customers who've been identity-verified via `lookup_customer`), read the conversation, and approve/reject — e.g. for account opening.

**STATUS: WORKING** — list, detail, approve/reject, and DynamoDB write-back all confirmed via the deployed Amplify page.

### 5.1 Flag sessions for review at verification time

Extended `lookup_customer` (in `bluey-dynamodb-tool`) to also set `reviewStatus: pending_review` on successful match:
```python
        if customer and session_id:
            sessions_table = dynamodb.Table("bluey-sessions")
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET customerId = :cid, verified = :v, reviewStatus = :rs",
                ExpressionAttributeValues={":cid": customer["customerId"], ":v": True, ":rs": "pending_review"},
            )
```

### 5.2 IAM role for the banker API Lambda

New role `bluey-banker-api-role`:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:UpdateItem"],
            "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions"
        },
        {
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem"],
            "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-customers"
        }
    ]
}
```
Plus `AWSLambdaBasicExecutionRole` attached.

### 5.3 `bluey-banker-api` Lambda

Python 3.12, region `us-east-1`, execution role `bluey-banker-api-role`. Supports 4 actions via query param (`GET`) or JSON body (`POST`): `list`, `detail`, `approve`, `reject`.

```python
import boto3, json

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
sessions_table = dynamodb.Table("bluey-sessions")
customers_table = dynamodb.Table("bluey-customers")

HEADERS = {"Content-Type": "application/json"}  # do NOT add Access-Control-Allow-Origin here — see 5.5

def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    params = event.get("queryStringParameters") or {}
    body = json.loads(event.get("body") or "{}")

    action = params.get("action") or body.get("action")

    if action == "list":
        resp = sessions_table.scan(
            FilterExpression="reviewStatus = :rs",
            ExpressionAttributeValues={":rs": "pending_review"},
        )
        items = resp.get("Items", [])
        results = []
        for item in items:
            customer = customers_table.get_item(Key={"customerId": item.get("customerId", "")}).get("Item", {})
            results.append({
                "sessionId": item["sessionId"],
                "customerId": item.get("customerId"),
                "fullName": customer.get("fullName", "Unknown"),
                "updatedAt": item.get("updatedAt"),
            })
        return _response(200, {"sessions": results})

    if action == "detail":
        session_id = params.get("sessionId") or body.get("sessionId")
        item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")
        if not item:
            return _response(404, {"error": "not found"})
        customer = customers_table.get_item(Key={"customerId": item.get("customerId", "")}).get("Item", {})
        return _response(200, {"session": item, "customer": customer})

    if action in ("approve", "reject"):
        session_id = body.get("sessionId")
        sessions_table.update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET reviewStatus = :rs",
            ExpressionAttributeValues={":rs": "approved" if action == "approve" else "rejected"},
        )
        return _response(200, {"result": action})

    return _response(400, {"error": "unknown action"})

def _response(status, body):
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, default=str)}
```

### 5.4 Function URL

Auth: NONE (hackathon only). CORS configured directly on the Function URL (Lambda console → Configuration → Function URL → Edit):
- Allow origin: `*`
- Allow methods: `GET`, `POST`
- **Allow headers: `content-type`** — required, see 5.5

### 5.5 CORS gotcha — do not set CORS headers in both places

Hit two separate CORS failures:
1. **Doubled header**: originally set `Access-Control-Allow-Origin: *` in the Lambda's own response headers *and* in the Function URL's CORS config. Browsers reject a response with the header appearing twice, even with identical values (`'*, *' is not a valid value`). Fix: only set it in the Function URL config, never in Lambda response headers.
2. **Preflight rejection on POST**: `Approve`/`Reject` buttons send `Content-Type: application/json`, which triggers a CORS preflight (`OPTIONS`) request. The Function URL's CORS config must explicitly list `content-type` under **Allow headers**, or the preflight fails with `Request header field content-type is not allowed`. GET requests (the `list`/`detail` actions) don't trigger a preflight, so this only surfaces once POST actions are tested — don't assume GET working means CORS is fully configured.

### 5.6 Dashboard page (`index.html`)

Single static HTML file (vanilla JS, no framework) with three views: session list, session detail (customer info + transcript), and approve/reject actions. Fetches `bluey-banker-api` directly from the browser. Set `API_URL` at the top of the `<script>` block to the real Function URL before deploying.

### 5.7 Deploy to Amplify Hosting

1. Amplify console → Create new app → **Deploy without Git provider**
2. App name: `bluey-banker-review`
3. Drag and drop `index.html` → Save and deploy
4. Live URL: `https://staging.XXXXX.amplifyapp.com` (or similar)

### 5.8 Known gap

Sessions created by testing tools directly (agent console test box, or manually-typed session IDs) rather than through a real `bluey-chat-proxy` conversation will show "No message history" in the dashboard, since they were never populated with a `messages` array — `update_item` creates a partial record if the key doesn't already exist. For realistic demo data, verification should happen via an actual chat through the proxy endpoint.

---

## Part 6: Account Balance & Transaction Lookup Tools

Purpose: let the verified agent answer "what's my balance" / "show my transactions" using real seeded data, as tools on the same single harness (per the single-agent-for-Q&A, separate-agent-for-document-intake decision — see architecture note below).

**STATUS: WORKING** — confirmed via real conversation, balances and transactions cross-checked against seeded data.

### 6.1 Real schema (confirmed via scan before building)

`bluey-accounts`: `accountId` (PK), `customerId`, `accountNumber`, `accountType`, `balance`, `currency`.
`bluey-transactions`: `accountId` (PK), `date#transactionId` (composite sort key), `amount`, `category`, `description`.

### 6.2 IAM — extend `bluey-dynamodb-lambda-role`

```json
{
    "Effect": "Allow",
    "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:Scan"],
    "Resource": [
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions",
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-customers",
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-accounts",
        "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-transactions"
    ]
}
```

### 6.3 New tool branches in `bluey-dynamodb-tool`

```python
    if tool_name == "get_accounts":
        customer_id = event["customerId"]
        accounts_table = dynamodb.Table("bluey-accounts")
        resp = accounts_table.scan(
            FilterExpression="customerId = :cid",
            ExpressionAttributeValues={":cid": customer_id},
        )
        return {"result": resp.get("Items", [])}

    if tool_name == "get_transactions":
        account_id = event["accountId"]
        limit = event.get("limit", 10)
        transactions_table = dynamodb.Table("bluey-transactions")
        resp = transactions_table.query(
            KeyConditionExpression="accountId = :aid",
            ExpressionAttributeValues={":aid": account_id},
            ScanIndexForward=False,
            Limit=limit,
        )
        return {"result": resp.get("Items", [])}
```

### 6.4 Gateway tool schema additions

```json
{
    "name": "get_accounts",
    "description": "Retrieve all accounts belonging to a verified customer",
    "inputSchema": {
        "type": "object",
        "properties": { "customerId": {"type": "string", "description": "The customer's ID (from prior verification)"} },
        "required": ["customerId"]
    }
},
{
    "name": "get_transactions",
    "description": "Retrieve recent transactions for a specific account, most recent first",
    "inputSchema": {
        "type": "object",
        "properties": {
            "accountId": {"type": "string", "description": "The account ID to fetch transactions for"},
            "limit": {"type": "integer", "description": "Max number of transactions to return (default 10)"}
        },
        "required": ["accountId"]
    }
}
```

### 6.5 System prompt addition

> "Only discuss account balances or transactions after the customer has been verified via `lookup_customer`. Once verified, use their `customerId` to call `get_accounts` to list their accounts, and `get_transactions` with a specific `accountId` to show transaction history. Never fabricate balances or transactions — always call these tools."

### 6.6 Architecture decision: single agent vs multi-agent

Decided: **stay single-agent** for conversational Q&A tools (balance, transactions, product recommendations, financial advice) — they share one persona and one conversation, and adding each as a new tool branch on the existing harness is far less overhead than standing up separate agents. **Introduce a second, separate agent only for document intake / banker handoff** (account opening), since that flow is a genuinely different mode — structured intake + human handoff rather than open Q&A — and justifies its own persona/system prompt. Not yet built.

---

## Part 7: Document Upload & Account-Opening Agent

Purpose: a **second, separate harness** dedicated to guiding a prospective customer through opening a new account — collecting name/phone/email, requesting ID + proof of address documents, and flagging the completed application for banker review. Kept separate from the main Bluey harness per the single-agent-for-Q&A / separate-agent-for-intake decision in Part 6.6.

**STATUS: WORKING** end to end — confirmed via a full conversation: info collection → document upload → status check → banker review, including document links visible to the banker.

### 7.1 S3 bucket for documents

Created `bluey-documents-957014951500` (account ID appended for global uniqueness), region `us-east-1`, **Block all public access** left ON — access is only ever via short-lived presigned URLs, never public.

### 7.2 Documents metadata table

DynamoDB table `bluey-documents`: partition key `sessionId` (String), sort key `docType` (String) — so each session has at most one row per document type (`id_document`, `proof_of_address`).

### 7.3 `bluey-document-api` Lambda

Role `bluey-document-lambda-role`:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        { "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": "arn:aws:s3:::bluey-documents-957014951500/*" },
        { "Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"], "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-documents" },
        { "Effect": "Allow", "Action": ["dynamodb:UpdateItem"], "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions" }
    ]
}
```
(the `bluey-sessions` UpdateItem permission is required for the review-flagging step in 7.3.2 below)

Code — three actions (`get_upload_url`, `confirm_upload`, `list_documents`), Python 3.12, region `us-east-1`:
```python
import boto3, json
from datetime import datetime, timezone

s3 = boto3.client("s3", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
documents_table = dynamodb.Table("bluey-documents")
sessions_table = dynamodb.Table("bluey-sessions")

BUCKET = "bluey-documents-957014951500"
HEADERS = {"Content-Type": "application/json"}  # no Access-Control-Allow-Origin here — set via Function URL CORS config only

def lambda_handler(event, context):
    body = json.loads(event.get("body") or "{}")
    params = event.get("queryStringParameters") or {}
    action = params.get("action") or body.get("action")

    if action == "get_upload_url":
        session_id = body["sessionId"]
        doc_type = body["docType"]
        content_type = body.get("contentType", "application/octet-stream")
        s3_key = f"documents/{session_id}/{doc_type}"

        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": BUCKET, "Key": s3_key, "ContentType": content_type},
            ExpiresIn=300,
        )
        documents_table.put_item(Item={
            "sessionId": session_id, "docType": doc_type, "s3Key": s3_key,
            "status": "pending", "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        return _response(200, {"uploadUrl": url, "s3Key": s3_key})

    if action == "confirm_upload":
        session_id = body["sessionId"]
        doc_type = body["docType"]
        documents_table.update_item(
            Key={"sessionId": session_id, "docType": doc_type},
            UpdateExpression="SET #s = :s, uploadedAt = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "uploaded", ":u": datetime.now(timezone.utc).isoformat()},
        )

        # 7.3.2 — flag session for banker review once BOTH required documents are in
        resp = documents_table.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        uploaded_types = [d["docType"] for d in resp.get("Items", []) if d.get("status") == "uploaded"]
        if "id_document" in uploaded_types and "proof_of_address" in uploaded_types:
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET reviewStatus = :rs",
                ExpressionAttributeValues={":rs": "pending_review"},
            )
        return _response(200, {"result": "confirmed"})

    if action == "list_documents":
        session_id = params.get("sessionId") or body.get("sessionId")
        resp = documents_table.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        docs = resp.get("Items", [])
        for doc in docs:
            if doc.get("status") == "uploaded":
                doc["viewUrl"] = s3.generate_presigned_url(
                    "get_object", Params={"Bucket": BUCKET, "Key": doc["s3Key"]}, ExpiresIn=600,
                )
        return _response(200, {"documents": docs})

    return _response(400, {"error": "unknown action"})

def _response(status, body):
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, default=str)}
```
Function URL: Auth NONE, CORS Allow origin `*`, Allow methods `GET, POST`, **Allow headers `content-type`**.

### 7.4 Second harness: `bluey-account-opening`

New AgentCore harness, own Gateway. System prompt:
```
You are Bluey's account-opening assistant. Your job is to guide a customer through opening a new bank account, step by step:

1. Collect the customer's full name, phone number, and email.
2. After the customer confirms their name, phone, and email, call save_applicant_info with these details and their sessionId before continuing.
3. Explain that two documents are required: a valid ID document and a proof of address (dated within the last 3 months).
4. Tell the user to use the upload button in the chat interface for each document. Do not ask them to paste file content — the frontend handles the actual upload separately.
5. After both documents show as uploaded (check via the check_documents_status tool), confirm with the user and let them know their application is now with a banker for review.
6. Never approve an account yourself — you only collect information and documents. A human banker makes the final decision.
7. Be warm, clear, and patient — this may be a stressful process for some users.

User messages may end with a hidden tag like [session_id: xyz] — this is internal context, not part of what the user said. Never mention it to the user. Always pass this value as the sessionId parameter to any tool that needs it.
```

### 7.5 Two new tools on `bluey-dynamodb-tool` (reused, not a new Lambda)

```python
    if tool_name == "check_documents_status":
        session_id = event["sessionId"]
        documents_table = dynamodb.Table("bluey-documents")
        resp = documents_table.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        docs = resp.get("Items", [])
        uploaded_types = [d["docType"] for d in docs if d.get("status") == "uploaded"]
        return {"result": {
            "idUploaded": "id_document" in uploaded_types,
            "proofOfAddressUploaded": "proof_of_address" in uploaded_types,
        }}

    if tool_name == "save_applicant_info":
        session_id = event["sessionId"]
        sessions_table = dynamodb.Table("bluey-sessions")
        sessions_table.update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET applicantName = :n, applicantPhone = :p, applicantEmail = :e",
            ExpressionAttributeValues={
                ":n": event.get("name", ""), ":p": event.get("phone", ""), ":e": event.get("email", ""),
            },
        )
        return {"result": "saved"}
```

**Required IAM fix:** `bluey-dynamodb-lambda-role` must include `bluey-documents` (for `check_documents_status`) in its Resource array — this was missing initially and caused the agent to report "technical issues checking documents":
```json
"Resource": [
    "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-sessions",
    "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-customers",
    "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-accounts",
    "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-transactions",
    "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-documents"
]
```

Gateway tool schema additions (on the account-opening harness's Gateway target):
```json
{
    "name": "check_documents_status",
    "description": "Check whether the customer has uploaded their required documents (ID and proof of address) for this session",
    "inputSchema": { "type": "object", "properties": { "sessionId": {"type": "string"} }, "required": ["sessionId"] }
},
{
    "name": "save_applicant_info",
    "description": "Save a new applicant's collected name, phone, and email to their session, for banker review",
    "inputSchema": {
        "type": "object",
        "properties": { "sessionId": {"type": "string"}, "name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"} },
        "required": ["sessionId", "name", "phone", "email"]
    }
}
```

### 7.6 `bluey-account-opening-proxy` Lambda

Same pattern as `bluey-chat-proxy` (Part 2), pointing at the new harness ARN. **Timeout must be manually set to 30s again** — new Lambdas don't inherit the timeout setting from `bluey-chat-proxy`; this bit us again here exactly as in Part 2.

### 7.7 Handling prospects with no `customerId` in the banker app

Sessions from this flow never get a `customerId` (they're not existing verified customers) — only `applicantName`/`applicantPhone`/`applicantEmail` from `save_applicant_info`. This broke `bluey-banker-api`'s `list`/`detail`, which called `customers_table.get_item(Key={"customerId": ""})` — **DynamoDB rejects empty-string key values**, crashing the handler and making the banker app show nothing at all, silently. Fixed by branching on whether `customerId` exists:

```python
            customer_id = item.get("customerId")
            if customer_id:
                customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
                full_name = customer.get("fullName", "Unknown")
            else:
                full_name = item.get("applicantName", "New applicant")
```
(applied in both `list` and `detail` — see full current code in 7.8)

### 7.8 Document links in the banker dashboard

`bluey-banker-api-role` extended with:
```json
{ "Effect": "Allow", "Action": ["dynamodb:Query"], "Resource": "arn:aws:dynamodb:us-east-1:957014951500:table/bluey-documents" },
{ "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::bluey-documents-957014951500/*" }
```

`detail` action now also returns a `documents` array with presigned `viewUrl`s (10-min expiry — a banker who leaves the tab open past that will need to reload the page for a fresh link, a known minor gap not worth fixing for a hackathon):
```python
    if action == "detail":
        session_id = params.get("sessionId") or body.get("sessionId")
        item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")
        if not item:
            return _response(404, {"error": "not found"})

        customer_id = item.get("customerId")
        if customer_id:
            customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
        else:
            customer = {
                "fullName": item.get("applicantName", "New applicant"),
                "phone": item.get("applicantPhone"),
                "email": item.get("applicantEmail"),
            }

        docs_resp = documents_table.query(
            KeyConditionExpression="sessionId = :sid",
            ExpressionAttributeValues={":sid": session_id},
        )
        documents = []
        for doc in docs_resp.get("Items", []):
            if doc.get("status") == "uploaded":
                doc["viewUrl"] = s3.generate_presigned_url(
                    "get_object", Params={"Bucket": BUCKET, "Key": doc["s3Key"]}, ExpiresIn=600,
                )
            documents.append(doc)

        return _response(200, {"session": item, "customer": customer, "documents": documents})
```
(add `s3 = boto3.client("s3", region_name="us-east-1")`, `documents_table = dynamodb.Table("bluey-documents")`, `BUCKET = "bluey-documents-957014951500"` near the top of `bluey-banker-api`)

`index.html`'s `loadDetail()` updated to render a "Documents" section with view links, above the conversation transcript. Re-uploaded to Amplify to deploy.

### 7.9 Known open item

Transaction visualization (charts in response to "show me my spending") — not yet designed. Needs a decision on delivery mechanism (server-rendered image vs structured chart data for the Figma frontend to render) since Bluey's real frontend is separate from this chat interface.

---

## Part 8: Real Authentication (Cognito)

Purpose: replace the "anyone with the URL can call it" state of every endpoint with real authentication. Decided to use Cognito everywhere (not just the banker app), which reopened — but did not change — the earlier decision that customers identify themselves via SA ID number rather than a login-gated identity; Cognito here secures *which client can call the API at all*, separate from *which bank customer this conversation belongs to*.

**STATUS: WORKING.** Final architecture is a hybrid, for reasons explained in 8.5 — API Gateway + Cognito JWT authorizer for the two non-harness Lambdas (`bluey-banker-api`, `bluey-document-api`), and Function URLs with manual Cognito token verification for the two harness-invoking Lambdas (`bluey-chat-proxy`, `bluey-account-opening-proxy`).

### 8.1 Cognito User Pool

Console → Create user pool → `bluey-user-pool`. Sign-in: email. Self-registration enabled. Required attributes: `email`, `name`. No MFA (hackathon speed). Default Cognito-sent email delivery.

**`Bankers` group** created (Groups tab → Create group) to distinguish bank staff from ordinary customers — checked via `cognito:groups` claim in tokens.

### 8.2 App client — public client gotcha

**First attempt failed**: created an app client via "Traditional web application" type, which defaults to generating a client secret. `initiate_auth` then fails with `NotAuthorizedException: Client ... is configured with secret but SECRET_HASH was not received` — browser/public clients must never hold a secret (can't be kept safe in client-side JS), so a confidential client is the wrong type entirely, not something to work around.

**Fix**: created a new app client with **Application type: Single-page application (SPA)** (not "Traditional web application" — SPA clients are secret-less by design) and explicitly toggled **"Automatically generate a new client secret" OFF**. Also enabled **ALLOW_USER_PASSWORD_AUTH** under Authentication flows (needed for direct `initiate_auth` testing via boto3; a real frontend would use the Hosted UI or Amplify Auth instead).

### 8.3 Test user + `NEW_PASSWORD_REQUIRED` challenge

A console-created test user with a temporary password returns a `NEW_PASSWORD_REQUIRED` challenge on first `initiate_auth`, not tokens directly:
```python
resp = client.initiate_auth(
    ClientId="YOUR_PUBLIC_CLIENT_ID",
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": "test@example.com", "PASSWORD": "TempPass123!"},
)
# resp["ChallengeName"] == "NEW_PASSWORD_REQUIRED"

resp2 = client.respond_to_auth_challenge(
    ClientId="YOUR_PUBLIC_CLIENT_ID",
    ChallengeName="NEW_PASSWORD_REQUIRED",
    Session=resp["Session"],
    ChallengeResponses={
        "USERNAME": "test@example.com",
        "NEW_PASSWORD": "NewPass123!",
        "userAttributes.name": "Test Banker",  # required — user pool has `name` as a required attribute; omitting it errors with "Invalid attributes given, name is missing"
    },
)
id_token = resp2["AuthenticationResult"]["IdToken"]
access_token = resp2["AuthenticationResult"]["AccessToken"]
```

### 8.4 API Gateway + Cognito JWT authorizer — for `bluey-banker-api` and `bluey-document-api`

Created one HTTP API, `bluey-api`, with routes `/banker` and `/documents` (POST + GET where applicable), each Lambda-integrated to the matching function.

JWT authorizer (`bluey-cognito-authorizer`): Identity source `$request.header.Authorization`, Issuer `https://cognito-idp.us-east-1.amazonaws.com/YOUR_USER_POOL_ID`, Audience = the public App Client ID. Attached to every route.

**Key finding: HTTP API's event format is nearly identical to Function URLs** — `event["body"]`, `event["queryStringParameters"]`, `event["requestContext"]["http"]["method"]` all carry over, so existing Lambda logic needed almost no changes, just the auth/group check added.

Claims arrive at:
```python
claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
user_sub = claims["sub"]
groups = claims.get("cognito:groups", "")
is_banker = "Bankers" in groups
```

`bluey-banker-api`: added as the first lines of `lambda_handler`:
```python
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    groups = claims.get("cognito:groups", "")
    if "Bankers" not in groups:
        return _response(403, {"error": "forbidden — bankers only"})
```

`bluey-document-api`: mixed access (customers upload their own docs; only bankers list/view). `is_banker` computed once near the top, then gated specifically on the `list_documents` action:
```python
    if action == "list_documents":
        if not is_banker:
            return _response(403, {"error": "forbidden — bankers only"})
```

Test with the **ID token** (`Authorization: Bearer <id_token>`) — confirmed both a positive case (Bankers-group user gets `200` from `/banker`) and a negative case (non-Bankers user gets `403`).

### 8.5 Fundamental conflict discovered: API Gateway's hard timeout vs. AgentCore cold starts

Migrating `bluey-chat-proxy` to the same API-Gateway-plus-JWT-authorizer pattern initially seemed like the consistent choice, but it broke on real testing: even a plain "Hi" returned `503 {"message": "Service Unavailable"}`.

**Root cause**: CloudWatch showed the Lambda itself ran for ~44 seconds — this is a harness cold start, not a bug. AWS's own docs confirm AgentCore Runtime cold-starts add meaningful latency on the first invocation of an idle session (observed directly: first message ~60s, second message ~10s, matching a cold-then-warm pattern). **API Gateway's HTTP API integration timeout is a hard 29-second ceiling that cannot be raised** — no Lambda timeout setting fixes this, because API Gateway gives up and returns 503 before the Lambda even finishes. Function URLs, by contrast, have no such ceiling.

**Decision**: keep `bluey-banker-api` and `bluey-document-api` on API Gateway (they never invoke the harness, so they're always fast — the 29s ceiling is a non-issue). Move `bluey-chat-proxy` and `bluey-account-opening-proxy` **back to Function URLs**, and verify Cognito tokens manually inside the Lambda code instead of relying on an API Gateway authorizer.

### 8.6 Manual token verification for the harness-invoking Lambdas

Uses `boto3`'s built-in `cognito-idp.get_user()` — validates a token by asking Cognito directly, no extra libraries/layers needed. Requires the **access token**, not the ID token used for the API Gateway path.

```python
cognito_client = boto3.client("cognito-idp", region_name="us-east-1")

def verify_token(event):
    headers = event.get("headers", {}) or {}
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer "):]
    try:
        resp = cognito_client.get_user(AccessToken=token)
        attrs = {a["Name"]: a["Value"] for a in resp["UserAttributes"]}
        return attrs.get("sub")
    except cognito_client.exceptions.NotAuthorizedException:
        return None

def lambda_handler(event, context):
    user_id = verify_token(event)
    if not user_id:
        return {"statusCode": 401, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": "unauthorized"})}
    # ... rest unchanged; user_id (the Cognito sub) replaces the old body-supplied user_id
```

IAM addition on `bluey-chat-proxy-role` and the account-opening equivalent:
```json
{ "Effect": "Allow", "Action": "cognito-idp:GetUser", "Resource": "*" }
```

Function URLs on both Lambdas: Auth type **NONE** (auth is handled in code now), CORS Allow headers must include `content-type, authorization`.

### 8.7 Final architecture summary

| Lambda | Entry point | Auth mechanism |
|---|---|---|
| `bluey-chat-proxy` | Function URL | Manual — `cognito-idp.get_user()` on access token |
| `bluey-account-opening-proxy` | Function URL | Manual — `cognito-idp.get_user()` on access token |
| `bluey-banker-api` | API Gateway (`/banker`) | API Gateway JWT authorizer + `Bankers` group check |
| `bluey-document-api` | API Gateway (`/documents`) | API Gateway JWT authorizer + `Bankers` group check (banker-only actions) |

**Frontend implication**: the customer-facing app (Figma team) needs the SPA app client, a login/signup screen (or Cognito Hosted UI), and to send `Authorization: Bearer <access_token>` on every request to `/chat` and `/account-opening`. The banker dashboard needs the same login flow but sends the **ID token** to `/banker` and `/documents` instead.

---

## Part 9: Transaction Visualization (Chart Data)

Purpose: let customers ask to "see" or "visualize" their spending. Since Bluey's real frontend is a separate app (Figma team, not this chat interface), the backend returns **structured chart data** (labels + values), not a rendered image — the frontend's own charting library renders it in their design system.

**STATUS: WORKING** — confirmed end-to-end via a real conversation (verify → ask for spending breakdown → `chartData` present in the proxy's JSON response).

### 9.1 Design: getting tool output back to the proxy

`invoke_harness` only streams text — tool call results never reach `bluey-chat-proxy` directly. Reused the same pattern as `lookup_customer` (Part 4): the tool writes its output onto the session record (`pendingChartData`), and the proxy reads + clears that field after the harness call finishes, folding it into the JSON response for that turn only.

### 9.2 `get_transaction_chart` tool (in `bluey-dynamodb-tool`)

```python
from decimal import Decimal

    if tool_name == "get_transaction_chart":
        account_id = event["accountId"]
        session_id = event.get("sessionId")

        transactions_table = dynamodb.Table("bluey-transactions")
        resp = transactions_table.query(
            KeyConditionExpression="accountId = :aid",
            ExpressionAttributeValues={":aid": account_id},
        )
        items = resp.get("Items", [])

        category_totals = {}
        for txn in items:
            amt = float(txn.get("amount", 0))
            if amt < 0:  # spending only, not income
                cat = txn.get("category", "Other")
                category_totals[cat] = category_totals.get(cat, 0) + abs(amt)

        chart_data = {
            "type": "bar",
            "title": "Spending by Category",
            "labels": list(category_totals.keys()),
            "values": [round(v, 2) for v in category_totals.values()],
        }

        if session_id:
            sessions_table = dynamodb.Table("bluey-sessions")
            dynamo_safe_chart = {
                **chart_data,
                "values": [Decimal(str(v)) for v in chart_data["values"]],
            }
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET pendingChartData = :c",
                ExpressionAttributeValues={":c": dynamo_safe_chart},
            )

        return {"result": chart_data}
```

**Gotcha hit**: initial version wrote plain Python `float`s into the DynamoDB item, which fails with `TypeError: Float types are not supported. Use Decimal types instead.` — boto3's DynamoDB SDK requires `Decimal` for all numeric values, never `float`. **General rule for any future numeric tool**: keep `float` in values returned directly to the agent/tool caller (fine there), but always convert to `Decimal` (typically `Decimal(str(x))`, not `Decimal(x)`, to avoid floating-point precision artifacts) before any DynamoDB write.

### 9.3 Gateway tool schema addition (main Bluey harness)

```json
{
    "name": "get_transaction_chart",
    "description": "Generate spending-by-category chart data for an account, when the customer asks to see, visualize, or break down their spending",
    "inputSchema": {
        "type": "object",
        "properties": { "accountId": {"type": "string"}, "sessionId": {"type": "string"} },
        "required": ["accountId", "sessionId"]
    }
}
```

### 9.4 System prompt addition

> "When a customer asks to see, visualize, or get a chart of their spending, first call `get_accounts` if you don't already have their accountId, then call `get_transaction_chart` with that accountId and the current sessionId. Only claim a chart was created if the tool call actually succeeded. After calling it, briefly describe what the chart shows in your reply — the chart itself will be displayed automatically by the app, so don't try to describe every number."

### 9.5 `bluey-chat-proxy` — surfacing chart data in the response

After building `messages` for the turn, before the final session write:
```python
    session_check = sessions_table.get_item(Key={"sessionId": session_id}).get("Item", {})
    chart_data = session_check.get("pendingChartData")

    sessions_table.update_item(
        Key={"sessionId": session_id},
        UpdateExpression="SET messages = :m, updatedAt = :u, userId = if_not_exists(userId, :uid), createdAt = if_not_exists(createdAt, :c)" + (" REMOVE pendingChartData" if chart_data else ""),
        ExpressionAttributeValues={
            ":m": messages, ":u": now, ":uid": user_id,
            ":c": (existing.get("createdAt") if existing else now) or now,
        },
    )

    response_body = {"reply": full_text, "session_id": session_id}
    if chart_data:
        response_body["chartData"] = chart_data

    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(response_body, default=str)}
```
This also clears `pendingChartData` in the same write (`REMOVE`) so it never leaks into a later, unrelated turn's response.

### 9.6 Frontend contract addition

Response shape now optionally includes:
```json
{"reply": "...", "session_id": "...", "chartData": {"type": "bar", "title": "Spending by Category", "labels": [...], "values": [...]}}
```
`chartData` is only present on turns where a visualization was actually requested and generated — absent otherwise.

---

## Open Items / Next Steps

- [x] Fix and confirm `bluey-chat-proxy`
- [x] Automatic session persistence (Part 3)
- [x] Customer identity verification (Part 4)
- [x] Banker-facing review interface (Part 5)
- [x] Fixed: `put_item` overwrite bug wiping tool-written fields (now `update_item`)
- [x] Account balance / transaction lookup tools (Part 6)
- [x] Architecture decision: single agent for Q&A tools, separate agent for document intake (Part 6.6)
- [x] Document upload + account-opening agent, full flow (Part 7)
- [x] Real authentication via Cognito, hybrid architecture (Part 8)
- [x] Transaction visualization / chart data (Part 9)
- [ ] Define the request/response contract for the Figma frontend team, including Cognito login + Bearer token requirement and the new `chartData` field
- [ ] Transaction visualization — needs a decision on delivery mechanism (server-rendered image vs structured chart data for the Figma frontend)
- [ ] Product recommendations tool — plan to reuse logic from the customer-intelligence segmentation/recommendation notebook (separate repo, not yet integrated)
- [ ] Financial advisor reasoning (single-agent, likely mostly prompt work on top of existing tools + KB)
- [ ] Minor: presigned document view links expire after 10 min — acceptable for hackathon, not fixed
- [ ] AgentCore harness cold starts (~30-60s first call) — acceptable for hackathon demo pacing, but worth knowing if judged on responsiveness; AWS's pre-warming/warm-pool strategies exist if needed later
