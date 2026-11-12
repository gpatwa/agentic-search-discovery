import json
from pathlib import Path

import pytest

from app.evaluation.intent_eval import SCOPE, build_intent_report, load_query_texts, main
from app.intelligence.adapter import BoundedIntentAdapter, ScriptedIntentMode, ScriptedIntentProvider
from app.intelligence.cache import CachedIntentProvider
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.telemetry import (
    CacheOutcome,
    IntentOutcome,
    ObservedIntentAdapter,
    bucket_latency,
)
from app.telemetry.discovery import LatencyBucket

QUERIES = Path(__file__).parent / "fixtures" / "golden" / "queries.json"


def test_records_outcome_cache_cost_and_never_the_query() -> None:
    cache = CachedIntentProvider(ScriptedIntentProvider(), provider_name="scripted")
    observed = ObservedIntentAdapter(
        BoundedIntentAdapter(cache), cache=cache, cost_per_provider_call_usd=0.002
    )

    observed.resolve("secret query text")
    observed.resolve("secret query text")

    first, second = observed.records
    assert (first.cache, second.cache) == (CacheOutcome.MISS, CacheOutcome.HIT)
    assert (first.estimated_cost_usd, second.estimated_cost_usd) == (0.002, 0.0)
    assert first.outcome is IntentOutcome.PROVIDER_OK
    assert "secret" not in json.dumps([record.model_dump(mode="json") for record in observed.records])


def test_fallback_outcomes_and_guardrail_drops_are_recorded() -> None:
    model_off = ObservedIntentAdapter(BoundedIntentAdapter())
    model_off.resolve("racing")
    assert model_off.records[0].outcome is IntentOutcome.MODEL_OFF
    assert model_off.records[0].cache is CacheOutcome.NONE

    for mode, outcome in (
        (ScriptedIntentMode.TIMEOUT, IntentOutcome.PROVIDER_TIMEOUT),
        (ScriptedIntentMode.MALFORMED, IntentOutcome.PROVIDER_INVALID),
    ):
        observed = ObservedIntentAdapter(BoundedIntentAdapter(ScriptedIntentProvider(mode)))
        observed.resolve("racing")
        assert observed.records[0].outcome is outcome

    guarded = ObservedIntentAdapter(
        BoundedIntentAdapter(ScriptedIntentProvider(expansions=("zzz unrelated",)), vocabulary=CatalogVocabulary())
    )
    guarded.resolve("racing")
    assert guarded.records[0].guardrail_dropped == 1


def test_records_are_bounded_and_summary_is_consistent() -> None:
    observed = ObservedIntentAdapter(BoundedIntentAdapter(), max_records=3)
    for _ in range(5):
        observed.resolve("racing")

    assert len(observed.records) == 3
    summary = observed.summary()
    assert summary["requests"] == 3
    assert summary["estimated_cost_usd_per_1k"] == 0.0
    with pytest.raises(ValueError):
        ObservedIntentAdapter(BoundedIntentAdapter(), cost_per_provider_call_usd=-1)


def test_latency_buckets() -> None:
    assert bucket_latency(0) is LatencyBucket.LT_10_MS
    assert bucket_latency(10) is LatencyBucket.MS_10_TO_49
    assert bucket_latency(99.9) is LatencyBucket.MS_50_TO_99
    assert bucket_latency(100) is LatencyBucket.MS_100_TO_249
    assert bucket_latency(999) is LatencyBucket.MS_250_TO_999
    assert bucket_latency(5000) is LatencyBucket.GE_1000_MS


def test_golden_intent_report_is_labelled_and_internally_consistent() -> None:
    texts = load_query_texts(QUERIES)
    report = build_intent_report(texts)
    configs = {config["name"]: config for config in report["configs"]}

    assert report["scope"] == SCOPE and "no model" in SCOPE
    assert report["queries"] == len(texts) == 30
    assert all(config["preserved_parser_meaning_rate"] == 1.0 for config in configs.values())

    assert configs["model_off"]["outcomes"] == {"model_off": 30}
    assert configs["scripted"]["outcomes"] == {"provider_ok": 30}
    assert configs["scripted_timeout"]["outcomes"] == {"provider_timeout": 30}
    assert configs["scripted_malformed"]["outcomes"] == {"provider_invalid": 30}

    noisy = configs["scripted_noisy_with_guardrail"]
    assert noisy["outcomes"] == {"provider_ok": 30}
    assert noisy["guardrail_dropped"] == 60  # two off-domain suggestions dropped per query

    cached = configs["scripted_cached_two_passes"]
    assert cached["requests"] == 60
    assert cached["provider_calls"] == len({" ".join(text.casefold().split()) for text in texts})
    assert cached["cache"]["hit"] >= 30


def test_cli_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    messages: list[str] = []

    assert main(["--queries", str(QUERIES), "--out", str(out)], printer=messages.append) == 0

    assert json.loads(out.read_text())["scope"] == SCOPE
    assert messages == [f"wrote {out}"]
