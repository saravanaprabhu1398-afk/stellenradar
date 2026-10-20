# Stellenradar – Cost Model

| Version | Date | Author | Status |
|---|---|---|---|
| 0.1 | 2026-10-20 | Prabhu Saravanan | Draft – estimates, to be replaced by actuals in `docs/costs.md` |

Related documents: [PRD NFR-03](01-prd.md) · [System Design](02-system-design.md) · ADRs [003](decisions/ADR-003-no-nat-gateway.md), [005](decisions/ADR-005-bedrock-models.md), [009](decisions/ADR-009-dms-instance-per-run.md)

**Target:** ≤ $30 per month (NFR-03). **Result of this model:** a steady-state month costs about **$22** with the proposed extraction model. The first month can exceed the target because of the one-time backfill, and the choice of extraction model is the single biggest lever.

---

## 1. Assumptions

| ID | Assumption | Value | Confidence |
|---|---|---|---|
| CA-1 | New or changed postings per day | **100** (range 50–300, PRD A2) | Low – measure in Week 1 |
| CA-2 | Active postings at any time | 3,500 | Low – measure in Week 1 |
| CA-3 | Extraction tokens per posting | 2,500 input (≈ 900 prompt + schema, ≈ 1,600 ad text), 250 output | Medium – measure on the labelled set |
| CA-4 | Extraction retry rate (invalid output) | 5% | Medium |
| CA-5 | Embedding tokens per posting | 1,500 | Medium |
| CA-6 | RDS running hours per month | 45 (daily run ≈ 45 min + occasional manual sessions) | Medium |
| CA-7 | DMS instance hours per month | 25 (≈ 50 min per run incl. create/delete) | Medium – spike S3 |
| CA-8 | Region | eu-central-1 (extraction via EU inference profile) | – |
| CA-9 | Free tier / credits | **Ignored** – the model uses list prices so it holds after credits expire | – |

## 2. Unit prices used

