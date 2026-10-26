SELECT
  refnr, title, employer, location, description, salary_text, employment_type,
  homeoffice_possible, is_temp_agency, published_at, source_url, raw_s3_key,
  to_hex(sha256(to_utf8(concat_ws('|',
    coalesce(title, ''), coalesce(employer, ''), coalesce(location, ''), coalesce(description, ''),
    coalesce(salary_text, ''), coalesce(employment_type, '')
  )))) AS content_hash
FROM (
  SELECT
    json_extract_scalar(line, '$.meta.refnr') AS refnr,
    trim(json_extract_scalar(line, '$.detail.stellenangebotsTitel')) AS title,
    trim(json_extract_scalar(line, '$.detail.firma')) AS employer,
    nullif(concat_ws(' ',
      json_extract_scalar(line, '$.detail.stellenlokationen[0].adresse.plz'),
      json_extract_scalar(line, '$.detail.stellenlokationen[0].adresse.ort')
    ), '') AS location,
    trim(regexp_replace(json_extract_scalar(line, '$.detail.stellenangebotsBeschreibung'), '\s+', ' ')) AS description,
    nullif(json_extract_scalar(line, '$.detail.verguetungsangabe'), 'KEINE_ANGABEN') AS salary_text,
    CASE
      WHEN json_extract_scalar(line, '$.detail.arbeitszeitVollzeit') = 'true' THEN 'full_time'
      WHEN json_extract_scalar(line, '$.detail.arbeitszeitTeilzeitFlexibel') = 'true'
        OR json_extract_scalar(line, '$.detail.arbeitszeitTeilzeitVormittag') = 'true'
        OR json_extract_scalar(line, '$.detail.arbeitszeitTeilzeitNachmittag') = 'true'
        OR json_extract_scalar(line, '$.detail.arbeitszeitTeilzeitAbend') = 'true' THEN 'part_time'
    END AS employment_type,
    try_cast(json_extract_scalar(line, '$.detail.homeofficemoeglich') AS boolean) AS homeoffice_possible,
    try_cast(json_extract_scalar(line, '$.detail.istArbeitnehmerUeberlassung') AS boolean) AS is_temp_agency,
    try_cast(json_extract_scalar(line, '$.detail.datumErsteVeroeffentlichung') AS date) AS published_at,
    'https://www.arbeitsagentur.de/jobsuche/jobdetail/' || json_extract_scalar(line, '$.meta.refnr') AS source_url,
    "$path" AS raw_s3_key,
    row_number() OVER (
      PARTITION BY json_extract_scalar(line, '$.meta.refnr')
      ORDER BY json_extract_scalar(line, '$.meta.fetched_at') DESC
    ) AS rn
  FROM {db_raw}.postings_lines
  WHERE ingest_date = '{run_date}'
    AND run_id = '{run_id}'
    AND "$path" LIKE '%/details\_%' ESCAPE '\'
) d
WHERE rn = 1 AND refnr IS NOT NULL
