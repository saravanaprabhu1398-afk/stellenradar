CREATE TABLE IF NOT EXISTS {db_curated}.posting_enrichment (
  refnr                      string,
  content_hash               string,
  prompt_version             string,
  model_id                   string,
  skills                     array<string>,
  cloud_platforms            array<string>,
  seniority                  string,
  salary_min_eur             int,
  salary_max_eur             int,
  german_level_required      string,
  english_only               boolean,
  visa_sponsorship_mentioned boolean,
  remote_policy              string,
  input_tokens               int,
  output_tokens              int,
  enriched_at                timestamp,
  run_id                     string
)
LOCATION 's3://{data_bucket}/curated/posting_enrichment/'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format' = 'parquet',
  'write_compression' = 'zstd',
  'vacuum_max_snapshot_age_seconds' = '604800'
)
