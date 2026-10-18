# Stellenradar – Product Requirements Document

| Version | Date | Author | Status |
|---|---|---|---|
| 0.1 | 2026-10-18 | Prabhu Saravanan | Draft – in review |

Related documents: [README](../README.md) · System Design (`02-system-design.md`, planned) · SDD (`03-sdd.md`, planned)

---

## 1. Problem statement

German data-engineering job ads are long, unstructured and inconsistent. Answering basic questions – *which skills are in demand, how many roles are English-only, which ads mention visa sponsorship, which roles fit my profile best* – means reading hundreds of postings by hand every week. The answers go stale within days because postings change daily.

There is also no single place that connects the market view (what employers ask for) with the personal view (where I have applied and how those applications are going).

## 2. Goals

| ID | Goal |
|---|---|
| G1 | Collect German data-engineering postings daily into a lakehouse that keeps full change history. |
| G2 | Turn each posting's free text into structured, trustworthy fields using an LLM, with measured accuracy. |
| G3 | Replicate a personal application tracker into the lake via CDC and join it with market data. |
| G4 | Deliver a daily email with the 10 postings that best match my CV. |
| G5 | Run as a production-style AWS platform: fully in Terraform, least-privilege, governed, and within a fixed budget. |
| G6 | Publish an aggregated summary of the German data-engineering job market as a portfolio piece. |

## 3. Non-goals

- A public web UI. Outputs are the email digest and Athena-queryable marts.
- Scraping other job boards (LinkedIn, StepStone, Indeed, …) – terms of service.
- Automatically applying to jobs.
- Real-time or streaming ingestion. Postings change daily, so daily batch is sufficient.
- Countries other than Germany.
- Multi-user or multi-tenant support. There is one candidate profile (mine).
- Republishing raw postings. Only aggregated statistics are shared.

## 4. Users and personas

| Persona | Description | Needs |
|---|---|---|
| **P1 – Job seeker** (primary; me) | Lead data engineer targeting data-engineering roles in Germany | A short daily list of relevant roles; market facts to steer applications and upskilling; a view of how my applications are progressing |
| **P2 – Portfolio reviewer** | Hiring manager, recruiter or interviewer looking at the GitHub repo | Evidence of real AWS data-engineering skill: clear architecture, IaC, tests, cost control, documented decisions |
| **P3 – Platform operator** (me, wearing an ops hat) | Runs and maintains the pipeline | Pipeline runs without manual work; failures are visible; cost is predictable; the stack can be torn down with one command |

## 5. User stories

| ID | Persona | Story |
|---|---|---|
| US-01 | P1 | As a job seeker, I want a daily email with the top 10 matching postings so that I can apply to the best roles without searching manually. |
| US-02 | P1 | As a job seeker, I want each match to show why it matched (skills, seniority, language, remote policy) so that I can decide quickly. |
| US-03 | P1 | As a job seeker, I want to see the most demanded skills and how they trend week by week so that I can prioritise what to learn and highlight. |
| US-04 | P1 | As a job seeker, I want to know the share of English-only roles and roles mentioning visa sponsorship so that I can target employers realistically. |
| US-05 | P1 | As a job seeker, I want my application tracker joined with market data so that I can see response rates by skill set, seniority or company. |
| US-06 | P1 | As a job seeker, I want postings I have already applied to excluded from the digest so that I only see new opportunities. |
| US-07 | P2 | As a portfolio reviewer, I want an architecture diagram, ADRs, cost breakdown and evaluation results in the repo so that I can judge the engineering quality quickly. |
| US-08 | P3 | As the operator, I want failed runs to alert me by email so that I can fix them before data goes stale. |
| US-09 | P3 | As the operator, I want budget alerts and stopped-when-idle databases so that the monthly cost stays within target. |
| US-10 | P3 | As the operator, I want table- and column-level permissions so that personal data (tracker, CV) is not readable by roles that do not need it. |
| US-11 | P3 | As the operator, I want every enrichment record to carry its model ID and prompt version so that results are reproducible when I change prompts or models. |

## 6. Functional requirements

Priority: **M** = must have for v1, **S** = should have, **C** = could have.

### 6.1 Ingestion

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-01 | The system calls the Bundesagentur für Arbeit job search endpoints once per day on a schedule, for a configured list of data-engineering search terms, location Germany. | M | US-01 |
| FR-02 | The system stores every API response unchanged in the raw layer (S3, JSON), partitioned by ingest date. | M | – |
| FR-03 | The system fetches the detail record for every posting that is new or whose list entry changed since the previous run. | M | US-02 |
| FR-04 | The system deduplicates postings by the BA reference number (`refnr`) and maintains SCD Type 2 history in `curated.postings` (`valid_from`, `valid_to`, `is_current`). | M | – |
| FR-05 | Postings that disappear from the API are closed (`valid_to` set, `is_current = false`) rather than deleted. | M | US-03 |

