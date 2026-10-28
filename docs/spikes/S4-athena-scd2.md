# Spike S4 – SCD2 with Athena and Iceberg

| Date | Author | Status | How to repeat |
|---|---|---|---|
| 2026-10-26 | Prabhu Saravanan | **PASS** | `make spike-s4 RUN_ID=<id> RUN_DATE=<date>` ([`scripts/athena_sql.py`](../../scripts/athena_sql.py), SQL in `src/curate/sql/`) |

**Question:** can Athena maintain SCD Type 2 history in an Iceberg table with plain SQL – and is a re-run of the same day harmless? ([System Design §10](../02-system-design.md), [ADR-001](../decisions/ADR-001-iceberg-table-format.md), [SDD §4.2](../03-sdd.md))

**Method:** on the first full ingest (`initial-20261024T064440Z`, 500 detail records), run the DDL and the three curate statements, record the table invariants, run the same three statements again with the same parameters, and compare.

## Statements tested

| File | Pattern | Result |
|---|---|---|
| `ddl/010_curated_postings.sql` | `CREATE TABLE IF NOT EXISTS … PARTITIONED BY (bucket(16, refnr))`, Iceberg, zstd Parquet | ✅ |
| `curate/010_close_changed.sql` | `MERGE … USING (<details subquery>) … WHEN MATCHED AND hash differs THEN UPDATE` | ✅ |
| `curate/020_close_disappeared.sql` | `MERGE … USING (SELECT … FROM same table WHERE refnr NOT IN (<listed subquery>))` – self-referencing source | ✅ |
| `curate/030_insert_new_versions.sql` | `INSERT … SELECT … WHERE NOT EXISTS (current row)` | ✅ |

SCD2 therefore uses **separate statements** (close, then insert) – the fallback anticipated in the system design – rather than one MERGE that both updates and inserts the same key.

## Results

| Invariant | After run 1 | After run 2 |
|---|---|---|
| Current rows | 500 | 500 |
| Total versions | 500 | 500 |
| Closed versions | 0 | 0 |
| References with more than one current row | 0 | 0 |
| `is_current` ⇔ `valid_to IS NULL` violations | 0 | 0 |

- **Idempotent:** the second run changed nothing.
- **No false closures:** close-disappeared ran (manifest `complete = true`) and closed nothing, because every curated posting appeared in the full listing.
- **Cost:** ~1–1.7 MB scanned per statement (Athena bills a 10 MB minimum per query) – negligible.

## Confirmed in the first scheduled-style run (2026-10-27)

The first full pipeline run (`make run-pipeline`, Ingest → Curate in Step Functions) merged a new complete listing into the S4 data:

| Result | Count |
|---|---|
| Changed postings closed and re-inserted as a new version | 8 |
| Disappeared postings closed (listing `complete = true`) | 22 |
| Current rows / total versions / closed versions after the run | 857 / 887 / 30 |
| Multiple current versions per `refnr` | 0 |
| `is_current` ⇔ `valid_to IS NULL` violations | 0 |

Both cases that the spike could not cover with one snapshot now work on real data, and the invariants held.

## Not covered

- Reading the Athena result CSV with a Step Functions `ItemReader` – no longer needed: enrichment runs in a single Lambda that reads its work list with `GetQueryResults` (SDD §4.3).
