#!/usr/bin/env bash
# One-time GitHub setup for the Terraform workflow: repository variables, the ALERT_EMAIL secret
# and the protected "dev" environment. Run after `make bootstrap-ci`. Requires the GitHub CLI (gh).
set -euo pipefail

REPO="saravanaprabhu1398-afk/stellenradar"
TF_DIR="infra/envs/dev"

command -v gh >/dev/null || { echo "GitHub CLI missing: brew install gh && gh auth login"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Not logged in: gh auth login"; exit 1; }

plan_role=$(terraform -chdir="$TF_DIR" output -raw gha_plan_role_arn)
apply_role=$(terraform -chdir="$TF_DIR" output -raw gha_apply_role_arn)
bucket="stellenradar-tfstate-$(aws sts get-caller-identity --query Account --output text)"
email=$(sed -n 's/^alert_email *= *"\(.*\)"/\1/p' "$TF_DIR/terraform.tfvars")
[ -n "$email" ] || { echo "alert_email not found in $TF_DIR/terraform.tfvars"; exit 1; }

echo "Setting repository variables and secret..."
gh variable set AWS_PLAN_ROLE_ARN --repo "$REPO" --body "$plan_role"
gh variable set AWS_APPLY_ROLE_ARN --repo "$REPO" --body "$apply_role"
gh variable set TF_STATE_BUCKET --repo "$REPO" --body "$bucket"
gh secret set ALERT_EMAIL --repo "$REPO" --body "$email"

echo "Creating protected environment 'dev' (you as required reviewer, main branch only)..."
user_id=$(gh api user --jq .id)
if ! gh api -X PUT "repos/$REPO/environments/dev" --input - >/dev/null <<JSON
{
  "reviewers": [{"type": "User", "id": $user_id}],
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
then
  echo "Could not create a protected environment. Private repositories need GitHub Pro/Team for this;"
  echo "make the repository public or upgrade, then re-run this script."
  exit 1
fi
gh api -X POST "repos/$REPO/environments/dev/deployment-branch-policies" \
  -f name=main -f type=branch >/dev/null 2>&1 || echo "(branch policy for main already exists)"

echo "Done. Check: https://github.com/$REPO/settings/environments"
