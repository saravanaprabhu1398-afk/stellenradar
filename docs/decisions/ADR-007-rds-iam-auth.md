# ADR-007: RDS IAM authentication for VPC Lambdas

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed | 2026-10-19 | Prabhu Saravanan | PRD NFR-06, NFR-07, NFR-08 · HLD §6.1, §7 · ADR-003 |

## Context

The match Lambda runs inside the VPC with no internet route and only an S3 gateway endpoint (ADR-003). It must authenticate to PostgreSQL. The usual pattern – read a password from Secrets Manager – needs a Secrets Manager interface endpoint (~$8/month per AZ) or a NAT Gateway.

## Decision drivers

- No interface endpoints or NAT (budget)
- No passwords in environment variables or code (NFR-07)
- Least-privilege, per-component identity (NFR-06)

## Options considered

| Option | Network need | Security | Notes |
|---|---|---|---|
| Secrets Manager + interface endpoint | Endpoint (~$8/month/AZ) | Good | Over budget for one Lambda |
| Password in Lambda environment variable (encrypted) | None | Weak – secret visible to anyone with `GetFunctionConfiguration` | Violates NFR-07 |
| Secrets Manager read by the orchestrator and passed in the payload | None | Weak – secret appears in Step Functions history | Rejected |
| **RDS IAM database authentication** | **None** – token is signed locally with the Lambda's role credentials | Good – short-lived (15 min) tokens, no stored password | Requires TLS and a DB role granted `rds_iam` |

## Decision

- Enable **IAM database authentication** on the RDS instance.
- Create a PostgreSQL role `match_app` with `GRANT rds_iam TO match_app` and only the privileges it needs (vector tables; read on tracker tables for exclusions).
- The match Lambda's IAM role gets `rds-db:connect` on `arn:aws:rds-db:eu-central-1:<account>:dbuser:<resource-id>/match_app` only.
- The Lambda generates a token with `generate_db_auth_token` (local SigV4 signing) and connects with `sslmode=verify-full` using the RDS CA bundle packaged with the function.
- The RDS **master password** stays in Secrets Manager (managed by RDS) and is used only for administration, never by application code.

## Consequences

**Positive**
- No secret to store, rotate or leak for the application path; no endpoint cost.
- Access is controlled in IAM and auditable in CloudTrail.

**Negative / risks**
- IAM auth is rate-limited for new connections (fine for one Lambda connecting once per run).
- The CA bundle must be packaged and kept current.
- Creating the `match_app` role and extensions (`vector`) requires an admin connection once – handled by a migration step (detailed in the SDD).

## Validation and revisit triggers

- Week 2: match Lambda connects with an IAM token; a connection attempt with the wrong role is rejected.
- Revisit if more VPC components need database access with different privileges – each gets its own DB role and IAM policy.
