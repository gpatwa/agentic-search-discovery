# Chapter plan: a live intent model, behind gates

Status: **proposal for the owner's review.** Nothing here is built, approved or run. Written 2026-11-12.
It follows `docs/DISCOVERY_INTENT_GATEWAY_CHAPTER_PLAN.md`, whose steps 1 to 5 are merged (typed rewrites, catalogue
guardrail, head-query cache, telemetry, fake-provider evaluation).

Model use stays optional and off by default. Today the only live model call is Azure OpenAI embeddings. Nothing in this
plan changes a README claim; that is gate G7 below.

## Goal

Put one real model behind the existing `IntentProvider` interface, prove on evidence that it helps without breaking the
safety boundary, and keep the deterministic path as the fallback and kill-switch target. "Helps" is defined before any
live call, not after (gate G3).

## What already exists (verified in the repo)

- `IntentProvider` and `BoundedIntentAdapter`: provider output is untrusted, validated, and checked to leave parser-owned
  meaning and caller catalog IDs unchanged; any failure falls back to the deterministic intent.
- Guardrail, cache and telemetry from the previous chapter; an evaluation harness that so far only exercises fakes.
- `IntelligenceSafetyPolicy`: kill switch, input and output token budgets, redaction, prompt-injection quarantine.
- Config gates: `no_model=True` by default; model-backed features (`features.llm`, `learned`, Azure embeddings) refuse to
  start unless `no_model=False`. Azure auth modes `key`, `managed_identity`, `az_cli` already exist for embeddings.
- `app/embedding/azure_openai.py` is the pattern to copy: hard cap on calls per process, bounded retries on throttling only,
  errors that carry the HTTP status and never the input, construction makes no network call.
- ADR-IMD-076 already says LLM use is offline or shadow only, never creates candidates, and falls back on timeout,
  malformed output, injection or kill switch.

## Owner decisions recorded 2026-11-12 (selections made in session; not infrastructure approvals)

| Gate | Decision |
|---|---|
| G1 | Reuse `compass-pilot-openai` for now (crosses the Compass product boundary, as the embeddings already do). |
| G2 | The redacted query text may be sent, for the pilot only; no user, tenant or session identifiers. Note: the shared safety policy redacts secret-shaped values only, not emails or phone numbers. |
| G3 | Guardrail and safety bars first: 0 contract violations served, fallback rate at most 5%, p95 latency under 1.5 s, relevance comparison on the ESCI subset. No quality claim from the 30 synthetic queries. |
| G4/G5 | Live calls approved for stage 2 (offline evaluation script, not serving), ceiling about USD 5. Not approved: shadow mode, serving (G6), README claim changes (G7). |

## Stages

Each stage ends with a PR that CI has passed. Stages 2 to 4 also need the owner's go-ahead in the session where they run;
CLAUDE.md forbids live validation and cloud changes otherwise.

**Stage 0: decisions (owner).** Answer the open questions below and record the answers in this file.

**Stage 1: provider code, fakes only (no approval needed).** A `ChatIntentProvider` implementing `IntentProvider` over an
injected HTTP client, so tests use a fake transport.
- Prompts are versioned constants, one per rewrite kind (synonym, broader, substitute), asking for strict JSON that matches
  `StructuredDiscoveryIntent`. The user's query goes in as quoted data, never concatenated into instructions.
- Input passes through `IntelligenceSafetyPolicy.inspect` first; output through `inspect_output`, then the adapter's own
  validation and the catalogue guardrail. Reuse, do not duplicate.
- Hard caps: calls per process, output tokens per call, a per-process spend ceiling, a timeout, bounded retries on 429/5xx
  only. Errors carry status only, never the query or response body.
- Wiring behind `no_model=False` and a new explicit flag, default off; the kill switch wins over everything.
- Tests: fake transport covering success, malformed JSON, over-long output, injection text in the query, timeout, 429 then
  success, cap reached, kill switch on, and "no network call at construction". Telemetry cost field uses the configured
  per-call estimate.