### 6.2 LLM enrichment

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-06 | For every new or changed posting version, the system extracts the 9 enrichment fields (skills, cloud_platforms, seniority, salary_min_eur, salary_max_eur, german_level_required, english_only, visa_sponsorship_mentioned, remote_policy) with Amazon Bedrock. | M | US-02, US-03, US-04 |
| FR-07 | LLM output is validated against a JSON schema. Invalid output is retried once, then written to a dead-letter location and excluded from marts. | M | – |
| FR-08 | Every enrichment record stores `model_id`, `prompt_version` and `enriched_at`. | M | US-11 |
| FR-09 | Unchanged postings are not re-enriched (cost control). | M | US-09 |
| FR-10 | A hand-labelled set of 50 postings and a scoring script report per-field and overall accuracy for a given prompt version. | M | US-07, US-11 |

### 6.3 Application tracker (CDC)

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-11 | An application tracker exists in RDS PostgreSQL (company, role, `refnr` if known, applied date, status, notes). | M | US-05 |
| FR-12 | AWS DMS replicates inserts, updates and deletes from the tracker to S3, and they are merged into `curated.applications`. | M | US-05 |
| FR-13 | Tracker records link to postings by `refnr` where available. | S | US-05, US-06 |

### 6.4 Matching and digest

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-14 | The system generates an embedding for my CV and for every current posting, and stores them in pgvector. | M | US-01 |
| FR-15 | The system ranks current postings by a score combining vector similarity with rule-based filters (e.g. seniority, German level, remote policy). | M | US-01 |
| FR-16 | Postings already in the application tracker are excluded from the ranking. | S | US-06 |
| FR-17 | The system sends one email per day via SES with the top 10 postings: title, employer, location, score, key extracted fields and a link to the original ad. | M | US-01, US-02 |

### 6.5 Analytics marts

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-18 | `marts.skill_demand_weekly`: posting count per skill per ISO week. | M | US-03 |
| FR-19 | `marts.market_overview`: share of English-only roles, share mentioning visa sponsorship, salary bands, remote policy split. | M | US-04 |
| FR-20 | `marts.top_matches_daily`: daily ranked matches with scores. | M | US-01 |
| FR-21 | A mart joining applications with enriched postings (applications and outcomes by skill, seniority and employer). | S | US-05 |

### 6.6 Operations and governance

| ID | Requirement | Priority | Story |
|---|---|---|---|
| FR-22 | Failed pipeline runs (ingest, enrichment, dbt, digest) send an alert email. | M | US-08 |
| FR-23 | Lake Formation restricts access per table and per column. Personal data (applications, CV) is readable only by the roles that need it. | M | US-10 |
| FR-24 | RDS and DMS are stopped outside their run windows automatically. | M | US-09 |

## 7. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Freshness | Curated and mart data is no older than 24 hours after a successful daily run. |
| NFR-02 | Reliability | ≥ 95% of daily runs over any 14-day window succeed without manual intervention. Re-running a day is idempotent (no duplicates). |
| NFR-03 | Cost | Total AWS cost ≤ $30 per month. Budget alerts at $10, $25 and $50. Actuals tracked monthly. |
| NFR-04 | Region | All resources and data reside in `eu-central-1` (Frankfurt). Any exception (e.g. a Bedrock model only reachable via an EU cross-region inference profile) is documented in an ADR. |
| NFR-05 | Infrastructure as code | 100% of AWS resources are defined in Terraform. `terraform destroy` removes everything. All resources tagged `project=stellenradar`. |
| NFR-06 | Security | Least-privilege IAM role per component. No IAM users with access keys. CI/CD authenticates to AWS with GitHub OIDC. |
| NFR-07 | Security | Secrets live in Secrets Manager. S3 buckets are encrypted and block public access. RDS is not publicly accessible. |
| NFR-08 | Networking | No NAT Gateway. S3 is reached via a gateway endpoint; interface endpoints only where a VPC-bound component needs them. |
| NFR-09 | Responsible use | The BA API is called at most once per day per search term, throttled (≤ 1 request/second), with a descriptive User-Agent. Raw data stays private. |
| NFR-10 | Privacy (GDPR) | Personal data (CV, tracker notes, any contact names in postings) is never published. Only aggregated statistics leave the platform. |
| NFR-11 | Reproducibility | Prompts are versioned in Git. Every enrichment result can be traced to a model ID and prompt version. |
| NFR-12 | Quality | Code is linted and unit-tested in CI. dbt models have schema tests (unique, not null, accepted values). |
| NFR-13 | Observability | Each component logs to CloudWatch with a run ID. Per-run counts (fetched, new, changed, enriched, failed) are recorded. |

## 8. Success metrics