Prices are list prices in USD for eu-central-1 where found. Items marked *est.* must be confirmed in the [AWS Pricing Calculator](https://calculator.aws/) before Week 1, and the table updated.

| Service | Unit | Price | Source / confidence |
|---|---|---|---|
| Bedrock – Claude Haiku 4.5 (EU profile) | 1M input / output tokens | $1.10 / $5.50 | Secondary source; EU geo profile ≈ 10% above global ($1 / $5) – *est.* |
| Bedrock – Titan Text Embeddings V2 | 1M tokens | $0.02 | AWS blog |
| RDS PostgreSQL `db.t4g.micro` single-AZ | hour | ≈ $0.019 | Secondary source (~$14/month on-demand) – *est.* |
| RDS gp3 storage | GB-month | ≈ $0.137 | *est.* |
| DMS `dms.t3.micro` single-AZ | hour | ≈ $0.02 | *est.* |
| DMS Serverless | DCU-hour | ≈ $0.11 (observed in Tokyo) | [Classmethod test](https://dev.classmethod.jp/en/articles/aws-dms-serverless-stop-billing-48hours/) – not used (ADR-009) |
| Lambda (arm64) | GB-second | ≈ $0.0000133 | AWS public pricing |
| Step Functions Standard | 1,000 state transitions | $0.025 | AWS public pricing |
| Athena | TB scanned (10 MB minimum per query) | $5 | AWS public pricing |
| S3 Standard | GB-month | ≈ $0.0245 | *est.* |
| Secrets Manager | secret-month | $0.40 | AWS public pricing |
| CloudWatch custom metric / standard alarm | month | $0.30 / $0.10 | AWS public pricing |
| SES | 1,000 emails | $0.10 | AWS public pricing |

## 3. Steady-state monthly estimate (base case: 100 postings/day)

| # | Service | Calculation | $ / month |
|---|---|---|---|
| 1 | Bedrock extraction | 3,000 postings × 1.05 retries → 7.9M input × $1.10 + 0.79M output × $5.50 | **13.00** |
| 2 | Bedrock embeddings | 3,000 × 1,500 = 4.5M tokens × $0.02 | 0.10 |
| 3 | RDS instance | 45 h × $0.019 | 0.90 |
| 4 | RDS storage | 20 GB gp3 × $0.137 (billed even when stopped) | 2.75 |
| 5 | DMS instance (per run) | 25 h × $0.02 + minimal storage | 0.60 |
| 6 | Secrets Manager | 3 secrets × $0.40 | 1.20 |
| 7 | CloudWatch | 5 custom metrics × $0.30 + 6 alarms × $0.10 + ~0.3 GB logs | 2.30 |
| 8 | S3 | < 5 GB storage + requests | 0.50 |
| 9 | Athena | ~60 queries/day × 10 MB minimum + small scans | 0.30 |
| 10 | Lambda | ~30,000 GB-s + requests | 0.40 |
| 11 | Step Functions | ~7,500 transitions | 0.20 |
| 12 | SES, SNS, EventBridge Scheduler, Glue Catalog, Lake Formation, Budgets, VPC, S3 gateway endpoint | Within free allowances or no charge | 0.10 |
| | **Total (base case)** | | **≈ 22.35** |

Bedrock extraction is **58%** of the bill; RDS storage is the largest fixed cost.

## 4. Sensitivity

### 4.1 Volume (with Haiku 4.5)

| Postings / day | Bedrock extraction | Total |
|---|---|---|
| 50 | 6.50 | ≈ 15.85 |
| **100 (base)** | **13.00** | **≈ 22.35** |
| 200 | 26.00 | ≈ 35.35 ⚠ |
| 300 | 39.00 | ≈ 48.35 ⚠ |

Above roughly 150 postings/day, Haiku 4.5 alone breaks the budget.

### 4.2 Extraction model (base volume)

| Model | Approx. price per 1M in / out | Extraction $ / month | Total | Note |
|---|---|---|---|---|
| Claude Haiku 4.5 (EU profile) | $1.10 / $5.50 | 13.00 | ≈ 22.35 | Proposed in ADR-005 |
| Claude 3 Haiku (if available in the region) | ≈ $0.25 / $1.25 | ≈ 3.00 | ≈ 12.35 | Older model; check availability and deprecation date |
| Amazon Nova Lite | ≈ $0.06 / $0.24 | ≈ 0.70 | ≈ 10.05 | Cheapest; German extraction quality unproven |

Prices for the alternatives are approximate and must be checked; the point is the order of magnitude.

**Decision rule (feeds ADR-005):** in Week 2, run the labelled set through Haiku 4.5 **and** one cheaper model. Pick the cheapest model that reaches ≥ 90% accuracy. If only Haiku 4.5 reaches it, keep it and enable the volume guard below.

### 4.3 Guards against volume spikes

- `MAX_ENRICH_PER_RUN` (default 200): postings beyond the cap stay on the work list and are enriched on the next run. A spike becomes a short delay instead of a cost overrun.
- The `BedrockInputTokens` metric has an alarm at 2× the expected daily volume.

## 5. One-time and occasional costs

| Event | Calculation (Haiku 4.5) | On-demand | With Bedrock batch inference (≈ 50% off, if supported for the model) |
|---|---|---|---|
| Initial backfill of 3,500 active postings | 9.2M input + 0.92M output | ≈ 15.20 | ≈ 7.60 |
| Prompt version change (re-enrich all current postings) | Same as backfill | ≈ 15.20 | ≈ 7.60 |
| Evaluation iterations | 50 postings × ~10 runs | ≈ 2.20 | – |
| Embedding backfill | 3,500 × 1,500 tokens | ≈ 0.10 | – |

**Month 1 estimate:** steady state ($22) + backfill + evaluation ≈ **$32–40**, which exceeds the target. Options, in order of preference:
1. Run the backfill with **batch inference** (spike S2 confirms support) → ≈ $32.
2. Backfill only postings published in the last 30 days.
3. Choose a cheaper model if it passes the accuracy gate (§4.2) → month 1 well under $20.

Prompt changes in production are batched (at most one re-enrichment per month) for the same reason.

## 6. Costs deliberately avoided

| Avoided component | Approx. monthly cost | Avoided by |
|---|---|---|
| NAT Gateway (1 AZ) | ≈ $38 + data processing | [ADR-003](decisions/ADR-003-no-nat-gateway.md) |
| Interface VPC endpoints (Bedrock, Secrets Manager, …) | ≈ $8–9 each per AZ | ADR-003, [ADR-007](decisions/ADR-007-rds-iam-auth.md) |
| Always-on RDS `db.t4g.micro` | ≈ $14 instance | Stop outside run windows |
| DMS Serverless with daily stop/start | ≈ $80+ | [ADR-009](decisions/ADR-009-dms-instance-per-run.md) |
| Always-on DMS `dms.t3.micro` | ≈ $15 | ADR-009 |
| OpenSearch Serverless | > $100 | [ADR-002](decisions/ADR-002-pgvector-vector-store.md) |
| Amazon MWAA | > $100 | [ADR-004](decisions/ADR-004-orchestration.md) |
| Customer-managed KMS keys | $1 each | AWS-managed keys |
| Second environment | ≈ 2× fixed costs | [ADR-008](decisions/ADR-008-single-environment.md) |

## 7. Budget alerts and tracking

| Control | Setting |
|---|---|
| AWS Budget (monthly, cost) | Actual > $10, > $25, > $50; forecast > $30 → email |
| Cost Anomaly Detection | Monitor for the account, alert on anomalies > $5 |
| Tags | All resources tagged `project=stellenradar`; activate `project` as a cost allocation tag |
| Tracking | `docs/costs.md` – each month: actual per service (Cost Explorer, grouped by service), comparison with this model, explanation of variances > 20% |
| Kill switch | Disable the EventBridge schedule and stop RDS (documented in the runbook) |

## 8. Validation

- **Week 1:** measure CA-1 and CA-2 from the first ingest runs, and update §3 and §4.
- **Week 2:** measure CA-3 from the labelled set (actual tokens per posting) and update the model choice using §4.2.
- **Week 3:** run Cost Explorer daily costs for 7 days × 30 and compare with §3. Success metric SM-07 is met when the projected and the first full month's actual cost are both ≤ $30.
