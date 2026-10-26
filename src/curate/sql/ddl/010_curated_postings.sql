CREATE TABLE IF NOT EXISTS {db_curated}.postings (
  refnr               string,
  title               string,
  employer            string,
  location            string,
  description         string,
  salary_text         string,
  employment_type     string,
  homeoffice_possible boolean,
  is_temp_agency      boolean,
  published_at        date,
  source_url          string,
  content_hash        string,
  raw_s3_key          string,
  valid_from          timestamp,
  valid_to            timestamp,
  is_current          boolean,
  run_id              string
)
PARTITIONED BY (bucket(16, refnr))
LOCATION 's3://{data_bucket}/curated/postings/'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format' = 'parquet',
  'write_compression' = 'zstd',
  'vacuum_max_snapshot_age_seconds' = '604800'
)
