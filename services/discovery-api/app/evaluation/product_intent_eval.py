"""Relevance of intent suggestions on a real ESCI product subset: baseline BM25 vs BM25 + suggestions.

Offline benchmark only (docs/DISCOVERY_ESCI_SCOPE.md): the ESCI files and subset stay in an untracked local path.
Cite Reddy et al. (2022), arXiv:2206.06588. Not Walmart or Amazon, and not Discovery's experience ranking.

Pipeline: take a deterministic sample of HELD-OUT queries (queries never used for tuning), build a snapshot of
suggestions for them (live model: billable, needs --confirm-live, capped), then score three configurations with the
same graded nDCG as `product_search`:
  baseline        BM25 on the query
  with_suggestions  reciprocal-rank fusion (k=60, equal weights) of BM25 on the query and on up to 3 suggestions
                    (top 10 each), the same shape as the search wiring
  control         the same fusion with suggestions attached to the WRONG queries (rotated by one)
Reports the paired nDCG difference with a bootstrap interval. Unjudged products count as grade 0, and most of the
top 10 is unjudged on a 25k subset, so absolute numbers are conservative; compare configurations, not to the literature.
"""
from __future__ import annotations

import json
import os
import random
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.embedding.azure_openai import token_provider_for
from app.evaluation.product_search import (
    DEFAULT_GAINS,
    Bm25Retriever,
    EvalQuery,
    load_queries,
    ndcg_at_k,
    split_queries,
)
from app.ingest.models import ProductRecord
from app.ingest.pipeline import verify_subset
from app.intelligence.adapter import IntentProvider
from app.intelligence.cache import normalize_query
from app.intelligence.chat_provider import ChatIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.precompute import IntentSnapshot, SnapshotEntry, build_snapshot

SCOPE = "ESCI US product subset, held-out query sample, BM25; benchmark direction only; not a Discovery ranking claim"
RRF_K = 60
MAX_SUGGESTIONS = 3
SUGGESTION_DEPTH = 10
DOMAIN = "retail products"


def suggestions_for(entry: SnapshotEntry | None) -> list[str]:
    if entry is None:
        return []
    return list(dict.fromkeys((*entry.expansions, *(item.text for item in entry.rewrites))))[:MAX_SUGGESTIONS]


def fused_ranking(retriever: Callable[[str, int], Sequence[str]], text: str, suggestions: Sequence[str], k: int) -> list[str]:
    """RRF over the original query (full depth) and each suggestion (top `SUGGESTION_DEPTH`)."""
    scores: dict[str, float] = defaultdict(float)
    order: dict[str, int] = {}
    lists = [list(retriever(text, max(k, 50)))] + [list(retriever(s, SUGGESTION_DEPTH))[:SUGGESTION_DEPTH] for s in suggestions]
    for ranked in lists:
        for rank, pid in enumerate(dict.fromkeys(ranked), start=1):
            scores[pid] += 1.0 / (RRF_K + rank)
            order.setdefault(pid, len(order))
    return sorted(scores, key=lambda pid: (-scores[pid], order[pid]))[:k]


