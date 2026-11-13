"""Tune the suggestion weight on the DEV split of ESCI, then score the chosen setting once on the held-out sample.

Protocol, fixed before looking at any dev result:
- Dev and held-out queries come from `split_queries` (disjoint by hash). The held-out snapshot and sample are the
  ones already used in `product_intent_eval`; nothing is tuned on them.
- Grid: suggestion weight in WEIGHTS x suggestions per query in LIMITS (weighted reciprocal-rank fusion).
- Selection: highest mean paired nDCG delta over baseline on dev; ties go to the smaller weight, then fewer suggestions.
  A choice only counts as validated on dev if its 95% bootstrap interval is above zero.
- The chosen config is evaluated ONCE on held-out. The report also prints the weight-1 held-out result for reference
  (already known from the previous run) but does not use it for selection.
Dev suggestions are built live (billable; needs --confirm-live; capped). Offline benchmark only; see the module docstring
of `product_intent_eval` for data terms and citation.
"""
from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app.embedding.azure_openai import token_provider_for
from app.evaluation.product_intent_eval import (
    DOMAIN,
    evaluate_configs,
    sample_held_out,
)
from app.evaluation.product_search import Bm25Retriever, EvalQuery, load_queries, split_queries
from app.ingest.models import ProductRecord
from app.ingest.pipeline import verify_subset
from app.intelligence.adapter import IntentProvider
from app.intelligence.chat_provider import ChatIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.precompute import IntentSnapshot, build_snapshot, load_snapshot, save_snapshot

WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)
LIMITS = (1, 3)
SCOPE = "ESCI US 25k subset; weight tuned on dev, scored once on held-out; benchmark direction only"


def memoized(retriever: Callable[[str, int], Sequence[str]]) -> Callable[[str, int], Sequence[str]]:
    cache: dict[tuple[str, int], tuple[str, ...]] = {}

    def run(text: str, k: int) -> Sequence[str]:
        key = (text, k)
        if key not in cache:
            cache[key] = tuple(retriever(text, k))
        return cache[key]

    return run


def choose(grid: Sequence[dict[str, object]]) -> dict[str, object]:
    """Highest dev mean delta; ties to the smaller weight, then fewer suggestions."""
    return sorted(grid, key=lambda row: (-row["mean_delta"], row["weight"], row["limit"]))[0]  # type: ignore[arg-type,return-value]


def dev_grid(retriever: Callable[[str, int], Sequence[str]], dev: Sequence[EvalQuery], snapshot: IntentSnapshot) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for limit in LIMITS:
        for weight in WEIGHTS:
            block = evaluate_configs(retriever, dev, snapshot, weight=weight, limit=limit)["all_sampled"]["with_suggestions"]  # type: ignore[index]
            rows.append(
                {
                    "weight": weight,
                    "limit": limit,
                    "mean_delta": block["mean_delta"],
                    "delta_95ci": block["delta_95ci"],
                    "wins": block["wins"],
                    "losses": block["losses"],
                    "ties": block["ties"],
                }
            )
    return rows


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable[..., IntentProvider] | None = None,
    printer: Callable[[str], None] = print,
) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.product_intent_tune")
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--heldout-snapshot", type=Path, required=True, help="snapshot from product_intent_eval")
    parser.add_argument("--dev-snapshot", type=Path, required=True, help="built live if the file does not exist")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--held-out-fraction", type=float, default=0.3)
    parser.add_argument("--endpoint")
    parser.add_argument("--deployment")
    parser.add_argument("--api-key-env")
    parser.add_argument("--max-requests", type=int, default=400)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(argv)

    manifest = verify_subset(args.subset)
    with (args.subset / "products.jsonl").open("r", encoding="utf-8") as handle:
        records = [ProductRecord.model_validate_json(line) for line in handle if line.strip()]
    queries, _dropped = load_queries(args.examples, {r.product_id for r in records}, locale=manifest.locale)
    dev_all, held_all = split_queries(queries, seed=args.seed, held_out_fraction=args.held_out_fraction)
    held = sample_held_out(queries, seed=args.seed, held_out_fraction=args.held_out_fraction, size=args.sample)
    ordered_dev = sorted(dev_all, key=lambda q: q.query_id)
    dev = ordered_dev if args.sample >= len(ordered_dev) else random.Random(args.seed).sample(ordered_dev, args.sample)
    assert not ({q.query_id for q in dev} & {q.query_id for q in held_all}), "dev sample leaked into held-out"
    retriever = memoized(Bm25Retriever(records))

    built_here = False
    if not args.dev_snapshot.exists():
        if not args.confirm_live:
            printer("dev snapshot missing; building it makes billable model calls; pass --confirm-live")
            return 2
        if not (args.endpoint and args.deployment):
            printer("--endpoint and --deployment are required to build the dev snapshot")
            return 2
        key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
        if args.api_key_env and not key:
            printer(f"environment variable {args.api_key_env} is empty or unset")
            return 2
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
                [q.text for q in dev],
                provider,
                source=getattr(provider, "cache_name", "provider"),
                vocabulary=CatalogVocabulary(terms=(r.title for r in records)),
            )
        finally:
            close = getattr(provider, "close", None)
            if close:
                close()
        save_snapshot(snapshot, args.dev_snapshot)
        built_here = True
        printer(f"built dev snapshot in {time.monotonic() - started:.0f}s")
    dev_snapshot = load_snapshot(args.dev_snapshot)
    held_snapshot = load_snapshot(args.heldout_snapshot)

    grid = dev_grid(retriever, dev, dev_snapshot)
    chosen = choose(grid)
    validated = float(chosen["delta_95ci"][0]) > 0  # type: ignore[index]
    final = evaluate_configs(retriever, held, held_snapshot, weight=float(chosen["weight"]), limit=int(chosen["limit"]))  # type: ignore[arg-type]
    reference = evaluate_configs(retriever, held, held_snapshot, weight=1.0, limit=3)
    report = {
        "scope": SCOPE,
        "dev_queries": len(dev),
        "dev_queries_with_suggestions": sum(1 for q in dev if " ".join(q.text.casefold().split()) in dev_snapshot.entries),
        "dev_snapshot_built_now": built_here,
        "dev_grid": grid,
        "chosen_on_dev": chosen,
        "chosen_validated_on_dev": validated,
        "heldout_queries": len(held),
        "heldout_chosen_config": final["all_sampled"],
        "heldout_weight1_reference": reference["all_sampled"]["with_suggestions"],  # type: ignore[index]
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
