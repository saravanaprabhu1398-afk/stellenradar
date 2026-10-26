# ADR-001: Apache Iceberg as table format

| Status | Date | Deciders | Related |
|---|---|---|---|
| **Accepted** (spike S4 passed 2026-10-26) | 2026-10-19 | Prabhu Saravanan | PRD FR-04, FR-05, FR-23 · HLD §5, §6.5 |

## Context

The curated and mart layers need row-level updates: `curated.postings` keeps SCD Type 2 history (close the old version, insert the new one), and `curated.applications` applies CDC inserts, updates and deletes. The query engine is Athena, the catalog is Glue and governance is Lake Formation. Plain Parquet files on S3 cannot be updated in place, so an open table format is required.

My background is Delta Lake on Databricks, so Delta is the default I would reach for.

## Decision drivers

- `MERGE INTO`, `UPDATE` and `DELETE` from **Athena**, with no Spark cluster
- Works with Glue Data Catalog and Lake Formation fine-grained permissions
- Supported by dbt-athena for incremental models
- No always-on compute (NFR-03)
- Time travel for debugging and replay

## Options considered

| Option | Athena writes | Lake Formation | dbt-athena | Notes |
|---|---|---|---|---|
| **Apache Iceberg** | Yes – `MERGE`, `UPDATE`, `DELETE`, `OPTIMIZE`, `VACUUM` | Yes, including column-level | Yes (`table_type='iceberg'`) | AWS's first-class open table format |
| Delta Lake | Read only | Read only via Athena | Read only | Writes would need Glue Spark jobs or EMR – extra cost and code |
| Apache Hudi | Read only | Limited | No incremental writes | Same problem as Delta |
| Plain Parquet + rewrite partitions | Insert / overwrite only | Yes | Yes | SCD2 and CDC need full rewrites; fragile and not "production-style" |

## Decision

Use **Apache Iceberg (format version 2)** for every table in `curated` and `marts`. Raw and staging stay as plain JSON/Parquet external tables because they are append-only.

## Consequences

**Positive**
- SCD2 and CDC merges are plain SQL in Athena, orchestrated by Step Functions – no Spark.
- Time travel (`FOR TIMESTAMP AS OF`) helps debug bad runs.
- Lake Formation column-level permissions work on the tables that hold personal data.
- Strong portfolio story: Delta on Azure/Databricks and Iceberg on AWS, with the trade-offs understood.

**Negative / risks**
- Many small files from daily merges. Mitigation: weekly `OPTIMIZE … REWRITE DATA` and `VACUUM` step; snapshot expiry after 7 days.
- Athena's Iceberg `MERGE` has limits (e.g. one statement cannot both update and insert the same key twice). SCD2 may need two statements – see spike S4.
- Some Iceberg features (e.g. branching/tagging) are not available through Athena; not needed here.

## Validation and revisit triggers

- ✅ **Spike S4 (2026-10-26, [results](../spikes/S4-athena-scd2.md)):** SCD2 with separate close and insert statements works in Athena on 500 real postings; a re-run changed nothing and all invariants held.
- Revisit if the platform moves to Spark-based processing (EMR Serverless / Glue), where Delta would be equally viable.
