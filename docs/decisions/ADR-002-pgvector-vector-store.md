# ADR-002: pgvector on RDS as vector store

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed | 2026-10-19 | Prabhu Saravanan | PRD FR-14–16, NFR-03 · HLD §6.1, §6.5 · ADR-006 |

## Context

CV matching needs a store for up to ~5,000 posting embeddings (1,024 dimensions) plus one CV embedding, and a daily similarity query combined with filters (seniority, German level, remote policy, "not already applied"). An RDS PostgreSQL instance already exists as the CDC source for the application tracker (ADR-006).

## Decision drivers

- Monthly budget ≤ $30 – no service with a high minimum charge
- Small scale: thousands of vectors, one query batch per day
- Ability to combine similarity with relational filters and joins to the tracker
- Fits the "no NAT" network design (ADR-003)

## Options considered

| Option | Cost profile | Pros | Cons |
|---|---|---|---|
| **pgvector on the existing RDS PostgreSQL** | No extra service; RDS already paid for and stopped when idle | SQL joins with tracker data; HNSW/IVFFlat indexes; familiar Postgres | Accessible only inside the VPC; RDS must be running for matching |
| OpenSearch Serverless (vector collection) | Minimum OCU charge – well over $100/month even with redundancy disabled | Managed, scalable, hybrid search | Breaks the budget on its own |
| Amazon S3 Vectors | Pay per storage and query; no idle cost | Serverless, no VPC needed | Newer service; no SQL joins with the tracker; filtering model differs; regional availability to confirm |
| Brute force in Lambda (vectors in S3, NumPy) | Near zero | Simplest; no database | No index, no SQL; reinvents a vector store; weaker portfolio signal |
| Aurora PostgreSQL Serverless v2 + pgvector | Can auto-pause to 0 ACU; pays storage and ACU-hours | Same SQL benefits, auto-pause instead of scripted stop/start | Resume latency; Aurora storage/I-O pricing harder to predict at this scale |

## Decision

Use **pgvector on the single RDS PostgreSQL instance** that also hosts the application tracker. Use an HNSW index with cosine distance. The instance runs only during the daily window (HLD §6.4).

## Consequences

**Positive**
- Zero additional services or fixed costs.
- One SQL query can rank postings, apply filters and exclude postings already in the tracker (FR-16).
- Industry-relevant skill (pgvector, Azure Database for PostgreSQL has the same extension).

**Negative / risks**
- Matching depends on RDS being up: the pipeline must start it and wait (~5–10 min).
- The tracker and vectors share one instance; a heavy similarity query could affect CDC. At this scale the risk is negligible.
- The match Lambda must run inside the VPC (handled by ADR-003 and ADR-007).

## Validation and revisit triggers

- Week 2: top-10 query over all current postings returns in < 2 s on the smallest instance class.
- Revisit S3 Vectors if RDS is ever removed (e.g. the tracker moves elsewhere), or if vector count grows past ~1 million.
- Revisit Aurora Serverless v2 if scripted stop/start proves unreliable.
