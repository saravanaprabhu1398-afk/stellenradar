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

_Not run yet – next: `make eval-score`._
