# Evaluation results

Results of `make eval-score` (test plan §4) are recorded here, newest first: date, model, prompt version, metrics and the
gate decision.

## Labelled set v1 (2026-11-05)

| Property | Value |
|---|---|
| File | [`eval/labelled_set.jsonl`](../eval/labelled_set.jsonl) – 50 postings, 35 dev / 15 holdout, labels only (no ad text) |
| Sample | `make eval-sample` on 2026-10-29: 35 German / 15 English ads, quota-stratified (test plan §4.1) |
| Labelled by | **An AI assistant (Claude, Anthropic)**, at the project owner's request, from the full ad text and [`eval/labelling_guide.md`](../eval/labelling_guide.md). A manual labelling pass was lost before it was saved. |
| Human check | Pending: the project owner re-checks 10 labels in `make eval-label` (test plan §4.1 quality check). Disagreements > 5 % of fields → fix labels and the guide, re-score. |

**Why AI labels are acceptable here, and their limits.** The labeller is a different model family from the Nova models under test,
so the score does not simply reward a model for agreeing with itself. But the score measures agreement with the labeller: where
the labeller is wrong, the score is wrong. Until the human check is done, results are **provisional** and are not used to
accept ADR-005's final model choice.

**Coverage of this set** (field → labelled values):

| Field | Distribution | Consequence |
|---|---|---|
| `german_level_required` | fluent 32 · none 10 · basic 8 | well covered |
| `seniority` | null 21 · senior 13 · mid 8 · principal 5 · lead 3 · junior 0 | no junior postings – junior detection is untested |
| `remote_policy` | hybrid 24 · null 21 · remote 4 · onsite 1 | remote/onsite thinly covered |
| `salary_*` | stated in 2 of 50 | salary **hallucination** (48 ads without salary) is well tested; salary **reading** is not |
| `english_only` | false 50 | no "German not required" ad – the positive case is untested |
| `visa_sponsorship_mentioned` | true 2 · false 48 | thin |

Labelled set v2 should add English-only ads, ads with salaries and junior postings (the daily data now has ~860 current
postings to sample from, most of them enriched).

## Scores

### 2026-11-05 – set v1, first run (provisional: AI labels, human check pending)

| Config | Overall | Dev | Holdout | Skills F1 | Salary halluc. | Invalid | Tokens in/out | p50 s | $ / 1k | Gate |
|---|---|---|---|---|---|---|---|---|---|---|
| `eu.amazon.nova-lite-v1:0@v1` (production today) | 69% | 64% | 81% | 0.53 | 21% | 3 | 2275 / 149 | 1.1 | 0.17 | FAIL |
| `eu.amazon.nova-lite-v1:0@v2` | 59% | 55% | 69% | 0.58 | 77% | 3 | 2559 / 130 | 3.4 | 0.18 | FAIL |
| `eu.amazon.nova-2-lite-v1:0@v2` | **78%** | 77% | 79% | **0.71** | **0%** | **0** | 2829 / 162 | 1.3 | 1.25 | FAIL |
| `eu.amazon.nova-pro-v1:0@v2` (run 12:15 UTC, after the fix) | **82%** | 81% | 84% | 0.64 | 8% | 0 | 2478 / 106 | 1.2 | 2.32 | FAIL |

| Field | Nova Lite v1 | Nova Lite v2 | Nova 2 Lite v2 | Nova Pro v2 |
|---|---|---|---|---|
| skills (F1 ≥ 0.8) | 22% | 24% | 30% | 18% |
| cloud_platforms | 84% | 84% | 98% | 94% |
| seniority | 46% | 64% | 56% | 88% |
| salary_min_eur | 74% | 20% | 96% | 94% |
| salary_max_eur | 72% | 18% | 100% | 90% |
| german_level_required | 80% | 78% | 84% | 88% |
| english_only | 72% | 74% | 86% | 76% |
| visa_sponsorship_mentioned | 90% | 90% | 100% | 100% |
| remote_policy | 84% | 80% | 52% | 88% |

**Reading:**
- **No config passes the gate.**
- **Nova 2 Lite + v2 is clearly best.** It has no invented salaries, no invalid outputs, and +9 points overall over production.
- **Prompt v2 hurts Nova Lite.** Salary hallucination goes from 21% to 77%: the smaller model copies the example amounts from the salary rules.
- **The remaining gaps** are skills (the per-posting F1 ≥ 0.8 rule is strict for open lists), seniority and remote policy (52% for Nova 2 Lite). Some of these may be label-rule disagreements rather than model errors (e.g. "Remote Work möglich" labelled `remote`). They are reviewed on the **dev split only** before prompt v3, so the holdout stays clean.
- **Nova Pro was not scored:** the model omitted a nullable field, which crashed scoring. Fixed – omitted nullable fields now read as null (also in production), and a failing posting counts as invalid instead of aborting the config.

### Error analysis of the first run (dev split; holdout rows were listed but not used for changes)

