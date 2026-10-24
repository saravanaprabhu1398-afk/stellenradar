MERGE INTO {db_curated}.postings t
USING ({details}) s
  ON t.refnr = s.refnr AND t.is_current = true
WHEN MATCHED AND t.content_hash <> s.content_hash THEN
  UPDATE SET valid_to = TIMESTAMP '{ingested_at}', is_current = false
