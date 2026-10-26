# Stellenradar – Software Design Document (SDD / LLD)

| Version | Date | Author | Status |
|---|---|---|---|
| 0.3 | 2026-10-21 | Prabhu Saravanan | Draft – in review (BA API v6 per spike S1; CDC per ADR-009; metrics trimmed for cost) |

Related documents: [PRD](01-prd.md) · [System Design](02-system-design.md) · [ADRs](decisions/README.md) · Data model (`04-data-model.md`, planned) · Security (`05-security.md`, planned)

The system design says **what** the components are and **why**. This document says **how each one is built**: contracts, logic, SQL, configuration, IAM, error handling and infrastructure layout. It is the reference for implementation in Weeks 1–3.

Items marked **⚠ Spike** depend on a Week 1 spike (HLD §10) and must be confirmed before implementation.

---

## 1. Conventions

| Topic | Convention |
|---|---|
| Language / runtime | Python 3.12 on Lambda, **arm64** (cheaper per GB-second) |
| Resource names | `stellenradar-<env>-<component>`, e.g. `stellenradar-dev-ingest` |
| Tags (provider `default_tags`) | `project=stellenradar`, `env=dev`, `managed_by=terraform`, `component=<name>` |
| Identifiers | `run_date` = business date in Europe/Berlin (`YYYY-MM-DD`). `run_id` = Step Functions execution name, `<run_date>T<HHMMSS>Z-<4 hex>` – unique per execution, so re-runs of a date get a new `run_id` |
| Timestamps | Stored as UTC `timestamp(6)`; converted to Europe/Berlin only for display |
| Logging | One JSON object per line from a small stdlib formatter in `src/common/logging.py` (no Powertools layer to pin and update); every log line has `component`, and pipeline logs add `run_id` |
| Metrics | CloudWatch Embedded Metric Format, namespace `Stellenradar` |
| Code style | `ruff` (lint + format), type hints, `mypy` on shared code |
| SQL style | Lower-case keywords in dbt; upper-case in raw Athena scripts; one statement per file |

## 2. Repository layout

```
stellenradar/
├── infra/
│   ├── bootstrap/                 # one-time: Terraform state bucket (local state)
│   ├── modules/
│   │   ├── network/               # VPC, subnets, S3 gateway endpoint, SGs
│   │   ├── storage/               # data, config, athena-results buckets + lifecycle
│   │   ├── catalog/               # Glue databases, Athena workgroup, Lake Formation
│   │   ├── lambda_function/       # generic: function + role + log group + alarms
│   │   ├── rds/                   # PostgreSQL, parameter group, subnet group
│   │   ├── dms/                   # DMS subnet group, endpoints, IAM roles (instance is per run)
│   │   ├── orchestration/         # Step Functions, EventBridge Scheduler
│   │   ├── notifications/         # SES identity, SNS alert topic
│   │   ├── monitoring/            # Budgets, CloudWatch alarms, dashboards
│   │   └── github_oidc/           # OIDC provider + gha-terraform / gha-dbt roles
│   └── envs/dev/                  # main.tf, variables.tf, backend.tf, dev.auto.tfvars (git-ignored)
├── src/
│   ├── common/                    # shared: config, logging, s3 helpers, athena client
│   ├── ingest/                    # handler.py, ba_client.py, search_terms.json
│   ├── curate/                    # handler.py, athena.py, sql/ (DDL and SCD2 statements)
│   ├── enrich/                    # handler.py, schema.json, validate.py, skills_taxonomy.yaml
│   │   └── prompts/v1/            # system.md, user.md
│   ├── embed/                     # handler.py
│   ├── match/                     # handler.py, db.py
│   ├── db_migrate/                # handler.py + migrations/*.sql
│   ├── dispatch_dbt/              # handler.py
│   └── digest/                    # handler.py, templates/digest.html.j2
├── dbt/                           # dbt-athena project
├── eval/                          # labelled_set.jsonl, labelling_guide.md, score.py
├── tests/                         # unit/ and integration/
├── docs/
├── Makefile                       # build, test, plan, db-up, db-down, invoke-*
└── .github/workflows/             # ci.yml, terraform.yml, dbt-daily.yml
```

> The README's planned layout had top-level `ingest/`, `enrich/`, `matching/` and `cdc/` folders. This SDD moves Lambda code under `src/` so shared code and packaging are simpler. The empty top-level folders are removed in the first implementation PR and the README is updated.

## 3. Configuration

| Setting | Where it lives | Example |
|---|---|---|
| Search terms | `src/ingest/search_terms.json` (in Git) | `["Data Engineer", "Analytics Engineer", "Big Data Engineer", "ETL Entwickler"]` (spike S1) |
| Model IDs | Terraform variables → Lambda environment | `EXTRACT_MODEL_ID = eu.amazon.nova-lite-v1:0`, `EMBED_MODEL_ID = amazon.titan-embed-text-v2:0` |
| Prompt version | Folder name under `prompts/` + env var `PROMPT_VERSION` | `v1` |
| Enrichment cap | Env var `MAX_ENRICH_PER_RUN` – postings above the cap stay on the work list for the next run ([cost model §4.3](06-cost-model.md)) | `200` |
| Bucket names, table names | Terraform outputs → Lambda environment | `DATA_BUCKET` |
| Candidate preferences | dbt `vars` in `dbt_project.yml` | `target_seniority: [senior, lead]`, `my_german_level: basic` |
| Email addresses | `dev.auto.tfvars` (git-ignored) → SES identity + env var | `DIGEST_TO` |
| Secrets | Secrets Manager only (RDS master password, DMS user password, GitHub token) | – |

