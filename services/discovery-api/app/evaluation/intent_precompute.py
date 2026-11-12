"""Build a head-query intent snapshot offline, then measure serving from it (no model on the serving path).

`--provider scripted` is local and free. `--provider azure` makes billable model calls (one per head query,
capped by `--max-requests`) and refuses to start without `--confirm-live`. The report holds counts and
latencies only: no query text and no model output. It measures hit rate, fallbacks and serve latency; it makes
no relevance claim.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app.embedding.azure_openai import token_provider_for
from app.evaluation.intent_eval import load_query_texts
from app.evaluation.intent_live_eval import percentile
from app.intelligence.adapter import BoundedIntentAdapter, IntentProvider, ScriptedIntentProvider
from app.intelligence.cache import normalize_query
from app.intelligence.chat_provider import ChatIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.precompute import (
    SnapshotIntentProvider,
    build_snapshot,
    save_snapshot,
    select_head_queries,
)
from app.intelligence.telemetry import ObservedIntentAdapter
from app.query.parser import parse_query

SCOPE = "offline precompute and snapshot serving; hit rate, fallbacks and latency only; no relevance claim"


def evaluate_serving(snapshot_provider: SnapshotIntentProvider, eval_texts: Sequence[str], head: set[str]) -> dict[str, object]:
    observed = ObservedIntentAdapter(
        BoundedIntentAdapter(snapshot_provider, vocabulary=CatalogVocabulary()), clock=time.perf_counter
    )
    latencies: list[float] = []
    preserved = 0
    held_out = held_out_hits = 0
    for text in eval_texts:
        started = time.perf_counter()
        result = observed.resolve(text)
        latencies.append((time.perf_counter() - started) * 1000)
        preserved += int(result.intent.preserves(parse_query(text)))
        if normalize_query(text) not in head:
            held_out += 1
            held_out_hits += int(not result.used_fallback)
    summary = observed.summary()
    total = len(eval_texts)
    return {
        "queries": total,
        "outcomes": summary["outcomes"],
        "hit_rate": 1 - sum(1 for r in observed.records if r.outcome.value != "provider_ok") / total if total else 0.0,
        "held_out_queries": held_out,
        "held_out_hit_rate": held_out_hits / held_out if held_out else None,
        "parser_meaning_preserved_rate": preserved / total if total else 0.0,
        "serve_latency_ms_p50": round(percentile(latencies, 0.5), 3),
        "serve_latency_ms_p95": round(percentile(latencies, 0.95), 3),
        "model_calls_while_serving": 0,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable[..., IntentProvider] | None = None,
    printer: Callable[[str], None] = print,
) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.intent_precompute")
    parser.add_argument("--log", type=Path, required=True, help="query log (JSON list of records with 'text')")
    parser.add_argument("--top", type=int, default=100, help="how many head queries to precompute")
    parser.add_argument("--min-count", type=int, default=1)
    parser.add_argument("--eval-queries", type=Path, help="queries to serve from the snapshot (default: the log)")
    parser.add_argument("--snapshot-out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument("--provider", choices=("scripted", "azure"), default="scripted")
    parser.add_argument("--endpoint")
    parser.add_argument("--deployment")
    parser.add_argument("--api-key-env", help="env var holding the account key; default is the Azure CLI identity")
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--confirm-live", action="store_true", help="required for --provider azure (billable)")
    args = parser.parse_args(argv)

    provider: IntentProvider
    source = "scripted"
    if args.provider == "azure":
        if not args.confirm_live:
            printer("refusing to run: --provider azure makes billable model calls; pass --confirm-live")
            return 2
        if not (args.endpoint and args.deployment):
            printer("--provider azure needs --endpoint and --deployment")
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
        )
        source = getattr(provider, "cache_name", f"azure:{args.deployment}")
    else:
        provider = ScriptedIntentProvider(expansions=("racing adventure",))

    try:
        log_texts = load_query_texts(args.log)
        head = select_head_queries(log_texts, top_n=args.top, min_count=args.min_count)
        snapshot = build_snapshot(head, provider, source=source)
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()
    save_snapshot(snapshot, args.snapshot_out)

    eval_texts = load_query_texts(args.eval_queries) if args.eval_queries else log_texts
    report = {
        "scope": SCOPE,
        "source": source,
        "head_queries_attempted": snapshot.attempted,
        "snapshot_entries_kept": len(snapshot.entries),
        "http_requests_sent": getattr(provider, "requests_sent", None),
        "serving": evaluate_serving(SnapshotIntentProvider(snapshot), eval_texts, set(head)),
    }
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.snapshot_out} and {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
