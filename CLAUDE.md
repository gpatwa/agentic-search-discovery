# Claude Code Repository Guide: agentic-search-discovery

## Start Here

Before changing files, inspect `git status -sb`, the current branch and recent commits. This repository may contain the
owner's work; never discard or overwrite unrelated changes. Read `docs/handoffs/CLAUDE_CODE_HANDOFF.md` for the current state.

## Boundaries

- This repository is one product: Hybrid (lexical and vector) search and personalised discovery over OpenSearch, with model use optional and off by default and an LLM-ready safety boundary. The agentic capability is the roadmap, not a current feature.
- Do not import code from the sibling products. They live in separate repositories:
- `agentic-data-analytics`
- `agentic-resolution-intelligence`
- Local fake providers are the default. Live OpenSearch, Azure and cloud changes need the owner's explicit request in the task.
- Say only what the code supports about AI: model use is optional and off by default.

## Safety and workflow

- Do not deploy to Azure, mutate cloud resources, connect to a live warehouse or run live validation unless the owner
  explicitly asks in the current task. Local fake-provider verification is the default.
- Human gates (security, policy, thresholds, licensing, final go/no-go) need the owner's explicit approval. Never infer approval.
- Never set GitHub secrets or print credentials; never commit `backend.hcl`, `terraform.tfvars`, `secrets.auto.tfvars` or plan files.
- Merge your own pull requests once every check is green (`gh pr merge <n> --auto --merge`).
- Do not claim a test or evaluation passed unless you ran it.

## Useful commands

```bash
cd services/discovery-api && PYTHONPATH=. python3 -m pytest tests -q
ruff check <changed-python-files>
git diff --check
```
