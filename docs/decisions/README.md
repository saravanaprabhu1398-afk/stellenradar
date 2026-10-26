# Architecture Decision Records

Each ADR records one significant decision: the context, the options considered, the choice, and its consequences. ADRs are immutable once accepted – a changed decision gets a new ADR that supersedes the old one.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-iceberg-table-format.md) | Apache Iceberg as table format | Accepted |
| [ADR-002](ADR-002-pgvector-vector-store.md) | pgvector on RDS as vector store | Proposed |
| [ADR-003](ADR-003-no-nat-gateway.md) | No NAT Gateway: split internet-facing and VPC-bound Lambdas | Proposed |
| [ADR-004](ADR-004-orchestration.md) | Step Functions for orchestration, dbt on GitHub Actions | Proposed |
| [ADR-005](ADR-005-bedrock-models.md) | Bedrock models and EU cross-region inference (Amazon Nova + Titan V2) | Accepted (model family) |
| [ADR-006](ADR-006-dms-serverless-cdc.md) | DMS Serverless for CDC, started and stopped per run | Superseded by ADR-009 |
| [ADR-007](ADR-007-rds-iam-auth.md) | RDS IAM authentication for VPC Lambdas | Proposed |
| [ADR-008](ADR-008-single-environment.md) | Single `dev` environment | Proposed |
| [ADR-009](ADR-009-dms-instance-per-run.md) | Provisioned DMS instance created and deleted per run | Proposed |

**Statuses:** Proposed → Accepted (after review) → Superseded / Deprecated.
ADRs that depend on a Week 1 spike move to *Accepted* only after the spike result is recorded in the ADR.

## Template

```markdown
# ADR-NNN: Title

| Status | Date | Deciders | Related |
|---|---|---|---|

## Context
## Decision drivers
## Options considered
## Decision
## Consequences
## Validation and revisit triggers
```
