# Infrastructure (Terraform)

All AWS resources for Stellenradar, in `eu-central-1`. Design: [SDD §5](../docs/03-sdd.md), [security](../docs/05-security.md), [ADR-008](../docs/decisions/ADR-008-single-environment.md).

## Layout

| Path | State | Contents |
|---|---|---|
| `bootstrap/` | Local (`terraform.tfstate`, git-ignored) | Terraform state bucket `stellenradar-tfstate-<account>` – applied once |
| `envs/dev/` | S3 backend, `envs/dev/terraform.tfstate`, native lockfile | Wires the modules together for the `dev` environment |
| `modules/monitoring/` | – | Monthly budget ($10/$25/$50 actual, $30 forecast) and the account's cost anomaly alerts |
| `modules/github_oidc/` | – | GitHub Actions roles: `gha-terraform-plan` (pull requests, read-only) and `gha-terraform-apply` (protected `dev` environment only) |

Pre-existing account resources: the cost anomaly monitor and subscription were **imported** and are managed here. The GitHub OIDC provider is **looked up, not managed**, because other workloads in the account may use it.

## How changes are deployed

```
pull request ──► terraform.yml "plan"  (role gha-terraform-plan, plan shown in the job summary)
merge to main ──► terraform.yml "apply" (waits for approval in environment dev, role gha-terraform-apply)
```

No AWS keys exist anywhere – both jobs use GitHub OIDC. The role trust policies match GitHub's ID-based token subject (`repo:<owner>@<owner_id>/<repo>@<repo_id>:...`), so a renamed or re-created repository with the same name cannot assume them.

## One-time setup (already done for this account)

1. `terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply` – state bucket.
2. Local files in `infra/envs/dev/` (both git-ignored):
   - `backend.hcl` – copy of `backend.hcl.example` with the account ID filled in
   - `terraform.tfvars` – `alert_email = "..."`
3. `make bootstrap-ci` – applies `envs/dev` locally once, creating the OIDC roles CI needs.
4. `make github-setup` – sets repository variables `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`, `TF_STATE_BUCKET`, secret `ALERT_EMAIL`, and the protected environment `dev` (requires the GitHub CLI).

## Local commands

```bash
aws sso login
make tf-fmt      # format
make tf-plan     # init + plan, writes infra/envs/dev/dev.tfplan
make tf-apply    # apply the saved plan
```

Prefer CI for applies; local applies are for bootstrap and emergencies.
