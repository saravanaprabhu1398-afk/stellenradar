# ADR-009: Provisioned DMS instance created and deleted per run

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed – pending spike S3 | 2026-10-20 | Prabhu Saravanan | Supersedes [ADR-006](ADR-006-dms-serverless-cdc.md) · PRD FR-12, NFR-03, SM-04 · [Cost model](../06-cost-model.md) |

## Context

ADR-006 chose DMS Serverless, started and stopped by the daily pipeline, assuming it bills only while replicating. Research for the cost model showed this is wrong:

- AWS documents that a stopped serverless replication stays provisioned for **48 hours** before its resources are deprovisioned, and a hands-on test ([Classmethod, 2025](https://dev.classmethod.jp/en/articles/aws-dms-serverless-stop-billing-48hours/)) observed DCU charges continuing for that whole window.
- With a daily start, the replication is never idle for 48 hours, so it bills **≥ 1 DCU around the clock** – roughly $80+/month at observed rates. That alone breaks the $30 budget.

A provisioned replication instance cannot be stopped either; an always-on `dms.t3.micro` costs roughly $15/month (estimate, to be confirmed in the AWS Pricing Calculator), half the budget for a tracker that changes a few times a week.

## Decision drivers

- Keep AWS DMS (portfolio objective)
- Monthly budget ≤ $30
- Changes visible within one run window (SM-04)

## Options considered

| Option | Est. monthly cost | Notes |
|---|---|---|
| DMS Serverless, daily start/stop (ADR-006) | ~$80+ | 48-hour deprovisioning window – rejected |
| Provisioned `dms.t3.micro`, always on | ~$15 | Simple, but half the budget |
| **Provisioned `dms.t3.micro`, created and deleted by Step Functions each run** | ~$1 | ~30 instance-hours/month; more orchestration logic |
| Run CDC weekly only | lower | Breaks SM-04 |
| Drop DMS for a nightly export | ~$0 | Breaks the DMS objective |

## Decision

**Terraform** owns everything long-lived: replication subnet group, source endpoint (with a fixed `slotName`), S3 target endpoint, IAM roles and secrets.

**Step Functions** owns the short-lived parts on every run:
1. `CreateReplicationInstance` (`dms.t3.micro`, single-AZ, smallest allowed storage, private subnets, `sg-dms`) – wait until `available`.
2. `CreateReplicationTask` – `cdc` mode starting from the saved checkpoint (first run: `full-load-and-cdc`).
3. `StartReplicationTask` – wait until CDC latency ≈ 0.
4. `StopReplicationTask` – save `RecoveryCheckpoint` to `s3://…/cdc/_state/checkpoint.json`.
5. `DeleteReplicationTask`, then `DeleteReplicationInstance`.

The PostgreSQL logical replication slot `stellenradar_dms` is created once by the DB bootstrap and is **persistent**, so WAL for changes made between runs is retained and replayed by the next task.

The failure branch of the state machine always deletes the task and instance, so a failed run cannot leave an instance billing. A Budgets alert is the second safety net.

## Consequences

**Positive**
- DMS cost drops from ~$15–80 to ~$1 per month.
- Demonstrates a real cost-engineering pattern: ephemeral infrastructure driven by the orchestrator.

**Negative / risks**
- Instance creation adds ~5–10 minutes per run (overlapped with matching where possible).
- More states and IAM permissions in the state machine (`dms:Create*`, `dms:Delete*`, `iam:PassRole` for the DMS roles).
- Resuming CDC on a new task from a saved checkpoint with a pre-created slot must be proven – **spike S3**.
- If a checkpoint is lost, recovery is a new full load; acceptable because the tracker is tiny.

## Validation and revisit triggers

- **Spike S3 (Week 2, day 1):** (a) a new task on a new instance resumes from `checkpoint.json` using the existing slot without losing or duplicating changes; (b) DMS in private subnets with only the S3 gateway endpoint can read the source secret and write to S3; (c) measured create-to-available time.
- Fallback if (a) fails: always-on `dms.t3.micro` (~$15/month) with other costs cut, recorded in a new ADR.
- Revisit if AWS changes DMS Serverless deprovisioning behaviour.
