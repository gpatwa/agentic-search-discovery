"""Stage 2 of the live-model chapter: run the chat provider on a query file, offline, never serving.

A BILLABLE live run. It refuses to start without `--confirm-live`, uses the Azure CLI identity by default
(or a key read from a named environment variable; never a command-line value), and stops at `--max-requests`. The report holds counts and latencies only: no query text and
no model output. It measures the safety bars (contract violations, fallback rate, latency); it makes no
claim about relevance quality.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app.embedding.azure_openai import token_provider_for
from app.evaluation.intent_eval import load_query_texts
from app.intelligence.adapter import BoundedIntentAdapter, IntentProvider
from app.intelligence.chat_provider import ChatIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.query.parser import parse_query

SCOPE = "offline live run on the given query file; safety bars only; no relevance or quality claim"


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def run_live_eval(
    provider: IntentProvider,
    texts: Sequence[str],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    adapter = BoundedIntentAdapter(provider, vocabulary=CatalogVocabulary())
    latencies: list[float] = []
    reasons: dict[str, int] = {}
    served_ok = preserved = dropped = suggestions = 0
    for text in texts:
        started = clock()
        result = adapter.resolve(text)
        latencies.append((clock() - started) * 1000)
        reasons[result.fallback_reason or "provider_ok"] = reasons.get(result.fallback_reason or "provider_ok", 0) + 1
        served_ok += int(not result.used_fallback)
        preserved += int(result.intent.preserves(parse_query(text)))
        dropped += result.dropped_by_guardrail
        suggestions += len(result.intent.expansions) + len(result.intent.rewrites)
    total = len(texts)
    return {
        "scope": SCOPE,
        "queries": total,
        "outcomes": dict(sorted(reasons.items())),
        "fallback_rate": 1 - served_ok / total if total else 0.0,
        "parser_meaning_preserved_rate": preserved / total if total else 0.0,
        "suggestions_kept": suggestions,
        "suggestions_dropped_by_guardrail": dropped,
        "latency_ms_p50": round(percentile(latencies, 0.50), 1),
        "latency_ms_p95": round(percentile(latencies, 0.95), 1),
        "http_requests_sent": getattr(provider, "requests_sent", None),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable[..., IntentProvider] | None = None,
    printer: Callable[[str], None] = print,
) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.intent_live_eval")
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument(
        "--api-key-env",
        help="name of an environment variable holding the account key; default is the Azure CLI identity",
    )
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--confirm-live", action="store_true", help="required: this makes billable calls")
    args = parser.parse_args(argv)
    if not args.confirm_live:
        printer("refusing to run: this makes billable model calls; pass --confirm-live")
        return 2
    factory = provider_factory or ChatIntentProvider
    key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    if args.api_key_env and not key:
        printer(f"environment variable {args.api_key_env} is empty or unset")
        return 2
    provider = factory(
        args.endpoint,
        args.deployment,
        **({"api_key": key} if key else {"token_provider": token_provider_for("az_cli")}),
        max_requests=args.max_requests,
    )
    try:
        report = run_live_eval(provider, load_query_texts(args.queries))
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
