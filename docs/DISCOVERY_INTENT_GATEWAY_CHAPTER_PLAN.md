# Chapter plan: a measured intent layer (cache, guardrails, evaluation)

Status: **proposal for the owner's review.** Nothing here is built or approved. Written 2026-11-11.

Model use stays optional and off by default. This chapter makes no AI claim the code does not support: today the only
model call is Azure OpenAI embeddings, and the intent provider is a scripted fake.

## Why this chapter

Two production write-ups point at the same shape: keep the model behind a narrow interface, and put a cache, guardrails
and measurement around it before turning a live model on.

- **Instacart** ([Building the Intent Engine](https://company.instacart.com/tech-innovation/building-the-intent-engine-how-instacart-is-revamping-query-understanding-with-llms), 2025-11-21):
  a head-query cache filled by an offline, context-rich pipeline; a small fine-tuned model for the long tail, routed by
  cache hit; guardrails that validate output against the catalogue taxonomy; specific prompts per rewrite type instead of
  one vague prompt. They report rewrite coverage rising from about 50% to over 95% (90%+ precision), and a student model
  matching its teacher (F1 95.7% vs 95.8%).
- **DoorDash** ([Decoupled Search Grounding](https://careersatdoordash.com/blog/how-doordash-reduced-llm-search-costs-by-98-without-losing-accuracy/), 2026-10-29):
  a gateway with exact and semantic caches, a provider fallback chain, per-request telemetry, and structured output. Their
  98% saving is search-provider cost only (model inference excluded), and native search still won on freshness.

Other sources (Walmart distillation, MiniELM, a Taobao synthetic-data paper) were seen only as titles or summaries and are
not relied on here.

**What does not transfer.** Our pilot runs a fictional catalogue with no behavioural data. Instacart's conversion-history
context has no equivalent, and DoorDash's web-search grounding does not fit a catalogue product. We do not copy those, and
we make no personalisation or accuracy-gain claim.

## What already exists

- `IntentProvider` protocol and `BoundedIntentAdapter` (`app/intelligence/adapter.py`) treat provider output as untrusted;
  `ScriptedIntentProvider` simulates success, malformed, timeout and injection outcomes.
- `StructuredDiscoveryIntent` (`app/intelligence/intent.py`) is a strict, frozen, `extra="forbid"` contract with bounded
  fields; identity, tenant, safety and eligibility fields are deliberately absent.
- Relevance judge and safety policy contain no model client.
- Golden fixtures and product-search evaluation tests exist (`test_golden_fixtures.py`, `test_product_search_eval.py`).

So the contract and the untrusted-output boundary are largely done. This chapter adds what is missing around them.

## Steps

All steps use fake providers and local data. No network, Azure or OpenSearch change.

1. **Typed intent outputs per task.** Extend the contract only where the evaluation needs it: taxonomy category (if the
   catalogue exposes one) and separate rewrite kinds (substitute, broader, synonym), each bounded and validated.
   Done when: contract tests cover each kind and reject off-contract data.
2. **Catalogue guardrail.** Reject or flag intent output whose category or attribute values are not in the catalogue
   vocabulary. Done when: tests show a hallucinated value is dropped and the deterministic parse is used instead.
3. **Head-query cache behind `IntentProvider`.** Normalised-query exact cache with a TTL, scoped per provider and
   intent version, so a version bump never serves stale shapes. No semantic cache yet. Done when: tests show hit, miss, expiry
   and version isolation, and that a cache hit never bypasses validation.
4. **Telemetry.** Per request: cache outcome, provider, latency, outcome class (ok, malformed, timeout, rejected) and an
   estimated-cost field that is zero for fakes. Respect the existing privacy and redaction rules: no raw tenant data in logs.
5. **Evaluation harness.** On the golden corpus, compare no-model, scripted, and cached configurations: result quality
   metrics already in `app/evaluation`, plus cache hit rate and rejected-output rate. Report only numbers actually run.

Steps 1 to 5 can land as two or three small PRs (contract plus guardrail; cache plus telemetry; evaluation).

## Gated work (owner approval required, separate chapter)

- Any live model provider behind `IntentProvider` (for example Azure OpenAI), including its cost and data-handling review.
- Semantic cache (needs an embedding call and a similarity threshold, which is a policy threshold).
- Offline teacher pipeline and distilling a smaller model: only worthwhile once real query traffic exists.
- Retrieval depth or candidate-count tuning on real data.
- Any README claim change beyond "model use optional and off by default".

## Open questions for the owner

1. Is the priority tail-query understanding (Instacart's problem) or cost and latency control (DoorDash's)? There is no
   model cost to reduce yet, so this plan leans to the first.
2. Does the catalogue have a taxonomy we can validate against, or should step 2 use brand and attribute vocabularies only?
3. Should live-model work be a separate chapter that needs its own explicit go-ahead? This plan assumes yes.

## Risks and caveats

- Fake-provider results prove the plumbing, not model quality. Evaluation numbers from this chapter must be labelled that way.
- A cache can hide a provider regression; telemetry and version scoping exist to keep that visible.
- Third-party figures above are the vendors' own and were not reproduced here.
