#!/usr/bin/env bash
set -euo pipefail
STAGE="${1:-dev}"
PROFILE="${AWS_PROFILE:-$(python -c 'import yaml; print(yaml.safe_load(open("config/environments.yaml"))["'"$STAGE"'"]["aws_profile"])')}"
REGION="${AWS_REGION:-$(python -c 'import yaml; print(yaml.safe_load(open("config/environments.yaml"))["'"$STAGE"'"]["region"])')}"
export AWS_PROFILE="$PROFILE" AWS_DEFAULT_REGION="$REGION"
aws sts get-caller-identity
python -m pip install -q -r requirements-dev.txt
npx cdk synth --strict
npx cdk diff
npx cdk deploy --all --require-approval never
python scripts/verify_permissions.py
