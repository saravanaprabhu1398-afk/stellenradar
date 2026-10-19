# ADR-008: Single `dev` environment

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed | 2026-10-19 | Prabhu Saravanan | PRD NFR-03, NFR-05 · HLD §6.5 |

## Context

Production teams usually run dev, test and prod environments, often in separate AWS accounts. This project has one user, one AWS account and a $30/month budget. Every environment duplicates RDS, DMS and storage costs.

## Decision drivers

- Monthly budget ≤ $30
- Show production practices without paying for them twice
- Ability to test changes safely before they affect the daily run

## Options considered

| Option | Cost | Pros | Cons |
|---|---|---|---|
| **One environment (`dev`), structured for more** | 1× | Fits budget; Terraform modules + `envs/<name>/` make a second env a copy | Changes are tested against the live pipeline |
| dev + prod in one account | ~2× | Safer changes | Over budget (two RDS instances) |
| Separate accounts via AWS Organizations | ~2× + setup | Best isolation, real-world pattern | Over budget and over scope for 3 weeks |

## Decision

- Deploy one environment named **`dev`** from `infra/envs/dev/`, built entirely from reusable modules in `infra/modules/`.
- Resource names and tags include the environment (`stellenradar-dev-*`, `env=dev`).
- Safety without a second environment:
  - PRs run `terraform fmt`, `validate` and `plan` only; `apply` runs only from `main`.
  - dbt runs `build` against a separate `dev_ci` schema in PRs and against `marts` only from `main`.
  - Lambdas and SQL changes are unit-tested in CI before merge.
  - Raw data is immutable, so curated tables can always be rebuilt after a bad change.

## Consequences

**Positive**
- Stays within budget.
- Adding a `prod` environment later is a new folder and a backend config, not a redesign.

**Negative / risks**
- A bad merge can break the daily run. Mitigation: plan-on-PR, tests, alerts, and rebuild-from-raw.
- Not a demonstration of multi-account governance; called out honestly in the README.

## Validation and revisit triggers

- Week 1: `infra/envs/dev` uses only modules; no hard-coded environment names in modules.
- Revisit if the project gains other users, or if the budget allows a second environment.