**Stage 2: offline evaluation with live calls (owner approval, G1 to G5).** Run the stage 1 provider on an agreed query set
in a script, not in serving. Compare against the deterministic path on the metrics fixed in G3. Output a report; nothing
is served.

**Stage 3: shadow mode (owner approval).** In the pilot, call the model on a sample of real queries, log the comparison with
the redacted telemetry, and serve only the deterministic result. This stage needs the sign-in wall and data handling in G2
settled, because it sees real user text.

**Stage 4: limited serving (owner approval, final go/no-go).** Only if shadow results meet the thresholds from G3. Start
with a small cohort behind the flag, with an automatic rollback on the guardrail-drop or fallback rate leaving a band.

## Gates (human decisions; never inferred)

| # | Gate | Why it is the owner's |
|---|---|---|
| G1 | Which model and which Azure account | `compass-pilot-openai` belongs to the Compass pilot (see `docs/DISCOVERY_STATUS.md`); a Discovery-owned account avoids crossing the product boundary. I have no verified facts about the `gpt-6-luna` deployment's capabilities or price and will not assume any. |
| G2 | Data handling | Query text leaves our boundary to the model service. Decide retention, logging and whether any user or tenant data may be sent; the plan assumes the raw query only. |
| G3 | What "better" means and the thresholds | Metrics, minimum query-set size, and pass bars (for example guardrail-drop rate, fallback rate, p95 latency, spend per 1,000 queries) are policy thresholds. The 30 synthetic golden queries are too few and too synthetic to support a quality claim. |
| G4 | Budget | Per-day and total spend ceiling, and who is paid for it. |
| G5 | Running live calls at all | Per CLAUDE.md, each live run needs an explicit request in that session. |
| G6 | Promotion | Moving from offline to shadow to serving is a go/no-go. |
| G7 | Public claims | README keeps "model use optional and off by default" until the owner approves new wording based on measured results. |

## Cost (not yet estimable)

Cost per 1,000 queries depends on the chosen model's price, prompt length and the cache hit rate. I have no verified price,
so this plan does not state one. Stage 1 makes the estimate computable from telemetry (per-call cost field times miss rate);
stage 2 produces the first real number. The cache matters most here: head queries repeat, and the earlier evaluation shows
how the hit rate is measured.

## Deferred on purpose

- Semantic cache: needs an embedding call per lookup and a similarity threshold (a G3-style policy number). Consider only
  after exact-cache hit rates on real traffic show what is left to gain.
- Distilling a smaller model from the teacher's output: only worthwhile with real traffic and an agreed quality bar.
- Web-search grounding: does not fit a catalogue product.
- Personalised intent: no consented behavioural data exists.

## Risks

- **Prompt injection through the query.** Mitigated by policy inspection, quoted-data prompts, strict output validation and
  the closed contract; still needs adversarial tests before stage 2.
- **Silent quality regression after a model or prompt change.** Version the prompt, key the cache by prompt and model
  version (the cache already keys by intent version and provider name), and re-run the stage 2 evaluation on every change.
- **Spend runaway.** Hard caps in code, a budget in G4, and the kill switch.
- **Misleading evidence.** Fake-provider numbers prove plumbing only; any report from stage 2 onward must say what data it
  used and how large it was.

## Open questions for the owner

1. G1: a Discovery-owned Azure OpenAI account, or reuse the Compass one for now? Which model?
2. G2: is sending the raw query text to the model service acceptable for the pilot, and for how long may it be retained?
3. G3: which metrics and pass bars, and where will a larger labelled query set come from (the public ESCI subset in
   `docs/DISCOVERY_ESCI_SCOPE.md`, or something else)?
4. G4: spend ceiling for stage 2.
5. Do you want stage 1 started now? It needs no live call and no cloud change.
