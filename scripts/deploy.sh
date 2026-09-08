#!/usr/bin/env bash
set -euo pipefail
STAGE="${1:-dev}"
PROFILE="${AWS_PROFILE:-$(python -c 'import yaml; print(yaml.safe_load(open("config/environments.yaml"))["'"$STAGE"'"]["aws_profile"])')}"
REGION="${AWS_REGION:-$(python -c 'import yaml; print(yaml.safe_load(open("config/environments.yaml"))["'"$STAGE"'"]["region"])')}"
export AWS_PROFILE="$PROFILE" AWS_DEFAULT_REGION="$REGION" CDK_DEFAULT_REGION="$REGION"
# BLUEY_STAGE must be propagated explicitly — infra/app.py falls back to context
# "stage" (cdk.json defaults this to "dev") if this isn't set, which would
# silently deploy every stage as "dev" (including its RemovalPolicy.DESTROY
# behavior on data tables) regardless of the argument passed to this script.
export BLUEY_STAGE="$STAGE"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
printf 'Deploying stage=%s account=%s region=%s profile=%s\n' "$STAGE" "$ACCOUNT_ID" "$REGION" "$PROFILE"
if [[ -n "${BLUEY_EXPECTED_ACCOUNT_ID:-}" && "$ACCOUNT_ID" != "$BLUEY_EXPECTED_ACCOUNT_ID" ]]; then
	printf 'ERROR: AWS account %s does not match BLUEY_EXPECTED_ACCOUNT_ID=%s\n' "$ACCOUNT_ID" "$BLUEY_EXPECTED_ACCOUNT_ID" >&2
	exit 1
fi
python -m pip install -q -r requirements-dev.txt
npx cdk bootstrap "aws://$ACCOUNT_ID/$REGION"
npx cdk synth --context stage="$STAGE"
npx cdk diff --context stage="$STAGE"
npx cdk deploy --all --context stage="$STAGE" --require-approval never
python scripts/verify_permissions.py --region "$REGION"