No secret is ever stored in a Lambda environment variable, Step Functions input or Terraform variable file in Git.

---

## 4. Components

### 4.1 Ingest Lambda (`stellenradar-dev-ingest`)

| Property | Value |
|---|---|
| Trigger | Step Functions task, payload `{run_id, run_date}` |
| Network | Outside VPC |
| Memory / timeout | 512 MB / 900 s |
| Output | Raw files in S3 + manifest; returns manifest summary to Step Functions |
| Requirements | FR-01, FR-02, FR-03, NFR-09 |

**BA API** (community-documented, [bundesAPI/jobsuche-api](https://github.com/bundesAPI/jobsuche-api); verified in [spike S1](spikes/S1-ba-api.md)):

| Call | Endpoint | Notes |
|---|---|---|
| Search | `GET https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?was=<term>&angebotsart=1&size=100&page=<n>` | Header `X-API-Key: jobboerse-jobsuche`. `angebotsart=1` = employment. Postings in `ergebnisliste`, total in `maxErgebnisse`; key field `referenznummer`. (v4 search returns HTTP 403) |
| Detail | `GET …/pc/v4/jobdetails/<base64(referenznummer)>` | Full ad text in `stellenangebotsBeschreibung` (2–5k characters in the spike) |

**Logic**

```text
load search_terms.json
load previous list index  s3://…/raw/state/list_index.json.gz   # {refnr: list_hash}
for term in search_terms:
    for page in 1..N until empty or page cap (50):
        GET search page (throttle: ≥ 1.1 s between the *starts* of any two requests)
        write raw/postings/ingest_date=<run_date>/run_id=<run_id>/search_<term>_<page>.json.gz
        for each result: list_hash = sha256(canonical JSON of list fields, incl. aenderungsdatum)
collect today's referenznummer set (union across terms, dedup)
to_fetch = references that are new or whose list_hash changed, capped at MAX_DETAILS (default 500 ≈ 10 min)
for refnr in to_fetch:
    GET detail (throttled), write raw/.../details_<batch>.json.gz (100 records per file)
write new list index (only for refnrs fetched successfully, so skipped ones retry tomorrow)
write manifest raw/manifests/run_id=<run_id>.json
```

**Manifest** (also the Lambda return value):

```json
{
  "run_id": "2026-10-21T040000Z-a1b2",
  "run_date": "2026-10-21",
  "complete": true,
  "terms": 4,
  "search_pages": 14,
  "postings_listed": 1020,
  "details_requested": 212,
  "details_fetched": 209,
  "details_failed": 3,
  "details_deferred": 0,
  "raw_prefix": "raw/postings/ingest_date=2026-10-21/run_id=2026-10-21T040000Z-a1b2/"
}
```

`complete = true` only when **every** search term was paged to the end without error. The curate step closes disappeared postings only when `complete` is true (§4.2), so a partial API outage never wrongly closes postings.

**Errors**

| Case | Handling |
|---|---|
| HTTP 429 / 5xx | Retry with exponential backoff (1, 2, 4, 8 s; max 4 attempts) |
| A search term fails after retries | Continue other terms; `complete = false`; metric `SearchTermFailed` |
| A detail fails after retries | Skip it (fetched tomorrow); count in `details_failed` |
| Time budget | Stop fetching details when < 60 s remain; remainder counted as `details_deferred` |
| > 50% of terms fail | Raise `IngestFailed` → Step Functions fails the run and alerts |

User-Agent: `stellenradar/0.1 (+https://github.com/saravanaprabhu1398-afk/stellenradar)`.

**Timing (from spike S1):** a call takes 0.8–1.1 s, so with the start-to-start throttle one request costs ≈ 1.1 s. A normal run (≈ 14 search pages + < 100 details) takes ~2 minutes; the initial backfill of ~1,000 details is spread over 2–3 runs by `MAX_DETAILS`.

**Normalisation to pipeline names** happens in the staging view (§4.2), not in the Lambda – raw files keep the BA's German field names exactly as returned.

### 4.2 Curate – SCD2 in Athena (`src/curate/sql/curate/*.sql`)

Run by Step Functions with the Athena `startQueryExecution.sync` integration in workgroup `stellenradar-dev`. Statements are templated with `${run_id}`, `${run_date}` and `${ingested_at}`.

**Raw table** (Terraform, `infra/modules/catalog/raw_tables.tf`): `stellenradar_raw.postings_lines`, one gzip JSON Lines document per row as a single `line` string, partition projection on `ingest_date` (date) and `run_id` (injected – queries must name it), no crawler. Search and detail lines share the prefix and are told apart with the `"$path"` pseudo-column. Parsing with `json_extract` in SQL keeps the table stable when the BA adds or drops fields.

**Statement files** live in `src/curate/sql/` (one statement per file, `{placeholders}` filled by `src/curate/athena.py`): `ddl/` creates the Iceberg tables, `curate/_details.sql` and `_listed.sql` are the normalising subqueries for one run (they replace the "staging view", which could not be parameterised by run), and `curate/0x0_*.sql` are the SCD2 steps below. The curate Lambda runs them in the daily pipeline; `scripts/athena_sql.py` runs the same code for setup, manual runs and spike S4.

**Staging view** `staging.v_details_today`: flattens today's detail records for the run, normalises whitespace, and computes

```sql
content_hash = to_hex(sha256(to_utf8(concat_ws('|',
    coalesce(title,''), coalesce(employer,''), coalesce(location,''),
    coalesce(description,''), coalesce(salary_text,''), coalesce(employment_type,'')))))
```

**`curated.postings` (Iceberg)** – key columns (full definition in the data model doc):

```sql
CREATE TABLE curated.postings (
  refnr          string,
  title          string,
  employer       string,
  location       string,
  description    string,
  salary_text    string,
  employment_type string,
  homeoffice_possible boolean,
  is_temp_agency boolean,
  published_at   date,
  source_url     string,
  content_hash   string,
  raw_s3_key     string,
  valid_from     timestamp(6),
  valid_to       timestamp(6),
  is_current     boolean,
  run_id         string
)
PARTITIONED BY (bucket(16, refnr))
LOCATION 's3://<data-bucket>/curated/postings/'
TBLPROPERTIES ('table_type'='ICEBERG', 'format'='parquet', 'write_compression'='zstd');
```

**SCD2 statements** – three statements, each idempotent (⚠ **Spike S4** confirms Athena accepts them as written):

```sql
-- 01_close_changed.sql : close the current version when content changed
MERGE INTO curated.postings t
USING staging.v_details_today s
  ON t.refnr = s.refnr AND t.is_current = true
WHEN MATCHED AND t.content_hash <> s.content_hash THEN
  UPDATE SET valid_to = TIMESTAMP '${ingested_at}', is_current = false;

-- 02_close_disappeared.sql : only executed when manifest.complete = true
UPDATE curated.postings
SET valid_to = TIMESTAMP '${ingested_at}', is_current = false
WHERE is_current = true
  AND refnr NOT IN (SELECT refnr FROM raw.search_results
                    WHERE ingest_date = '${run_date}' AND run_id = '${run_id}');

-- 03_insert_new_versions.sql : new postings and new versions of changed ones
INSERT INTO curated.postings
SELECT s.refnr, s.title, s.employer, s.location, s.description, s.salary_text,
       s.employment_type, s.homeoffice_possible, s.is_temp_agency, s.published_at, s.source_url, s.content_hash, s.raw_s3_key,
       TIMESTAMP '${ingested_at}' AS valid_from, CAST(NULL AS timestamp(6)) AS valid_to,
       true AS is_current, '${run_id}' AS run_id
FROM staging.v_details_today s
WHERE NOT EXISTS (SELECT 1 FROM curated.postings t
                  WHERE t.refnr = s.refnr AND t.is_current = true);
```

Why it is idempotent: after a successful run, every fetched posting has a current row with the same hash, so re-running 01 and 03 changes nothing; 02 only touches rows that are still current and absent.

**Work list for enrichment** (`04_select_to_enrich.sql`) – everything current that has no enrichment for the active prompt version. Because it is computed from table state, failed or skipped postings are retried automatically next run:

```sql
SELECT p.refnr, p.content_hash, p.raw_s3_key
FROM curated.postings p
LEFT JOIN curated.posting_enrichment e
  ON e.refnr = p.refnr AND e.content_hash = p.content_hash
 AND e.prompt_version = '${prompt_version}'
WHERE p.is_current AND e.refnr IS NULL;
```

This is a plain `SELECT`, not `UNLOAD`: Athena writes the result as **one** CSV file with a header row (`<QueryExecutionId>.csv` in the results bucket), which is exactly what the Distributed Map `ItemReader` needs. `UNLOAD` may split output into several header-less files. None of the selected columns can contain commas or quotes.

Note: bumping `PROMPT_VERSION` puts **all** current postings back on the work list (~1,000 Bedrock calls, spike S1). That is intended – it is how a new prompt is evaluated on real data – but it must be budgeted in the cost model.

A similar `05_unload_current.sql` writes the current `(refnr, content_hash)` list to `staging/current/run_id=…/` for the match step.

**Maintenance** (weekly, Sunday run only): `OPTIMIZE curated.postings REWRITE DATA USING BIN_PACK` and `VACUUM curated.postings` for each Iceberg table; table property `vacuum_max_snapshot_age_seconds = 604800` (7 days).

### 4.3 Enrich Lambda (`stellenradar-dev-enrich`)

| Property | Value |
|---|---|
| Trigger | Step Functions **Distributed Map**: `ItemReader` reads the Athena result CSV of `04_select_to_enrich.sql` (`CSVHeaderLocation: FIRST_ROW`); `ItemBatcher` 10 items; `MaxConcurrency` 4; `ToleratedFailurePercentage` 10 |
| Network | Outside VPC |
| Memory / timeout | 256 MB / 120 s |
| Requirements | FR-06, FR-07, FR-08, FR-09 |

**Logic per batch**

```text
for item in batch:
    detail = read JSON at item.raw_s3_key, select record by refnr
    text = title + employer + location + description (truncated to 12,000 chars)
    response = bedrock.converse(
        modelId=EXTRACT_MODEL_ID,
        system=[prompts/v1/system.md],
        messages=[user.md rendered with text],
        toolConfig={tools:[record_posting_fields(schema.json)], toolChoice:{tool:{name:"record_posting_fields"}}},
        inferenceConfig={temperature:0, maxTokens:800})
    fields = tool_use.input
    errors = validate(fields)        # JSON schema + business rules
    if errors: retry once with the errors appended to the prompt
    if still invalid: write to staging/dead_letter/…; continue
    normalise skills with skills_taxonomy.yaml
    append row to staging/enrichment/run_id=…/batch_<map-index>.jsonl
return {valid: n, dead_lettered: m, input_tokens, output_tokens}
```

Forcing a **tool call** with a JSON schema gives structured output more reliably than asking for raw JSON in text.

**Enrichment schema** (`schema.json`, abbreviated):

| Field | Type | Rule |
|---|---|---|
| `skills` | array of string | lower-case, normalised via taxonomy (e.g. "PySpark" → `spark`), max 25 |
| `cloud_platforms` | array of enum | `aws`, `azure`, `gcp` |
| `seniority` | enum or null | `junior`, `mid`, `senior`, `lead`, `principal` |
| `salary_min_eur`, `salary_max_eur` | integer or null | yearly EUR; 10,000–400,000; min ≤ max; **null unless stated in the ad** |
| `german_level_required` | enum | `none`, `basic`, `fluent` |
| `english_only` | boolean | true only if the ad states English is sufficient / German not required |
| `visa_sponsorship_mentioned` | boolean | true only if the ad mentions visa/relocation support |
| `remote_policy` | enum or null | `onsite`, `hybrid`, `remote` |

**Prompt design (`prompts/v1/system.md`) – rules the prompt must state**
1. Extract only what the ad states; when not stated, return `null` (never guess salaries).
2. Monthly salaries are converted to yearly (× 12); hourly rates → `null`.
3. Ads may be in German or English; output values are always the English enums above.
4. Skills are concrete technologies or methods, not soft skills.

Any change to prompt wording creates a new folder (`v2/`), never an in-place edit, so `prompt_version` always identifies the exact text.

**Bedrock throttling:** boto3 client with `retries={"mode": "adaptive", "max_attempts": 8}`; Step Functions additionally retries the batch on `Lambda.TooManyRequestsException` and `States.Timeout`.

**Load into Iceberg** (`06_merge_enrichment.sql`): external table `staging.enrichment` over the JSONL, then `MERGE INTO curated.posting_enrichment` on `(refnr, content_hash, prompt_version)` – insert when not matched, update when matched (re-runs overwrite with the latest result).

### 4.4 Embed Lambda (`stellenradar-dev-embed`)

| Property | Value |
|---|---|
| Trigger | Step Functions task after enrichment |
| Network | Outside VPC |
| Memory / timeout | 512 MB / 600 s |
| Requirements | FR-14 |

**Logic**
1. Read the Athena result CSV passed in the payload – current postings whose `(refnr, content_hash)` is not in `curated.embedding_log` for `EMBED_MODEL_ID`.
2. Embedding text: `title + " | " + employer + " | " + location + " | " + first 6,000 chars of description`.
3. Call Titan Text Embeddings V2 with `dimensions=1024, normalize=true`; up to 8 concurrent calls via a thread pool.
4. Write `staging/embeddings/run_id=…/part_<n>.jsonl` with `{refnr, content_hash, model_id, vector}`.
5. CV: read `config/cv/cv.md`; if its S3 ETag differs from `staging/embeddings/cv/latest.json`, embed it and write a new `cv/latest.json` (vector, etag, model_id). The CV text itself is never written anywhere else.
6. Step Functions then appends the processed keys to `curated.embedding_log` (Athena `INSERT`).

### 4.5 Match Lambda (`stellenradar-dev-match`) – inside VPC

| Property | Value |
|---|---|
| Trigger | Step Functions task, after `WaitForRdsAvailable` and `DbMigrate` |
| Network | VPC private subnets, SG `sg-match` |
| Memory / timeout | 512 MB / 300 s |
| DB access | IAM auth as `match_app` (ADR-007), driver `pg8000` (pure Python), TLS `verify-full` |
| Requirements | FR-14, FR-15 |

**Database objects** (created by migrations, §4.6):

```sql
CREATE TABLE vec.posting_vectors (
  refnr        text PRIMARY KEY,
  content_hash text NOT NULL,
  model_id     text NOT NULL,
  embedding    vector(1024) NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX posting_vectors_hnsw ON vec.posting_vectors
  USING hnsw (embedding vector_cosine_ops);

CREATE TABLE vec.cv_vector (
  id         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  model_id   text NOT NULL,
  embedding  vector(1024) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

**Logic**
1. Upsert all rows from `staging/embeddings/run_id=…/` (`INSERT … ON CONFLICT (refnr) DO UPDATE`), in batches of 200.
2. Upsert the CV vector if `cv/latest.json` is newer than `vec.cv_vector.updated_at`.
3. Delete vectors for postings no longer current: load `staging/current/run_id=…/` into a temp table, `DELETE … WHERE refnr NOT IN (temp)`.
4. Score: `SELECT refnr, 1 - (embedding <=> cv.embedding) AS similarity FROM vec.posting_vectors, vec.cv_vector cv ORDER BY embedding <=> cv.embedding LIMIT 300`.
5. Write `staging/match_scores/run_id=…/scores.jsonl` with `{run_date, refnr, similarity, model_id}`.

Hard filters and the final ranking happen in dbt (§4.8), where enrichment fields and the tracker are available. The Lambda only produces similarity.

### 4.6 Database migrations (`stellenradar-dev-db-migrate`) – inside VPC

**One-time bootstrap** (run once after the first `terraform apply`):
- The operator invokes the function with `{"mode": "bootstrap"}` and the master password (read from Secrets Manager on the operator's machine) in the payload. The handler never logs the event. This is the only time a password is passed to application code – a documented trade-off to avoid a Secrets Manager endpoint (ADR-003).
- Bootstrap SQL: `CREATE EXTENSION vector;` create roles `migrator` (owns schemas, `rds_iam`), `match_app` (`rds_iam`), `dms_user` (`rds_replication`, password = value from Secrets Manager); create schemas `tracker` and `vec`; create the persistent logical replication slot: `SELECT pg_create_logical_replication_slot('stellenradar_dms', 'test_decoding');`

**Normal mode** (every run, as a Step Functions step): connect as `migrator` with IAM auth, apply `src/db_migrate/migrations/NNN_*.sql` not yet recorded in `public.schema_migrations`, each in a transaction.

**Tracker schema** (`001_tracker.sql`):

```sql
CREATE TABLE tracker.applications (
  application_id  bigserial PRIMARY KEY,
  refnr           text,                       -- BA reference number when known
  company         text NOT NULL,
  role_title      text NOT NULL,
  applied_date    date NOT NULL,
  status          text NOT NULL CHECK (status IN
                   ('applied','screening','interview','offer','rejected','withdrawn','ghosted')),
  source          text,                       -- e.g. 'stellenradar-digest', 'linkedin', 'referral'
  notes           text,                       -- personal; column-restricted in Lake Formation
  updated_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tracker.applications REPLICA IDENTITY FULL;  -- full before-images for DMS updates/deletes
```

Filling the tracker (PRD Q5): `make db-up`, then `make tracker-add` runs a small CLI that invokes the db-migrate Lambda in `{"mode": "tracker_upsert"}` with the record – no direct network path to the database is needed.

### 4.7 CDC – DMS instance per run (`infra/modules/dms`, [ADR-009](decisions/ADR-009-dms-instance-per-run.md))

| Setting | Value |
|---|---|
| Source endpoint (Terraform) | PostgreSQL, user `dms_user`, SSL `require`, credentials via Secrets Manager ARN, `slotName=stellenradar_dms` (⚠ **Spike S3**) |
| Logical decoding | DMS default plugin (`test_decoding`); `pglogical` not required. Needs RDS parameter `rds.logical_replication = 1` |
| Target endpoint (Terraform) | S3, `s3://<data-bucket>/cdc/`, `DataFormat=parquet`, `TimestampColumnName=commit_ts`, `IncludeOpForFullLoad=true`, `DatePartitionEnabled=true` (inserts, updates and deletes are all written, with the `Op` column = `I`/`U`/`D`) |
| Replication instance (Step Functions) | `dms.t3.micro`, single-AZ, smallest allowed storage, replication subnet group in the private subnets, `sg-dms`; created each run, deleted after the merge |
| Replication task (Step Functions) | First run: `full-load-and-cdc`. Later runs: `cdc` with `CdcStartPosition` = `RecoveryCheckpoint` read from `s3://…/cdc/_state/checkpoint.json`. Table mapping `tracker.applications` only |
| Lifecycle | Create instance → wait `available` → create task → start → wait CDC latency ≈ 0 → stop → save checkpoint → delete task → delete instance |

**Merge into `curated.applications`** (`07_merge_applications.sql`) – latest change per key wins; deletes become soft deletes so history is kept:

```sql
MERGE INTO curated.applications t
USING (
  SELECT * FROM (
    SELECT c.*, row_number() OVER (PARTITION BY application_id ORDER BY commit_ts DESC) AS rn
    FROM staging.cdc_applications c
    WHERE commit_ts > (SELECT coalesce(max(source_commit_ts), TIMESTAMP '1970-01-01')
                       FROM curated.applications)
  ) WHERE rn = 1
) s
ON t.application_id = s.application_id
WHEN MATCHED AND s.op = 'D' THEN UPDATE SET is_deleted = true, source_commit_ts = s.commit_ts
WHEN MATCHED THEN UPDATE SET refnr = s.refnr, company = s.company, role_title = s.role_title,
     applied_date = s.applied_date, status = s.status, source = s.source, notes = s.notes,
     is_deleted = false, source_commit_ts = s.commit_ts
WHEN NOT MATCHED AND s.op <> 'D' THEN INSERT (application_id, refnr, company, role_title,
     applied_date, status, source, notes, is_deleted, source_commit_ts)
     VALUES (s.application_id, s.refnr, s.company, s.role_title, s.applied_date, s.status,
             s.source, s.notes, false, s.commit_ts);
```

Full-load rows have `op = 'I'` (`IncludeOpForFullLoad`), so the first run seeds the table with the same statement.

### 4.8 dbt project (`dbt/`)

| Setting | Value |
|---|---|
| Adapter | `dbt-athena-community` (version pinned in `requirements-dbt.txt`) |
| Targets | `prod` → schemas `marts`; `ci` → schema `dev_ci_<pr_number>` (dropped after merge) |
| Default materialisation | `table` with `table_type='iceberg'`; large facts `incremental` with `incremental_strategy='merge'` |

**Models**

| Layer | Model | Purpose |
|---|---|---|
| staging | `stg_postings_current` | `curated.postings` where `is_current` |
| staging | `stg_enrichment` | latest `prompt_version` per `(refnr, content_hash)` |
| staging | `stg_applications` | `curated.applications` where not deleted (no `notes`) |
| staging | `stg_match_scores` | today's scores |
| intermediate | `int_postings_enriched` | postings ⨝ enrichment; skills exploded in `int_posting_skills` |
| marts | `skill_demand_weekly` | ISO week × skill → posting count, share of postings, week-over-week change (incremental) |
| marts | `market_overview` | per week: English-only share, visa-mention share, salary bands (p25/p50/p75 where stated), remote split |
| marts | `top_matches_daily` | today's top 10 (below) |
| marts | `application_funnel` | applications by status × seniority × top skills (FR-21) |

**Ranking in `top_matches_daily`**

```text
hard filters:  not already in stg_applications (by refnr)
               german_level_required ≤ var('my_german_level')   (none < basic < fluent)
               seniority in var('target_seniority') or seniority is null
rule_score  =  0.5 * (remote_policy in var('preferred_remote'))
             + 0.3 * (cloud_platforms contains 'aws' or 'azure')
             + 0.2 * (salary_max_eur is null or salary_max_eur >= var('min_salary'))
final_score =  0.75 * similarity + 0.25 * rule_score
top 10 by final_score, ties broken by published_at desc
```

Weights are dbt vars so they can be tuned without code changes (PRD Q6); SM-06 measures the result.

**Tests**: `unique` + `not_null` on keys; `accepted_values` on enums; `dbt_utils.expression_is_true` for `salary_min_eur <= salary_max_eur`; a custom test that `top_matches_daily` has 1–10 rows for today; source freshness on `curated.postings` (`warn_after: 26h`, `error_after: 48h`).

### 4.9 Dispatch, GitHub workflow and digest

**`dispatch-dbt` Lambda** (outside VPC, 128 MB / 30 s): reads the GitHub fine-grained token (repo-scoped, `contents: read` + `actions: write`) from Secrets Manager and calls

```
POST https://api.github.com/repos/saravanaprabhu1398-afk/stellenradar/dispatches
{"event_type": "daily-pipeline-complete", "client_payload": {"run_id": "...", "run_date": "..."}}
```

**`.github/workflows/dbt-daily.yml`**

```text
on:  repository_dispatch [daily-pipeline-complete] | schedule (fallback, 07:30 UTC) | workflow_dispatch
permissions: id-token: write, contents: read
steps:
  1. fallback guard: if triggered by schedule and s3://…/marts/_runs/run_date=<today>/dbt_done exists → exit 0
  2. checkout; setup-python 3.12; pip install -r requirements-dbt.txt
  3. aws-actions/configure-aws-credentials (role gha-dbt, region eu-central-1)
  4. dbt deps && dbt source freshness && dbt build --target prod
  5. write dbt_done marker
  6. aws lambda invoke stellenradar-dev-digest --payload {run_id, run_date}
```

**`digest` Lambda** (outside VPC, 256 MB / 120 s):
1. Exit if `s3://…/digest/sent/run_date=<date>` exists (prevents duplicate emails on re-runs).
2. Athena query `SELECT * FROM marts.top_matches_daily WHERE run_date = DATE '<date>' ORDER BY final_score DESC`.
3. Render `templates/digest.html.j2` (title, employer, location, score, top skills, seniority, German level, remote, salary if stated, link to the BA ad) plus a plain-text alternative.
4. `ses:SendEmail` from the verified sender to `DIGEST_TO`; write the `sent` marker.
5. If the query returns 0 rows, send a short "no new matches today" email instead – a missing email always means a failure.

### 4.10 State machine (`statemachine/daily.asl.json`)

> **Implementation status (2026-10-26):** the first slice is live in `infra/modules/orchestration`: **Ingest → Curate → PublishSuccess**, with Retry/Catch to an SNS alert, an EventBridge rule for timeouts/aborts, the 26-hour no-success alarm and the 06:00 Europe/Berlin schedule. Deviation from the table below: the SCD2 statements run in a small **curate Lambda** (`src/curate/`, the code that passed spike S4) instead of Step Functions' native Athena integration – embedding SQL with quotes and `"$path"` in `States.Format` strings would be fragile and would duplicate tested code. The remaining states (RDS/DMS, enrichment, embeddings, matching, dbt dispatch) are added in Weeks 2–3. The definition is generated by Terraform (`jsonencode`), not kept as a separate ASL file.

| # | State | Type / integration | Notes |
|---|---|---|---|
| 1 | `InitRun` | Pass | Builds `run_id`, `run_date`, `ingested_at` from `$$.Execution` |
| 2 | `StartInfraAndIngest` | Parallel | Branch A: `rds:startDBInstance` (ignore `InvalidDBInstanceState` if already running), then `dms:createReplicationInstance`. Branch B: `Ingest` (Lambda) |
| 3 | `Curate` | Athena `.sync` × 3 | 02 runs only if `$.manifest.complete == true` (Choice) |
| 4 | `SelectToEnrich` | Athena `.sync` | Result CSV location passed to the Map |
| 5 | `Enrich` | Distributed Map → Lambda | Skipped by a Choice if the work list is empty |
| 6 | `MergeEnrichment` | Athena `.sync` | |
| 7 | `SelectToEmbed` → `Embed` → `LogEmbeddings` | Athena, Lambda, Athena | |
| 8 | `WaitForRds` | Wait 30 s + `rds:describeDBInstances` loop | Timeout 20 min |
| 9 | `CreateDmsInstance` | `dms:createReplicationInstance` + describe loop | Started in branch A of step 2 so it is ready by now; waited for here |
| 10 | `DbMigrate` → `UnloadCurrent` → `Match` → `LoadScores` | Lambda, Athena, Lambda, Athena | |
| 11 | `RunCdcTask` | `dms:createReplicationTask`, `startReplicationTask`, describe loop until CDC latency ≈ 0, `stopReplicationTask`, save checkpoint (S3 `putObject` SDK integration) | Timeout 25 min |
| 12 | `MergeApplications` | Athena `.sync` | |
| 13 | `Cleanup` | `dms:deleteReplicationTask`, `dms:deleteReplicationInstance`, then `rds:stopDBInstance` | Skips RDS stop if tag `keep_running=true` (manual session) |
| 14 | `Maintenance` | Choice: Sunday → `OPTIMIZE` / `VACUUM` | |
| 15 | `DispatchDbt` | Lambda | |
| 16 | `PublishMetrics` | Lambda-free: `cloudwatch:putMetricData` SDK integration | `PipelineSucceeded = 1` |

**Error policy (applies to every Task):**

| Error | Retry |
|---|---|
| `Lambda.ServiceException`, `Lambda.TooManyRequestsException`, `Lambda.SdkClientException` | 3 attempts, 5 s, backoff × 2 |
| `Athena.AmazonAthenaException`, `States.Timeout` on Athena tasks | 2 attempts, 30 s, backoff × 2 |
| `States.ALL` (after retries) | `Catch` → `CleanupOnFailure` (delete DMS task and instance if they exist, stop RDS, `sns:publish` with run_id + failed state + error) → `Fail` |

Execution timeout: 2 hours. Execution history retained 90 days.

### 4.11 Monitoring and alerting

| Signal | Source | Alarm / action |
|---|---|---|
| Execution failed | EventBridge rule on Step Functions `FAILED`, `TIMED_OUT`, `ABORTED` | SNS email |
| No success in 26 h | CloudWatch alarm on `Stellenradar/PipelineSucceeded` (sum < 1 over 26 h, missing data = breaching) | SNS email |
| dbt failed | GitHub Actions failure notification | Email from GitHub |
| Enrichment dead letters > 10% | Metric `EnrichDeadLetterRate` | SNS email |
| DMS slot lag | `OldestReplicationSlotLag` > 1 GB while running | SNS email |
| Cost | AWS Budgets $10 / $25 / $50 (actual) + forecast > $30 | Email |
| RDS left running | EventBridge Scheduler at 22:00 Europe/Berlin: `rds:stopDBInstance` (universal target) | – |

**Per-run metrics.** Each CloudWatch custom metric costs about $0.30/month, so only five are published as metrics (no extra dimensions): `PipelineSucceeded`, `PostingsNew`, `EnrichDeadLetterRate`, `BedrockInputTokens`, `BedrockOutputTokens`. All other counts (listed, changed, closed, embedded, matched, digest sent) are written to the run manifest and structured logs, where they can be queried with Logs Insights at no standing cost.

---

## 5. Infrastructure

### 5.1 Terraform

| Topic | Design |
|---|---|
| Version | Terraform ≥ 1.10, AWS provider ~> 6.x (pinned in `.terraform.lock.hcl`) |
| State | `infra/bootstrap` creates `stellenradar-tfstate-<account>` (versioned, encrypted, public access blocked). `envs/dev/backend.tf` uses `backend "s3" { use_lockfile = true }` |
| Modules | One per concern (§2). `envs/dev/main.tf` only wires modules together |
| Lambda packaging | Terraform builds each function zip itself (`archive_file` over the needed `src/` packages, deterministic timestamps), so CI needs no build step. Functions with third-party dependencies (`pg8000`, `jsonschema`, `jinja2`) get a shared layer built with `pip --platform manylinux2014_aarch64 --only-binary=:all:` when they are added |
| Validation | `terraform fmt -check`, `terraform validate`, `tflint`, `checkov` (soft-fail initially) in CI |
| Destroy | `terraform destroy` removes everything; buckets have `force_destroy = true` in dev only |

### 5.2 Network

| Resource | Value |
|---|---|
| VPC | `10.20.0.0/16`, DNS hostnames on |
| Private subnets | `10.20.1.0/24` (eu-central-1a), `10.20.2.0/24` (eu-central-1b); no internet gateway route |
| Endpoints | S3 **gateway** endpoint on the private route table – nothing else |
| `sg-rds` | Ingress 5432 from `sg-match`, `sg-migrate`, `sg-dms` |
| `sg-match`, `sg-migrate` | Egress 5432 → `sg-rds`; egress 443 → S3 prefix list |
| `sg-dms` | Egress 5432 → `sg-rds`; egress 443 → S3 prefix list |

### 5.3 RDS

| Setting | Value |
|---|---|
| Engine | PostgreSQL 16.x (pgvector supported) |
| Class / storage | `db.t4g.micro`, 20 GB gp3, single-AZ |
| Access | `publicly_accessible = false`; `iam_database_authentication_enabled = true`; `rds.force_ssl = 1` |
| Parameters | `rds.logical_replication = 1`, `max_slot_wal_keep_size = 2048` (MB). Caps WAL kept for an idle slot; if exceeded, the slot is invalidated and DMS must redo the full load – acceptable because the tracker is tiny |
| Credentials | `manage_master_user_password = true` (RDS-managed secret) |
| Backups | 1-day retention, no final snapshot in dev, `deletion_protection = false` |
| Encryption | Storage encrypted with the AWS-managed key |

### 5.4 IAM – role matrix

All roles trust only their service principal (or GitHub OIDC); all resource ARNs are scoped to this project's buckets/prefixes, tables and functions.

| Role | Key permissions |
|---|---|
| `ingest` | `s3:PutObject` on `raw/*`; `s3:GetObject`/`PutObject` on `raw/state/*` |
| `enrich` | `s3:GetObject` on `raw/*`; `s3:PutObject` on `staging/enrichment/*`, `staging/dead_letter/*`; `bedrock:InvokeModel` on the extraction model / inference profile ARNs |
| `embed` | `s3:GetObject` on `raw/*`, `config/cv/*` and the Athena results bucket; `s3:PutObject` on `staging/embeddings/*`; `bedrock:InvokeModel` on the embedding model ARN |
| `match` | VPC ENI permissions (`AWSLambdaVPCAccessExecutionRole`); `rds-db:connect` as `match_app`; `s3:GetObject` on `staging/embeddings/*`, `staging/current/*`; `s3:PutObject` on `staging/match_scores/*` |
| `db-migrate` | VPC ENI permissions; `rds-db:connect` as `migrator` |
| `dispatch-dbt` | `secretsmanager:GetSecretValue` on the GitHub token secret only |
| `digest` | Athena query on workgroup; LF `SELECT` on `marts.top_matches_daily`; `ses:SendEmail` from the verified identity; `s3:*Object` on `digest/sent/*` |
| `sfn-daily` | `lambda:InvokeFunction` on project functions; Athena + Glue + LF data access for curate/merge; `rds:Start/Stop/DescribeDBInstances` on the instance; `dms:Create/Delete/Describe` on replication instances and tasks tagged `project=stellenradar`, `dms:Start/StopReplicationTask`, `iam:PassRole` for the DMS service roles; `s3:GetObject`/`PutObject` on `cdc/_state/*`; `s3:GetObject` on the Athena results bucket (Map `ItemReader`); `sns:Publish`; `cloudwatch:PutMetricData` |
| `gha-terraform` | Trust: `repo:saravanaprabhu1398-afk/stellenradar:ref:refs/heads/main` (apply) and `:pull_request` (plan, read-only policy) |
| `gha-dbt` | Trust: workflows running on `main` (covers `repository_dispatch` and `schedule`, which always run on the default branch); Athena, Glue, LF data access on `curated` (read, without `applications.notes`) and `marts` (write); `lambda:InvokeFunction` on digest |
| `gha-dbt-ci` | Trust: `pull_request`; LF read on `curated` (without `notes`), create/write/drop only in `dev_ci_*` databases; no Lambda invoke |

Lake Formation grants mirror this matrix. Only `sfn-daily`, which writes `curated.applications`, can read the `notes` column; `gha-dbt`, `gha-dbt-ci` and the read-only `analyst` role get column-filtered grants without it. The CV in the config bucket is readable only by `embed`.

---

## 6. CI/CD

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | Pull request, push to `main` | `ruff check`, `ruff format --check`, `mypy src/common`, `pytest tests/unit` (coverage report), `terraform fmt/validate/tflint`, `dbt parse` |
| `terraform.yml` | PR → `plan` (posted as PR comment); push to `main` → `apply` | OIDC role `gha-terraform`; `apply` protected by a GitHub environment `dev` with required review (me) |
| `dbt-daily.yml` | `repository_dispatch`, fallback schedule, manual | §4.9 |
| `dbt-ci` job (in `ci.yml`) | PR touching `dbt/` | OIDC role `gha-dbt-ci`; `dbt build --target ci` against `dev_ci_<pr>`, then drop the schema |

Branch protection on `main`: PR required, CI green required, linear history.

## 7. Error handling summary

| Failure | Detected by | Effect | Recovery |
|---|---|---|---|
| BA API down | Ingest retries exhausted | Run fails if > 50% terms fail; otherwise partial, no closures | Automatic next day; manual re-run |
| Single detail fetch fails | Ingest | Posting fetched next run | Automatic |
| LLM returns invalid JSON | `validate()` | Retry once, then dead letter; run continues if < 10% | Fix prompt (new version); dead letters re-enter the work list automatically |
| Bedrock throttling | botocore adaptive retry, Step Functions retry | Slower run | Automatic |
| RDS fails to start | `WaitForRds` timeout | Run fails after enrichment is saved | Re-run; work list resumes from table state |
| DMS fails | `RunCdcTask` timeout | Run fails; DMS instance deleted, RDS stopped by cleanup | Changes stay in the slot; next run resumes from the last checkpoint |
| dbt test fails | GitHub Actions | No digest; GitHub email | Fix model, re-run workflow |
| Digest already sent | `sent` marker | No duplicate | – |

## 8. Requirements traceability

| Requirement | Implemented in |
|---|---|
| FR-01–03 | §4.1 Ingest |
| FR-04–05 | §4.2 Curate |
| FR-06–09 | §4.3 Enrich, §4.2 work list |
| FR-10 | `eval/` (test plan) |
| FR-11–13 | §4.6 tracker schema, §4.7 CDC |
| FR-14–16 | §4.4 Embed, §4.5 Match, §4.8 ranking |
| FR-17 | §4.9 Digest |
| FR-18–21 | §4.8 dbt marts |
| FR-22 | §4.10 error policy, §4.11 |
| FR-23 | §5.4 + security doc |
| FR-24 | §4.10 `Cleanup`, §4.11 22:00 stop |
| NFR-02 | §4.2 idempotency, §4.10 retries |
| NFR-05 | §5.1 |
| NFR-06–08 | §5.2, §5.4, ADR-003, ADR-007 |
| NFR-09 | §4.1 throttle + User-Agent |
| NFR-11 | §4.3 prompt versioning |
| NFR-12 | §6 |
| NFR-13 | §1 logging, §4.11 metrics |

## 9. Open items

| ID | Item | Resolve in |
|---|---|---|
| O1 | ✅ Spike S1 done ([results](spikes/S1-ba-api.md)): search `/pc/v6/jobs`, detail `/pc/v4/jobdetails/{base64}`; field mapping in data model §3 | – |
| O2 | ✅ Spike S2 done: `EMBED_MODEL_ID = amazon.titan-embed-text-v2:0`, `EXTRACT_MODEL_ID = eu.amazon.nova-lite-v1:0` (development default; final Nova model chosen in Week 2, [ADR-005](decisions/ADR-005-bedrock-models.md)). Confirm forced `toolChoice` works with the chosen Nova model | Week 2 |
| O3 | CDC resume from checkpoint on a new task/instance, and DMS credential access without endpoints (Spike S3) | Week 2, day 1 |
| O4 | ✅ SCD2 statements accepted by Athena and idempotent ([spike S4](spikes/S4-athena-scd2.md)). Still open: reading the Athena result CSV with `ItemReader` | Week 2 (enrichment Map) |
| O5 | Labelling guide for the 50-posting evaluation set | Test plan |
