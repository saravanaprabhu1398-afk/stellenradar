# Spike S1 – BA job search API

| Date | Author | Status | Scripts |
|---|---|---|---|
| 2026-10-21 | Prabhu Saravanan | Done | `spikes/s1_ba_api/probe.py`, `spikes/s1_ba_api/analyse.py` (raw output in `spikes/s1_ba_api/out/`, git-ignored) |

**Question:** which BA endpoints work, how does pagination behave, do job details contain the full description text, and how many postings are there? ([System Design §10](../02-system-design.md), [SDD O1](../03-sdd.md))

**Method:** ~20 throttled requests (≥ 1.1 s apart) with header `X-API-Key: jobboerse-jobsuche` against `https://rest.arbeitsagentur.de/jobboerse/jobsuche-service`; no AWS resources.

## Results

### Search endpoint

| Endpoint | Result |
|---|---|
| `/pc/v4/jobs` | **HTTP 403** – no longer usable |
| `/pc/v4/app/jobs` | **HTTP 403** |
| `/pc/v6/jobs` | ✅ HTTP 200 |

v6 response: top-level keys `ergebnisliste` (the postings), `facetten`, `maxErgebnisse` (total hits), `page`, `size`.

v6 posting fields: `referenznummer` (natural key – replaces v4's `refnr`), `stellenangebotsTitel`, `firma`, `stellenlokationen`, `hauptberuf`, `alleBerufe`, `aenderungsdatum`, `datumErsteVeroeffentlichung`, `veroeffentlichungszeitraum`, `eintrittszeitraum`, `vertragsdauer`, `verguetungsangabe`, `homeofficemoeglich`, `arbeitszeitVollzeit`, `arbeitszeitTeilzeitAbend`, `arbeitszeitTeilzeitFlexibel`, `arbeitszeitTeilzeitNachmittag`, `arbeitszeitTeilzeitVormittag`, `arbeitszeitSchichtNachtWochenende`, `istGeringfuegigeBeschaeftigung`, `quereinstiegGeeignet`, `stellenangebotsart`, `chiffrenummer`, `externeURL`.

### Pagination

| Check | Result |
|---|---|
| `size=100` | ✅ accepted, 100 items per page |
| Pages 1–3 for "Data Engineer" | 300 unique `referenznummer`, **0 duplicates** across pages |
| Latency per search call | 0.8–1.1 s |

### Detail endpoint

| Check | Result |
|---|---|
| `/pc/v4/jobdetails/{base64(referenznummer)}` | ✅ HTTP 200 for 3/3 postings – works with references from the v6 search |
| Description (`stellenangebotsBeschreibung`) | **2,318 / 3,266 / 5,190 characters** – full ad text ✅ |
| `/pc/v3/jobdetails/` fallback | Not needed |

Detail fields = all v6 posting fields above **plus** `stellenangebotsBeschreibung`, `allianzpartnerName`, `allianzpartnerUrl`, `istArbeitnehmerUeberlassung` (temp agency), `istPrivateArbeitsvermittlung`, `istBehinderungGefordert`, `istBetreut`.

### Volume (`angebotsart=1`, Germany-wide)

| Search term | `maxErgebnisse` |
|---|---|
| Data Engineer | 831 |
| Analytics Engineer | 208 |
| Big Data Engineer | 29 |
| ETL Entwickler | 12 |
| Dateningenieur | 1 |
| **Sum (before de-duplication across terms)** | **1,081** |

### Relevance and external listings (first 25 "Data Engineer" results)

- All 25 titles were "Data Engineer" variants (e.g. "Data Engineer (m/w/d)") – no noise in the top results. Relevance of later pages is not yet checked.
- `externeURL` set on 0 of 25 – the sample had no externally hosted ads.

## Conclusions and design changes

| # | Finding | Change |
|---|---|---|
| 1 | v4 search is blocked; v6 works | Ingest uses **`/pc/v6/jobs`**; the natural key field is **`referenznummer`**. SDD §4.1 and data model §3 updated |
| 2 | Detail endpoint returns the full text | Core assumption of the design **confirmed** – LLM enrichment on full descriptions stays as designed |
| 3 | ~1,000 active postings (not 3,500) | Cost model CA-2 lowered; backfill cheaper |
| 4 | ~1 s per call | Throttle is measured **between request starts** (≥ 1.1 s), not after each response, so one call takes ≈ 1.1 s instead of ≈ 2 s. `MAX_DETAILS` set to **500** per run (≈ 10 min); the initial backfill of ~1,000 details spreads over 2–3 runs |
| 5 | `aenderungsdatum` exists in list results | Included in the list hash – a source-side modification triggers a detail re-fetch |
| 6 | Structured `homeofficemoeglich` and `verguetungsangabe` exist | Kept in curated data; in Week 2, compare them with the LLM's `remote_policy` and salary fields (cheap consistency check, possible prompt hint) |
| 7 | "Dateningenieur" returns 1 hit | Search terms (PRD Q1) for v1: **Data Engineer, Analytics Engineer, Big Data Engineer, ETL Entwickler**. Candidates to test in the first ingest runs: "Data Platform Engineer", "Data Architect", "DWH Entwickler" |

## Open follow-ups

- Spot-check relevance on later result pages (pages 5–9 for "Data Engineer") during the first ingest runs; if noise appears, filter on `hauptberuf`.
- Measure the share of postings with `externeURL` across a full run, and their description length.
- Measure new/changed postings per day (cost model CA-1) over the first week.
