# ADR-005: Bedrock models and EU cross-region inference

| Status | Date | Deciders | Related |
|---|---|---|---|
| Accepted (model family) – final extraction model chosen in Week 2 | 2026-10-19, revised 2026-10-21 | Prabhu Saravanan | PRD FR-06–08, FR-14, NFR-04, NFR-11 · HLD §6.3 · [Cost model §4.2](../06-cost-model.md) |

## Context

Two model types are needed:
1. **Extraction:** read a (mostly German) job description and return the 9-field JSON schema reliably.
2. **Embeddings:** represent postings (mostly German) and the CV (English) in the same vector space for similarity.

NFR-04 says all data stays in eu-central-1. On Bedrock, some text models are offered in Frankfurt only through **cross-region inference profiles** (`eu.` prefix), which route each request to one of several **EU** regions.

The first draft of this ADR proposed a Haiku-class Claude model. Spike S2 (below) changed that.

## Decision drivers

- Extraction accuracy ≥ 90% (SM-03) on German text
- Low cost per posting (50–300 postings/day)
- Reliable structured output (tool calling)
- Multilingual embeddings (German ↔ English)
- Data residency: EU at minimum, Frankfurt where possible
- No extra commercial setup (AWS Marketplace subscription) for a personal project

## Spike S2 results (2026-10-21)

| Model | ID used | Result |
|---|---|---|
| Amazon Titan Text Embeddings V2 | `amazon.titan-embed-text-v2:0` (in-region, eu-central-1) | ✅ 1,024 dims returned |
| Amazon Nova Lite | `eu.amazon.nova-lite-v1:0` (EU profile) | ✅ Converse call succeeded |
| Claude Haiku 4.5 | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | ⚠ One call succeeded, then `INVALID_PAYMENT_INSTRUMENT`: Anthropic models are sold through AWS Marketplace and need a valid payment method and Marketplace subscription |
| Claude Haiku 5.5 | `eu.anthropic.claude-haiku-5-5` | ❌ "not available for this account" (restricted; requires AWS Sales) |

EU inference profiles also listed in the account: `eu.amazon.nova-micro-v1:0`, `eu.amazon.nova-pro-v1:0`, `eu.amazon.nova-2-lite-v1:0`, `eu.anthropic.claude-3-haiku-20240307-v1:0`.

## Options considered

**Extraction**

| Option | Pros | Cons |
|---|---|---|
| **Amazon Nova family (Lite / 2 Lite / Pro) via EU profile** | Billed directly by AWS, no Marketplace subscription; Nova Lite is roughly 20× cheaper than Haiku 4.5; confirmed working | German extraction quality unproven for this schema – measured in Week 2 |
| Claude Haiku 4.5 via EU profile | Strong German understanding and structured output | Needs a Marketplace subscription and payment setup; ~$13/month at base volume |
| Claude Haiku 5.5 | Newest Haiku | Not available to this account |
| Open-weight models (Llama, Mistral) | Low cost | Regional availability and tool-calling reliability vary |

**Embeddings**

| Option | Pros | Cons |
|---|---|---|
| **Amazon Titan Text Embeddings V2 (1,024 dims)** | Multilingual; configurable dimensions; very low cost; confirmed in eu-central-1 | – |
| Cohere Embed Multilingual | Strong multilingual quality | Marketplace model; regional availability to confirm |

## Decision

- **Extraction:** an **Amazon Nova** model through the **EU cross-region inference profile**, called with the Bedrock Converse API, temperature 0, with a forced tool call carrying the JSON schema, and validation in code.
  - Default for development: **`eu.amazon.nova-lite-v1:0`**.
  - Week 2 evaluation compares **Nova Lite, Nova 2 Lite and Nova Pro** on the labelled set; the **cheapest model reaching the accuracy gate** (≥ 90% overall, test plan §4.3) becomes the production model and is recorded here.
- **Embeddings:** **`amazon.titan-embed-text-v2:0`**, 1,024 dimensions, normalised, invoked in eu-central-1 (no cross-region routing).
- **Claude is deferred**, not rejected. If no Nova model reaches the gate after prompt iteration, revisit Claude Haiku 4.5 (requires a valid payment method for the Marketplace subscription) and record the outcome in a new ADR.
- Model IDs live in configuration (Terraform variables → Lambda environment), never in code. Every enrichment row stores `model_id` and `prompt_version` (NFR-11).

## Consequences

**Positive**
- Extraction cost falls from ~$13 to ~$1–9 per month depending on the Nova model (cost model §4.2); the one-time backfill and prompt re-runs become cheap enough to stop worrying about.
- No Marketplace subscription or third-party terms to manage.
- Model swaps remain configuration changes, measured by `eval/score.py`.

**Negative / risks**
- Nova's accuracy on German job ads is unproven; this is the main Week 2 risk. Mitigation: three Nova sizes in the comparison, prompt iteration, Claude as the documented fallback.
- Tool calling with a forced tool choice must be confirmed for the chosen Nova model in Week 2; fallback is JSON-in-text with stricter validation.
- **Documented exception to NFR-04:** extraction requests via the EU profile may be processed in another EU region. Only public job-ad text is sent for extraction. The CV is sent only to the embedding model, in eu-central-1.
- Model deprecations: pinned IDs must be reviewed when AWS announces end-of-life.

## Validation and revisit triggers

- ✅ Spike S2: Titan V2 and Nova Lite confirmed (above).
- Week 2: SM-03 ≥ 90% on the labelled set with the chosen Nova model; record model ID, accuracy and cost per 1,000 postings in `docs/evaluation.md` and here.
- Revisit if no Nova model reaches the gate, if costs exceed the cost model, or if Claude access is set up and a comparison becomes worthwhile.
