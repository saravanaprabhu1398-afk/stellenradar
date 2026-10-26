-- Only run when the ingest manifest says complete = true (all terms listed without errors).
MERGE INTO {db_curated}.postings t
USING (
  SELECT c.refnr
  FROM {db_curated}.postings c
  WHERE c.is_current = true
    AND c.refnr NOT IN ({listed})
) s
  ON t.refnr = s.refnr AND t.is_current = true
WHEN MATCHED THEN
  UPDATE SET valid_to = TIMESTAMP '{ingested_at}', is_current = false
