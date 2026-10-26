SELECT DISTINCT json_extract_scalar(line, '$.posting.referenznummer') AS refnr
FROM {db_raw}.postings_lines
WHERE ingest_date = '{run_date}'
  AND run_id = '{run_id}'
  AND "$path" LIKE '%/search\_%' ESCAPE '\'
  AND json_extract_scalar(line, '$.posting.referenznummer') IS NOT NULL
