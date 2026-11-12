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

## Stage 2 result (run 2026-11-12, owner-approved)

One offline run of `python -m app.evaluation.intent_live_eval`: deployment `gpt-6-luna` on `compass-pilot-openai`, the 30
synthetic golden queries, account key read into process memory only (the Azure CLI identity got HTTP 401 because the Owner
role has no data-plane access; no role was changed). Nothing was served.

| Bar (G3) | Result | Met? |
|---|---|---|
| Contract violations served | 0: every answer went through strict validation and the guardrail; parser meaning preserved on 30/30 | yes |
| Fallback rate at most 5% | 1 of 30 (3.3%), reason `provider_invalid` | yes |
| p95 latency under 1.5 s | p50 3.7 s, p95 9.8 s | **no** |
| Relevance on the ESCI subset | not run: no ESCI data is present locally and downloading it was not approved | open |

Other facts: 41 HTTP requests for 30 queries (the extra 11 were retries; the status codes were not recorded); the guardrail
dropped 77 of 266 suggestions (29%). Cost is not computed because no verified price is known. This says nothing about
relevance quality, and 30 synthetic queries are a small sample.

**Reading.** The live path is safe at the contract level but too slow for the query path as configured. Per G6 this does not
move to shadow or serving. Options for the owner: a faster deployment or lower reasoning effort (unverified), or the
Instacart-style split: precompute intents for head queries offline into the cache and serve only cache hits online.

## Offline head-query precompute (built 2026-11-12, owner-selected route)

`app/intelligence/precompute.py` and `python -m app.evaluation.intent_precompute`: pick the most frequent normalised
queries from a log, run each once through the live provider plus strict validation and the catalogue guardrail, and save a
snapshot (JSON, per query only `expansions` and `rewrites`). `SnapshotIntentProvider` serves it with no model and no
network; a missing query raises `IntentMiss`, the adapter falls back to the deterministic intent, and telemetry records
`provider_miss`. The served intent is rebuilt from the real query's parse, so an entry cannot alter parser-owned meaning,
and every hit is re-validated. Loading refuses a snapshot from another intent version. A snapshot holds query text and
model suggestions: handle it like the query log.

Run on the 30 synthetic golden queries, with the first 15 acting as the "log" and `gpt-6-luna` as the model (account key in
process memory only, 15 HTTP requests, no retries):

| Measure | Result |
|---|---|
| Head queries kept in the snapshot | 15 of 15 |
| Hit rate over all 30 queries | 0.50 (all hits are the 15 head queries) |
| Hit rate on the 15 held-out queries | 0.00 (they fall back to the deterministic path, as designed) |
| Serve latency from the snapshot | p50 0.10 ms, p95 0.19 ms (in-process, excludes network and the API) |
| Model calls while serving | 0 |
| Parser meaning preserved | 30 of 30 |

So the latency bar (p95 under 1.5 s) is met for served hits, because the model is off the query path. What this does not
show: the golden queries are all distinct, so the "head" here is just the first half and the 0.50 hit rate is an artefact
of that split, not a traffic estimate. A real hit rate needs a real query log. Relevance is still unmeasured (G3), and the
snapshot is not wired into the API routes. Shadow mode and serving remain gated (G6).

## Wired into search (2026-11-12, owner-requested; off by default)

Set `DISCOVERY_INTENT_SNAPSHOT_PATH` to a snapshot file and `/v1/search` adds up to 3 extra lexical sources (named
`intent_lexical_1..3`, top 10 each) for the precomputed suggestions of that query, fused with the normal source by the
existing fusion. Unset (the default), nothing changes. Properties:

- No model call while serving; the query path only reads the loaded snapshot.
- Extras use the same retriever, eligibility and blocked-ID handling as the main source; suggestions come from the validated,
  guardrailed adapter, so they cannot widen what a caller may see.
- A missing or invalid snapshot file is logged and ignored at startup; any adapter error is swallowed per request. Search
  never fails because of this feature.
- The extra source names show in the decision trace's retrieval-stage reason codes; the adapter's outcomes (ok, miss) are
  recorded in-process on `app.state.intent_observer` (no endpoint was added).

Smoke run (not an evaluation): the live-built snapshot loaded over the fictional demo catalog; six head queries returned
`provider_ok` with the three extra sources in the trace, and an unseen query returned `provider_miss` with only the normal
source. Result counts there mean nothing, because the snapshot was built from the golden queries, not this catalog.

**Still unmeasured: whether the suggestions improve relevance.** The extras get equal weight in fusion, so they can change
rankings. Per G6 this stays off until the owner sets thresholds and a relevance comparison (ESCI subset or a real query log)
shows it is better. README claims are unchanged.

## Relevance evaluation on the golden corpus (2026-11-12)

`python -m app.evaluation.intent_relevance`: runs the real `LocalSearchService` over the golden experiences for every golden
query, once as the deterministic baseline and once with a snapshot, and scores both against the golden graded judgments
(k=10). The snapshot is built from query text only (`gpt-6-luna`, 30 live requests plus 7 retries, 27 of 30 queries got an
entry), never from judgments. A control attaches the same suggestions to the wrong queries (values rotated by one).

27 queries have at least one relevant judgment and were scored.

| Metric @10 | Baseline | With suggestions | Delta | Wins / losses / ties | Control (shuffled) delta |
|---|---|---|---|---|---|
| nDCG | 0.377 | 0.567 | +0.191 | 14 / 3 / 10 | -0.000 |
| Recall | 0.451 | 0.698 | +0.247 | 11 / 2 / 14 | +0.080 |
| MRR | 0.370 | 0.577 | +0.208 | 13 / 2 / 12 | -0.003 |

Reading: on this corpus the suggestions help, and the gain is not just "an extra source changes the ranking": the shuffled
control leaves nDCG and MRR flat (recall rises slightly, which is plain broadening). Two categories got clearly worse
(typo and near-duplicate-suppression, nDCG delta -1.0 each: the extra sources pull in the very near-duplicates or
misspelling matches the baseline avoided).

**What this is not.**
- It is not the ESCI comparison from G3: no ESCI data is present and the download was not approved.
- 27 scored queries and 62 judgments (unjudged results count as grade 0) on a roughly 45-document synthetic catalogue,
  with queries and judgments written by the same hand as the fixtures. It shows direction on this corpus, not a quality
  claim, and no significance test was run.
- The G3 safety bars were met earlier except live latency, which the snapshot design removes from the query path. The
  numeric relevance pass bar was never set, so there is no pass or fail yet.
- README claims stay unchanged and the wiring stays off by default until you decide (G6, G7).
