# ADR-006: DMS Serverless for CDC, started and stopped per run

| Status | Date | Deciders | Related |
|---|---|---|---|
| **Superseded by [ADR-009](ADR-009-dms-instance-per-run.md)** | 2026-10-19 | Prabhu Saravanan | PRD FR-11–13, NFR-03, SM-04 · HLD §6.4 · ADR-002, ADR-003 |

> **Superseded (2026-10-20):** DMS Serverless keeps a stopped replication provisioned – and billing – for 48 hours, so a daily start/stop bills around the clock. See [ADR-009](ADR-009-dms-instance-per-run.md).

## Context

Objective 3 requires replicating a personal application tracker from RDS PostgreSQL to the lake using **AWS DMS** (an explicit learning goal), with changes visible within one run window. The tracker changes a few times a week. A provisioned DMS replication instance bills every hour it exists – it cannot be stopped, only deleted.

## Decision drivers

- Use AWS DMS (portfolio objective)
- Monthly budget ≤ $30
- Changes visible within one run window (SM-04), not real-time
- Minimal operational effort

## Options considered

| Option | Cost profile | Pros | Cons |
|---|---|---|---|
| Provisioned DMS instance (`dms.t3.micro`), always on | Billed 24/7 | Simplest | A large share of the budget for a few changes per week |
| Provisioned instance, created and deleted by the pipeline each run | Billed only during the run | Cheap | Slow (instance creation adds minutes); Terraform drift; complex |
| **DMS Serverless, replication started and stopped by the pipeline** | Billed for capacity only while replication runs | No instance to manage; native start/stop API | First start provisions capacity (minutes); billing behaviour to verify |
| Logical decoding in a Lambda / Debezium | Low | Flexible | Not DMS – misses the objective; more code |
| Nightly full export (no CDC) | Near zero | Simplest | Not CDC – misses the objective; loses deletes history |

## Decision

Use **DMS Serverless** with a **full load + CDC** replication config from the tracker database to `s3://…/cdc/applications/` in Parquet, with DMS's operation column (`Op` = I/U/D) and commit timestamps.

- The daily state machine **starts** replication right after starting RDS and **stops** it after the CDC merge.
- While RDS and DMS are stopped, changes accumulate in PostgreSQL's **logical replication slot** and are delivered on the next start.
- Athena merges CDC files into `curated.applications` (Iceberg) using the `Op` column and commit timestamp ordering.

## Consequences

**Positive**
- Meets the DMS objective at a cost proportional to run time.
- No replication instance to size, patch or delete.

**Negative / risks**
- **Replication slot WAL growth:** if DMS never restarts, WAL accumulates on the stopped instance. Mitigation: daily run; alarm on `OldestReplicationSlotLag` / `TransactionLogsDiskUsage` while running.
- **Start-up time:** serverless provisioning may take several minutes; the state machine waits with a timeout.
- RDS parameter group needs `rds.logical_replication = 1` (requires a reboot at creation – handled by Terraform before first use).
- Exact billing while stopped, and credential access without a Secrets Manager endpoint, are unverified.

## Validation and revisit triggers

- **Spike S3 (Week 1/2):** (a) DMS Serverless in private subnets with only the S3 gateway endpoint can read source credentials and write to S3; (b) no charges accrue while replication is stopped; (c) measured start-to-running time. Record results here.
- Fallback if (a) or (b) fails: provisioned `dms.t3.micro` created and deleted per run, recorded as a new ADR superseding this one.
- SM-04: an insert, update and delete in the tracker each appear correctly in `curated.applications` after one run.
