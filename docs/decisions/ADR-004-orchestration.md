# ADR-004: Step Functions for orchestration, dbt on GitHub Actions

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed | 2026-10-19 | Prabhu Saravanan | PRD FR-22, NFR-02, NFR-03, NFR-06 · HLD §4, §6.2 |

## Context

The daily run has ~10 dependent steps: start RDS/DMS, ingest, Athena SCD2 merge, a fan-out of Bedrock calls, embeddings, matching, CDC merge, stop RDS/DMS, dbt build, digest. It needs retries, failure alerts, cleanup on failure (always stop RDS/DMS), and must not cost money when idle. dbt Core needs a Python environment with dbt-athena installed.

## Decision drivers

- No always-on compute (NFR-03)
- Retries, catch/cleanup and visibility per step (NFR-02, FR-22)
- No long-lived AWS keys (NFR-06)
- dbt runs close to its code and CI
- Reasonable build effort in 3 weeks

## Options considered

| Option | Idle cost | Pros | Cons |
|---|---|---|---|
| **Step Functions (AWS steps) + dbt on GitHub Actions** | ~$0 | Native integrations (Lambda, Athena `.sync`, RDS, DMS, Bedrock); visual run history; free-tier GitHub runners; OIDC | Two systems to correlate |
| Amazon MWAA (Airflow) | Well over $100/month even at the smallest size | One orchestrator, familiar from ADF/Airflow | Breaks budget |
| Step Functions running dbt in ECS Fargate | Low (per run) | Single orchestrator | Container image, task networking (no NAT → needs public subnet or endpoints), more Terraform |
| Step Functions running dbt in Lambda | Low | Single orchestrator | dbt packaging in Lambda is fragile; 15-min limit |
| GitHub Actions for everything (cron) | $0 | One place | AWS steps from a runner are slower and less observable; RDS/DMS cleanup is harder to guarantee |

## Decision

- **Step Functions (Standard workflow)** `stellenradar-daily`, started by **EventBridge Scheduler** at 06:00 Europe/Berlin, runs every AWS step.
- Every task has `Retry` with exponential backoff and a `Catch` to a cleanup branch (stop DMS → stop RDS → SNS alert → fail).
- The last step invokes a small `dispatch-dbt` Lambda that sends a GitHub **`repository_dispatch`** event (fine-grained token, repo-scoped, stored in Secrets Manager).
- The GitHub Actions workflow authenticates with **OIDC**, runs `dbt build`, and **invokes the digest Lambda only if dbt succeeds**.
- A fallback GitHub cron at 09:00 Europe/Berlin runs dbt only if no dbt run happened that day.

## Consequences

**Positive**
- Near-zero idle cost; Step Functions Standard is billed per state transition (a few hundred per day).
- Clear run history and per-step error visibility in the console.
- No stale digest: failures stop the chain before the email.

**Negative / risks**
- Correlating a Step Functions execution with a GitHub run: pass `run_id` in the dispatch payload and log it in both.
- A GitHub token is a secret to rotate (fine-grained token, 90-day expiry, calendar reminder).
- GitHub Actions scheduled workflows can be delayed; acceptable because dispatch is the primary trigger.

## Validation and revisit triggers

- Week 3: a forced failure in any step triggers cleanup (RDS stopped) and one alert email.
- Revisit if dbt runtime approaches GitHub limits, or if a second pipeline makes a single orchestrator (MWAA) worth its cost.