| ID | Metric | Target | Goal |
|---|---|---|---|
| SM-01 | Daily pipeline success rate | ≥ 95% over 14 consecutive days | G1, G5 |
| SM-02 | Data freshness | < 24 h | G1 |
| SM-03 | LLM field-level accuracy on the labelled set | ≥ 90% overall | G2 |
| SM-04 | Tracker change visible in `curated.applications` | Within one run window | G3 |
| SM-05 | Daily digest delivered with 10 ranked matches | Every day with a successful run | G4 |
| SM-06 | Digest relevance (my judgement) | ≥ 6 of 10 matches rated "would consider applying", sampled over 5 days | G4 |
| SM-07 | Monthly AWS cost | ≤ $30, documented in `docs/costs.md` | G5 |
| SM-08 | Terraform coverage | 100% of resources; clean `terraform destroy` | G5 |
| SM-09 | Market findings published | README section with top skills, English-only share, visa mentions and salary bands | G6 |

## 9. Assumptions and dependencies

**Assumptions**
- A1: The BA job search endpoints (documented by the community project [bundesAPI/jobsuche-api](https://github.com/bundesAPI/jobsuche-api)) stay available and roughly stable for the build period.
- A2: Volume is small: a few thousand active data-engineering postings and roughly 50–300 new or changed per day. To be verified in Week 1.
- A3: Most postings are in German. The LLM handles German input and returns English field values.
- A4: Salary is missing from most postings. `null` is a valid, correct answer.
- A5: One candidate (me) and one CV. Multi-user is out of scope.

**Dependencies**
- D1: AWS account with access to eu-central-1.
- D2: Bedrock model access granted for one text model (extraction) and one embedding model.
- D3: SES: a verified sender and recipient address. Sandbox mode is enough because I only email myself.
- D4: GitHub repository with Actions and an OIDC trust to AWS.

## 10. Risks and mitigations

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | The BA API is unofficial and may change or block requests. | Medium | High | Throttle and run once a day. Keep raw responses so data can be reprocessed. Isolate the API client behind one module. Alert on schema or HTTP errors. |
| R2 | LLM extraction is inaccurate or hallucinates (e.g. invents salaries). | Medium | High | JSON schema validation; instruct `null` when not stated; measure with the labelled set; version prompts; block marts on invalid output. |
| R3 | Bedrock cost or throttling grows with volume. | Low | Medium | Enrich only new or changed postings; use a small model; Step Functions concurrency limit and retries with backoff; budget alerts. |
| R4 | RDS and DMS are the most expensive parts and could break the $30 target. | High | Medium | Smallest instance classes; stop outside run windows; measure actual cost in Week 2; document a fallback (e.g. run DMS only on demand). |
| R5 | A VPC-bound component needs AWS APIs without a NAT Gateway, forcing paid interface endpoints. | Medium | Medium | Keep Lambdas out of the VPC unless they must reach RDS; add only the endpoints needed; record the decision in an ADR. |
| R6 | Personal data (CV, tracker) leaks through the public repo or marts. | Low | High | `.gitignore` for data and tfvars; CV stored only in a private bucket; Lake Formation column restrictions; publish aggregates only. |
| R7 | Scope is too large for 3 weeks. | Medium | Medium | Must/should priorities above; cut "S" and "C" items first; keep the Week 1 foundation solid. |
| R8 | Too few labelled postings or inconsistent labels make the accuracy metric unreliable. | Medium | Medium | Write a labelling guide first; label 50 postings stratified by seniority and language. |

## 11. Milestones

| Milestone | Scope | Exit criteria |
|---|---|---|
| M0 – Design (Week 0) | PRD, system design, ADRs, SDD, data model, security, cost model, test plan | Documents reviewed and merged |
| M1 – Foundation and ingestion (Week 1) | FR-01 to FR-05, NFR-03 to NFR-09 | Raw data lands daily; `curated.postings` has SCD2 history with no duplicate current rows |
| M2 – Enrichment, CDC and matching (Week 2) | FR-06 to FR-17 | SM-03 and SM-04 met; ranking query returns top 10 |
| M3 – Marts, governance and publishing (Week 3) | FR-18 to FR-24, docs | SM-01 to SM-09 met; README final |

## 12. Open questions

| ID | Question | Owner | Decide by |
|---|---|---|---|
| Q1 | Which search terms define "data engineering" (e.g. Data Engineer, Dateningenieur, Analytics Engineer, ETL Entwickler, Big Data)? | Prabhu | M1 |
| Q2 | Which Bedrock text and embedding models are available in eu-central-1, or only via an EU cross-region inference profile? | Prabhu | M0 (ADR) |
| Q3 | Does the matching Lambda run inside the VPC (to reach RDS) and, if so, which interface endpoints does it need? | Prabhu | M0 (system design) |
| Q4 | Which time window do the daily run, the DMS task and the digest use (e.g. 06:00–07:00 CET)? | Prabhu | M0 |
| Q5 | How is the application tracker filled in practice – direct SQL, a small script, or a spreadsheet import? | Prabhu | M2 |
| Q6 | How are matching weights tuned and evaluated beyond SM-06? | Prabhu | M2 |
