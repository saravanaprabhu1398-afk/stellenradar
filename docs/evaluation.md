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
| `eu.amazon.nova-pro-v1:0@v2` | – | – | – | – | – | – | – | – | – | not run (scoring bug, fixed) |

| Field | Nova Lite v1 | Nova Lite v2 | Nova 2 Lite v2 |
|---|---|---|---|
| skills (F1 ≥ 0.8) | 22% | 24% | 30% |
| cloud_platforms | 84% | 84% | 98% |
| seniority | 46% | 64% | 56% |
| salary_min_eur | 74% | 20% | 96% |
| salary_max_eur | 72% | 18% | 100% |
| german_level_required | 80% | 78% | 84% |
| english_only | 72% | 74% | 86% |
| visa_sponsorship_mentioned | 90% | 90% | 100% |
| remote_policy | 84% | 80% | 52% |

**Reading:**
- **No config passes the gate.**
- **Nova 2 Lite + v2 is clearly best.** It has no invented salaries, no invalid outputs, and +9 points overall over production.
- **Prompt v2 hurts Nova Lite.** Salary hallucination goes from 21% to 77%: the smaller model copies the example amounts from the salary rules.
- **The remaining gaps** are skills (the per-posting F1 ≥ 0.8 rule is strict for open lists), seniority and remote policy (52% for Nova 2 Lite). Some of these may be label-rule disagreements rather than model errors (e.g. "Remote Work möglich" labelled `remote`). They are reviewed on the **dev split only** before prompt v3, so the holdout stays clean.
- **Nova Pro was not scored:** the model omitted a nullable field, which crashed scoring. Fixed – omitted nullable fields now read as null (also in production), and a failing posting counts as invalid instead of aborting the config.
