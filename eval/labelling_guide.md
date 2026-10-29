# Labelling guide – extraction evaluation set

Label what the **advertisement states**, not what you would infer about the employer. When unsure, re-read the rule
here; if the rule does not settle it, add a line to this guide so the next posting gets the same answer.

The file `eval/work/labelling.yaml` is pre-filled with prompt v1's answers to save typing. **Check every field against
the ad** – v1 is often wrong, and an unchecked pre-fill makes v1 look better than it is. When a posting is done, set
`checked: true`. Set `skip: true` only if the text is unreadable or empty (a non-data job is still a valid posting).

The full ad is in `description`; `url` opens it on the Arbeitsagentur site if the text looks truncated.

## Fields

| Field | Rule |
|---|---|
| `skills` | Concrete technologies, tools, languages, platforms and named methods the ad asks for or mentions as a plus: `python`, `sql`, `spark`, `airflow`, `dbt`, `terraform`, `data modeling`, `power bi`. Lower-case, one per entry. **Not** skills: soft skills, degrees, spoken languages, job functions or broad fields (`data engineering`, `big data`, `ai`, `it`). Synonyms are normalised automatically (`pyspark` → `spark`), so write the plain name. Order does not matter. |
| `cloud_platforms` | `aws`, `azure`, `gcp` – only if named (also via a service: "Databricks on Azure" → `azure`, "BigQuery" → `gcp`). |
| `seniority` | Only explicit signals: `junior` (Junior, Berufseinsteiger, Absolvent, "erste Erfahrung", 0–2 years), `mid` (an explicit 2–5 years), `senior` (Senior, 5+ years), `lead` (Lead, Teamleitung, Head of), `principal` (Principal, Staff, Architect). Otherwise `null`. |
| `salary_min_eur`, `salary_max_eur` | Yearly gross EUR as integers. Yearly stays as is; monthly × 12; hourly/daily, "attraktive Vergütung" or a tariff name without an amount → `null`. A single amount → both. "ab 60.000" → min 60000, max null. "bis 80.000" → min null, max 80000. |
| `german_level_required` | `fluent` (fließend, verhandlungssicher, sehr gute Deutschkenntnisse, C1/C2, or a German ad that requires very good communication skills), `basic` (Grundkenntnisse, B1/B2, German "a plus"), `none` (not required, or an English ad that never mentions German). |
| `english_only` | `true` only if the ad says German is not required or English is sufficient. Must be `false` when German is `fluent`. |
| `visa_sponsorship_mentioned` | `true` only if visa sponsorship, work-permit support or relocation support is mentioned. |
| `remote_policy` | `remote` (fully remote / 100 % Homeoffice), `hybrid` (office days plus mobile work), `onsite` (on site only). Not mentioned → `null`. |

## Process

1. `make eval-sample` once – it refuses to overwrite an existing file (your labels) unless forced.
2. Label in any editor; save often. 50 postings take about 30–45 minutes.
3. `make eval-freeze` validates the labels (same rules as production) and writes `eval/labelled_set.jsonl`, which holds
   only `refnr`, `content_hash`, labels and the dev/holdout split – no ad text, so it can be committed.
4. Quality check (test plan §4.1): after 3+ days, re-label 10 postings without looking, and compare.
