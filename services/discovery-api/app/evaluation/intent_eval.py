"""Intent-layer evaluation on the golden queries, with fake providers only.

This measures plumbing: validity, fallback rate, parser-meaning preservation, guardrail drops,
cache hit rate and provider calls. It does NOT measure model quality or retrieval quality; every
provider here is scripted, so nothing in the report says anything about a real model.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.intelligence.adapter import BoundedIntentAdapter, ScriptedIntentMode, ScriptedIntentProvider
from app.intelligence.cache import CachedIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.intent import IntentRewrite, RewriteKind
from app.intelligence.telemetry import ObservedIntentAdapter
from app.query.parser import parse_query

SCOPE = "plumbing only: scripted fake providers; no model or retrieval quality is measured"
# A scripted provider that mixes one on-domain and one off-domain suggestion into every answer.
_NOISY_EXPANSIONS = ("racing adventure", "quarterly tax filing")
_NOISY_REWRITES = (
    IntentRewrite(kind=RewriteKind.SYNONYM, text="puzzle game"),
    IntentRewrite(kind=RewriteKind.BROADER, text="zzz unrelated"),
)


def load_query_texts(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        return [str(record["text"]) for record in json.load(handle)]


def _run(name: str, observed: ObservedIntentAdapter, texts: Sequence[str], *, passes: int = 1) -> dict[str, object]:
    preserved = 0
    total = 0
    for _ in range(passes):
        for text in texts:
            result = observed.resolve(text)
            total += 1
            preserved += int(result.intent.preserves(parse_query(text)))
    summary = dict(observed.summary())
    summary.update(name=name, preserved_parser_meaning_rate=preserved / total if total else 0.0)
    if observed.cache is not None:
        summary["provider_calls"] = getattr(observed.cache.provider, "calls", None)
    return summary


def build_intent_report(texts: Sequence[str]) -> Mapping[str, object]:
    vocabulary = CatalogVocabulary()
    configs: list[dict[str, object]] = []

    configs.append(_run("model_off", ObservedIntentAdapter(BoundedIntentAdapter()), texts))

    scripted = ScriptedIntentProvider(expansions=("racing adventure",))
    configs.append(_run("scripted", ObservedIntentAdapter(BoundedIntentAdapter(scripted)), texts))

    noisy = ScriptedIntentProvider(expansions=_NOISY_EXPANSIONS, rewrites=_NOISY_REWRITES)
    configs.append(
        _run("scripted_noisy_with_guardrail", ObservedIntentAdapter(BoundedIntentAdapter(noisy, vocabulary=vocabulary)), texts)
    )

    for label, mode in (("scripted_timeout", ScriptedIntentMode.TIMEOUT), ("scripted_malformed", ScriptedIntentMode.MALFORMED)):
        configs.append(_run(label, ObservedIntentAdapter(BoundedIntentAdapter(ScriptedIntentProvider(mode))), texts))

    # Same traffic twice through a cache: the second pass should be served almost entirely from it.
    inner = ScriptedIntentProvider(expansions=("racing adventure",))
    cache = CachedIntentProvider(inner, provider_name="scripted")
    configs.append(
        _run("scripted_cached_two_passes", ObservedIntentAdapter(BoundedIntentAdapter(cache), cache=cache), texts, passes=2)
    )

    return {"scope": SCOPE, "queries": len(texts), "configs": configs}


def main(argv: Sequence[str] | None = None, *, printer: Callable[[str], None] = print) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.intent_eval")
    parser.add_argument("--queries", type=Path, required=True, help="golden queries.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_intent_report(load_query_texts(args.queries))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