- **Invented salaries.** All 4 of Nova Pro's are absent from the ad text:
  - 100,000 from "€3M–5M deals";
  - 142,500 from "14,25 Gehälter";
  - 400,000 twice, the top of the allowed range.

  A deterministic check, `validate.ungrounded_salaries`, accepts a salary only if it matches an amount in the ad (as stated, × 12, or "65k"). On the stored outputs it would have caught all 20 invented values of Nova Lite v1, 73 of Nova Lite v2 and 7 of Nova Pro's 8 wrong values. It rejects none of the labelled salaries. The 8th Pro value is "mind. 59.000 €" read as min = max, which is a prompt rule.
- **Nova 2 Lite ignores two prompt rules systematically:**
  - it answers `onsite` when the ad says nothing (22 of 24 remote misses);
  - it answers `mid` without a seniority signal (19 of 22 seniority misses).

  Nova Pro does neither.
- **Nova Pro's remaining misses** are mostly borderline: "erfahrener Engineer" read as senior, and a title naming two levels.
- **`english_only`:** all 12 of Nova Pro's misses are English-language ads that never mention German. The labels keep the stricter rule: only an explicit statement counts, while "no German mentioned" is already `german_level_required: none`. Prompt v3 states this.
- **Skills:** the overall overlap is 0.64, but only 18% of postings reach F1 ≥ 0.8. Differences are mostly granularity ("transformer" vs "transformers", "catia" vs "catia v5") and generic terms ("ai", "governance"). The per-posting threshold was set before any data. It will be recalibrated against human-vs-labeller skill agreement from the owner's 10-posting check, since that agreement is the ceiling for any model.

### Label corrections

| Date | Posting | Field | Old → new | Reason |
|---|---|---|---|---|
| 2026-11-09 | 19913-3000711792188001-S (dev) | remote_policy | remote → hybrid | "Remote Work sowie mobiles Arbeiten möglich" for a consulting role with customer travel; the guide rule was wrong (now: remote only when fully remote) |
| 2026-11-09 | 19913-3082281792620000-S (dev) | remote_policy | remote → hybrid | same wording and role type |

### Changes for the next run

- **Prompt v3:**
  - "mind./ab" → min only;
  - salary non-amounts listed (bonuses, deal volumes, "14,25 Gehälter");
  - seniority without a number → null, and the higher of two title levels;
  - a German-language ad with no language requirement → fluent;
  - stricter `english_only`;
  - "Remote Work möglich" → hybrid, and never assume onsite;
  - cloud only when named;
  - more generic non-skills.
- **Salary guard** in the extractor for every model: an ungrounded salary is sent back once, then set to null. The posting is not lost.
- **Taxonomy:** variants seen in this run ("transformer", "nix/nixos", "tls/ssl", …).

### 2026-11-09 – set v1, second run (prompt v3, salary guard)

| Config | Overall | Dev | Holdout | Skills F1 | Salary halluc. | Invalid | Tokens in/out | $ / 1k | Gate |
|---|---|---|---|---|---|---|---|---|---|
| `eu.amazon.nova-pro-v1:0@v3` | **86.7%** | 84.8% | **91.1%** | 0.68 | 2.1% | 0 | 3066 / 112 | 2.81 | FAIL (overall, skills, halluc.) |
| `eu.amazon.nova-pro-v1:0@v2` | 85.8% | 84.4% | 88.9% | 0.67 | 0% | 0 | 2908 / 123 | 2.72 | FAIL (overall, skills) |
| `eu.amazon.nova-2-lite-v1:0@v3` | 81.3% | 81.6% | 80.7% | 0.71 | 0% | 0 | 3153 / 166 | 1.36 | FAIL (+ seniority 58%, remote 56%) |

Per field for Nova Pro v3:
- skills 34%;
- cloud 96%, seniority 96%;
- salary min 98%, max 98%;
- German 90%, english_only 78%;
- visa 100%, remote 90%.

**Remaining Nova Pro v3 misses (dev):**
- **The one invented salary** (BaFin, 44,100 estimated from "E12 TVöD") passed the 1% tolerance of the guard: the phone extension "-3651" × 12 = 43,812 was within it. The guard now requires an exact match.
- **"mind. 59.000 €"** still came back as min = max. It is now cleared deterministically when "ab / mind. / mindestens / at least / from" precedes the amount.
- **`english_only=true` for 11 English ads without a statement**, despite the explicit v3 rule. It now requires evidence in the text ("German not required", "keine Deutschkenntnisse", "English is sufficient", "Arbeitssprache Englisch" …), with one retry and then false. None of the 50 ads contains such a statement, so the positive case is still untested (coverage gap noted above).

**Replayed with the new guards** (stored model outputs, no new Bedrock calls; a live run would add the retry):

| Config | Overall | Dev | Holdout | Without skills | Salary halluc. |
|---|---|---|---|---|---|
| Nova Pro v3 + guards | **89.6%** | 88.3% | **92.6%** | **96.5%** | 0% |
| Nova Pro v2 + guards | 88.7% | 87.9% | 90.4% | 95.8% | 0% |

**Decision (provisional, ADR-005):** production switches to Nova Pro + v3 with the guards.
- The only gate criterion still failing is skills (per-posting F1 ≥ 0.8; overall F1 0.68).
- It will be recalibrated from the owner's 10-posting check: human-vs-labeller skill agreement is the realistic ceiling.
- `PROMPT_VERSION` v3 puts all current postings back on the work list. At 200 per run they are re-enriched over about 5 daily runs, costing about $2.50 in total.
