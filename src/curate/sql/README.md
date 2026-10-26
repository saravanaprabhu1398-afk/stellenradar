# Athena SQL

Statements run by the curate Lambda in the daily pipeline and by `scripts/athena_sql.py` for manual runs (SDD §4.2). Placeholders in
`{curly_braces}` are filled in by the runner; there is exactly one statement per file.

| Placeholder | Meaning |
|---|---|
| `{db_raw}`, `{db_curated}` | Glue database names (`stellenradar_raw`, `stellenradar_curated`) |
| `{data_bucket}` | Data bucket name |
| `{run_id}`, `{run_date}` | Ingest run whose raw files are curated |
| `{ingested_at}` | Version timestamp for this run, `YYYY-MM-DD HH:MM:SS` (UTC) |
| `{details}` | The contents of `curate/_details.sql` (today's normalised detail records) |
| `{listed}` | The contents of `curate/_listed.sql` (all references listed in the run) |

Files starting with `_` are fragments, not statements.
