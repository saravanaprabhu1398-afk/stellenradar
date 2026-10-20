# ADR-005: Bedrock models and EU cross-region inference

| Status | Date | Deciders | Related |
|---|---|---|---|
| Proposed – pending spike S2 | 2026-10-19 | Prabhu Saravanan | PRD FR-06–08, FR-14, NFR-04, NFR-11 · HLD §6.3 |

## Context

Two model types are needed:
1. **Extraction:** read a (mostly German) job description and return the 9-field JSON schema reliably.
2. **Embeddings:** represent postings (mostly German) and the CV (English) in the same vector space for similarity.

NFR-04 says all data stays in eu-central-1. On Bedrock, some newer text models are offered in Frankfurt only through **cross-region inference profiles** (`eu.` prefix), which route each request to one of several **EU** regions.

## Decision drivers

- Extraction accuracy ≥ 90% (SM-03) on German text
- Low cost per posting (50–300 postings/day)
- Reliable JSON output
- Multilingual embeddings (German ↔ English)
- Data residency: EU at minimum, Frankfurt where possible

## Options considered

**Extraction**

| Option | Pros | Cons |
|---|---|---|
| **Small Claude model (Haiku class) via EU inference profile** | Strong German understanding and structured output; low cost | Requests may be processed in other EU regions |
| Larger Claude model (Sonnet class) | Higher accuracy ceiling | Several times the cost; likely unnecessary for extraction |
| Amazon Nova Micro/Lite | Very low cost | German extraction quality unproven for this schema |
| Open-weight model (e.g. Llama, Mistral) on Bedrock | Low cost | Regional availability and JSON reliability vary |

**Embeddings**

| Option | Pros | Cons |
|---|---|---|
| **Amazon Titan Text Embeddings V2 (1,024 dims)** | Multilingual; configurable dimensions; low cost | – |
| Cohere Embed Multilingual | Strong multilingual quality | Regional availability to confirm |

## Decision

- **Extraction:** a Haiku-class Claude model through the **EU cross-region inference profile**, called with the Bedrock Converse API, temperature 0, with a JSON schema in the prompt and validation in code.
- **Embeddings:** **Titan Text Embeddings V2**, 1,024 dimensions, normalised, invoked in eu-central-1.
- Model IDs live in configuration (Terraform variables → Lambda environment), never in code. Every enrichment row stores `model_id` and `prompt_version` (NFR-11).
- Upgrade path: if accuracy is below 90% after prompt iteration, evaluate a Sonnet-class model on the same labelled set and record the cost/accuracy trade-off here.

## Consequences

**Positive**
- Good accuracy per euro; a clear, measured upgrade path.
- Model swaps are configuration changes, measured by `eval/score.py`.

**Negative / risks**
- **Documented exception to NFR-04:** extraction requests may be processed in another EU region. Data is public job-ad text (no personal data is sent for extraction), so the residency risk is low. The CV is sent only to the embedding model, in eu-central-1.
- Model deprecations: pinned IDs must be reviewed when AWS announces end-of-life.
- LLM output can still be wrong; mitigated by schema validation, `null`-when-unknown instructions and the evaluation set (PRD R2).

## Validation and revisit triggers

- **Spike S2 (Week 1):** confirm which model IDs are enabled in the account for eu-central-1 and via the EU profile; record the exact IDs here and move status to *Accepted*.
- Week 2: SM-03 ≥ 90% on the labelled set with the chosen model.
- Revisit if accuracy stalls below target, costs exceed the cost model, or a Frankfurt-only model of similar quality becomes available.
