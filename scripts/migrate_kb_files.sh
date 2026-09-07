#!/usr/bin/env bash
set -euo pipefail
SOURCE_PROFILE="${1:?source AWS profile}"
TARGET_PROFILE="${2:?target AWS profile}"
SOURCE_BUCKET="${3:?source KB bucket}"
TARGET_BUCKET="${4:?target KB bucket}"
REGION="${AWS_REGION:-us-east-1}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
AWS_PROFILE="$SOURCE_PROFILE" aws s3 sync "s3://$SOURCE_BUCKET/" "$TMP/" --region "$REGION"
AWS_PROFILE="$TARGET_PROFILE" aws s3 sync "$TMP/" "s3://$TARGET_BUCKET/" --region "$REGION"
echo "Copied knowledge-base source files."
