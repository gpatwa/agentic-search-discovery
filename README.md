# agentic-search-discovery

Hybrid (lexical and vector) search and personalised discovery over OpenSearch, with model use optional and off by default and an LLM-ready safety boundary. The agentic capability is the roadmap, not a current feature.

## What is agentic here

**Not agentic yet.** There is no agent loop. Model use is optional and off by default (`no_model: True`); today the only model call is Azure OpenAI embeddings for vector and hybrid retrieval. The intent adapter has only a scripted provider, and the relevance judge and safety policy contain no model client.

**Roadmap:** a real intent provider behind the existing `IntentProvider` interface, evaluated against the golden corpus, then an agent loop.

**Not claimed:** LLM-powered or agentic search; learned personalisation or engagement gains (no consented behavioural data exists; the pilot runs a fictional catalogue).

## Layout

`services/discovery-api` (contracts in `app/contracts`), `apps/discovery-web`, `infra/terraform/azure-discovery-pilot`, `infra/terraform/github-repo`, `docs/execution/immersive-discovery`.

## Test

```bash
cd services/discovery-api && PYTHONPATH=. python3 -m pytest tests -q
```

## Status

Pilot stage. Nothing here is production, and no compliance certification is claimed. Cloud changes (Terraform apply,
deploys) are made only on the owner's explicit request. See `CLAUDE.md` for the working rules and
`docs/handoffs/CLAUDE_CODE_HANDOFF.md` for the current state.

## Related repositories

This repository was extracted from a monorepo with its history. The products are independent and import nothing from each other:

- `agentic-data-analytics`
- `agentic-resolution-intelligence`

## Licence

MIT (see `LICENSE`).
