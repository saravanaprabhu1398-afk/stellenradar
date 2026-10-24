INSERT INTO {db_curated}.postings
SELECT
  s.refnr, s.title, s.employer, s.location, s.description, s.salary_text, s.employment_type,
  s.homeoffice_possible, s.is_temp_agency, s.published_at, s.source_url, s.content_hash, s.raw_s3_key,
  TIMESTAMP '{ingested_at}' AS valid_from,
  CAST(NULL AS timestamp) AS valid_to,
  true AS is_current,
  '{run_id}' AS run_id
FROM ({details}) s
WHERE NOT EXISTS (
  SELECT 1 FROM {db_curated}.postings c
  WHERE c.refnr = s.refnr AND c.is_current = true
)
