# ADR-003: No NAT Gateway – split internet-facing and VPC-bound Lambdas

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed | 2026-10-19 | Prabhu Saravanan | PRD NFR-03, NFR-08 · HLD §6.1 · ADR-002, ADR-007 |

## Context

RDS (tracker + pgvector) and DMS must live in a VPC. Several components need the public internet or public AWS endpoints: the BA API (ingest), Bedrock (enrich, embed), GitHub (dispatch-dbt) and SES (digest). A Lambda attached to a VPC has no internet access unless the VPC has a NAT Gateway or interface endpoints.

Approximate fixed costs in eu-central-1:
- NAT Gateway: ~$35–40/month per AZ before data processing charges
- Interface VPC endpoint: ~$8/month per endpoint per AZ (Bedrock runtime, Secrets Manager, SES, … each one separately)
- S3 gateway endpoint: free

Either NAT or more than two interface endpoints breaks the $30 budget.

## Decision drivers

- Monthly budget ≤ $30 (NFR-03)
- RDS must not be publicly accessible (NFR-07)
- Keep the design simple enough to build in 3 weeks

## Options considered

| Option | Monthly fixed cost | Notes |
|---|---|---|
| NAT Gateway | ~$35–40+ | Simplest networking; over budget alone |
| NAT instance (t4g.nano) | ~$4 + ops | Cheap but self-managed, patching, single point of failure – not "production-style" |
| Interface endpoints for every service the VPC Lambdas need | ~$8 × N | Bedrock + Secrets Manager + … quickly > $16 |
| Public RDS with security-group allow-list | $0 | Violates NFR-07; Lambda IPs are not fixed anyway |
| **Split: internet-facing Lambdas outside the VPC, only DB-facing compute inside, hand-off through S3** | $0 | Needs a staging hand-off and IAM DB auth |

## Decision

- Only the **match** Lambda and **DMS** run inside the VPC (two private subnets, no route to the internet).
- All other Lambdas (`ingest`, `enrich`, `embed`, `dispatch-dbt`, `digest`) run **outside** the VPC.
- Data crosses the boundary **through S3**: `embed` writes vectors to `staging/`, `match` reads them via the **S3 gateway endpoint** and writes scores back.
- The match Lambda authenticates to RDS with IAM tokens signed locally (ADR-007), so it needs no Secrets Manager endpoint.

## Consequences

**Positive**
- $0 networking cost; satisfies NFR-03 and NFR-08.
- RDS stays private; the attack surface inside the VPC is minimal.
- Clear separation of concerns: "talk to Bedrock" vs "talk to the database".

**Negative / risks**
- One extra hop (S3 staging) between embed and match – slightly more code and latency, negligible at this volume.
- Any future in-VPC component needing an AWS API other than S3 forces either an endpoint (cost) or a redesign. Each such case gets a new ADR.
- DMS must work with only the S3 gateway endpoint – see spike S3.
- Terraform/operator access to the DB for schema changes needs a path (e.g. a migration step run by the match Lambda, or temporary access) – detailed in the SDD.

## Validation and revisit triggers

- Week 1: `terraform plan` contains no `aws_nat_gateway` and no interface endpoints.
- Spike S3: DMS replicates to S3 with only the gateway endpoint.
- Revisit if the budget increases or a component must call AWS APIs from inside the VPC.
