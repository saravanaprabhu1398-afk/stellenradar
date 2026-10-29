SELECT p.refnr, p.content_hash, p.title, p.employer, p.location, p.description
FROM {db_curated}.postings p
LEFT JOIN {db_curated}.posting_enrichment e
  ON e.refnr = p.refnr
 AND e.content_hash = p.content_hash
 AND e.prompt_version = '{prompt_version}'
WHERE p.is_current
  AND e.refnr IS NULL
  AND p.description IS NOT NULL
ORDER BY p.published_at DESC NULLS LAST, p.refnr
LIMIT {limit}