def paired_bootstrap(deltas: Sequence[float], *, seed: int = 7, draws: int = 2000) -> tuple[float, float]:
    """95% interval for the mean paired difference."""
    if not deltas:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def evaluate_configs(
    retriever: Callable[[str, int], Sequence[str]],
    queries: Sequence[EvalQuery],
    snapshot: IntentSnapshot,
    *,
    k: int = 10,
) -> dict[str, object]:
    entries = snapshot.entries
    keys = sorted(entries)
    rotated = {key: entries[keys[(i + 1) % len(keys)]] for i, key in enumerate(keys)} if keys else {}

    def run(source: Mapping[str, SnapshotEntry]) -> list[float]:
        return [
            ndcg_at_k(
                fused_ranking(retriever, q.text, suggestions_for(source.get(normalize_query(q.text))), k),
                q.judgments,
                DEFAULT_GAINS,
                k,
            )
            for q in queries
        ]

    baseline = [ndcg_at_k(list(retriever(q.text, k)), q.judgments, DEFAULT_GAINS, k) for q in queries]
    variant, control = run(entries), run(rotated)
    n = len(queries)
    covered = [i for i, q in enumerate(queries) if normalize_query(q.text) in entries]

    def block(values: Sequence[float], indices: Sequence[int]) -> dict[str, object]:
        deltas = [values[i] - baseline[i] for i in indices]
        low, high = paired_bootstrap(deltas)
        return {
            "queries": len(indices),
            "baseline_ndcg": round(sum(baseline[i] for i in indices) / len(indices), 4) if indices else 0.0,
            "ndcg": round(sum(values[i] for i in indices) / len(indices), 4) if indices else 0.0,
            "mean_delta": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
            "delta_95ci": [round(low, 4), round(high, 4)],
            "wins": sum(1 for d in deltas if d > 1e-9),
            "losses": sum(1 for d in deltas if d < -1e-9),
            "ties": sum(1 for d in deltas if abs(d) <= 1e-9),
        }

    everything = list(range(n))
    return {
        "k": k,
        "queries_sampled": n,
        "queries_with_suggestions": len(covered),
        "all_sampled": {"with_suggestions": block(variant, everything), "control_shuffled": block(control, everything)},
        "only_queries_with_suggestions": {
            "with_suggestions": block(variant, covered),
            "control_shuffled": block(control, covered),
        },
    }


def sample_held_out(queries: Sequence[EvalQuery], *, seed: int, held_out_fraction: float, size: int) -> list[EvalQuery]:
    """A deterministic sample of held-out queries only (never dev queries)."""
    _dev, held = split_queries(queries, seed=seed, held_out_fraction=held_out_fraction)
    ordered = sorted(held, key=lambda q: q.query_id)
    return ordered if size >= len(ordered) else random.Random(seed).sample(ordered, size)


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable[..., IntentProvider] | None = None,
    printer: Callable[[str], None] = print,
) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.product_intent_eval")
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--snapshot-out", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--held-out-fraction", type=float, default=0.3)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--api-key-env")
    parser.add_argument("--max-requests", type=int, default=400)
    parser.add_argument("--confirm-live", action="store_true", help="required: billable model calls")
    args = parser.parse_args(argv)
    if not args.confirm_live:
        printer("refusing to run: this makes billable model calls; pass --confirm-live")
        return 2
    key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    if args.api_key_env and not key:
        printer(f"environment variable {args.api_key_env} is empty or unset")
        return 2

    manifest = verify_subset(args.subset)
    with (args.subset / "products.jsonl").open("r", encoding="utf-8") as handle:
        records = [ProductRecord.model_validate_json(line) for line in handle if line.strip()]
    queries, dropped = load_queries(args.examples, {r.product_id for r in records}, locale=manifest.locale)
    sample = sample_held_out(queries, seed=args.seed, held_out_fraction=args.held_out_fraction, size=args.sample)
    retriever = Bm25Retriever(records)
    vocabulary = CatalogVocabulary(terms=(r.title for r in records))

    factory = provider_factory or ChatIntentProvider
    provider = factory(
        args.endpoint,
        args.deployment,
        **({"api_key": key} if key else {"token_provider": token_provider_for("az_cli")}),
        max_requests=args.max_requests,
        domain=DOMAIN,
    )
    started = time.monotonic()
    try:
        snapshot = build_snapshot(
            [q.text for q in sample], provider, source=getattr(provider, "cache_name", "provider"), vocabulary=vocabulary
        )
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()
    args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
    from app.intelligence.precompute import save_snapshot  # noqa: PLC0415

    save_snapshot(snapshot, args.snapshot_out)

    report = {
        "scope": SCOPE,
        "dataset": "Amazon Shopping Queries (ESCI); cite Reddy et al. 2022, arXiv:2206.06588",
        "subset_size": manifest.selected,
        "locale": manifest.locale,
        "judgments_dropped": dropped,
        "snapshot_source": snapshot.source,
        "snapshot_entries_kept": len(snapshot.entries),
        "http_requests_sent": getattr(provider, "requests_sent", None),
        "build_seconds": round(time.monotonic() - started, 1),
        "evaluation": evaluate_configs(retriever, sample, snapshot),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
