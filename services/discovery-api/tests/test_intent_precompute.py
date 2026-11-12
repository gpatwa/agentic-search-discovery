import json
from pathlib import Path

import pytest

from app.evaluation.intent_precompute import SCOPE, main
from app.intelligence.adapter import BoundedIntentAdapter, IntentMiss, ScriptedIntentMode, ScriptedIntentProvider
from app.intelligence.intent import INTENT_VERSION, IntentRewrite, RewriteKind, build_intent
from app.intelligence.precompute import (
    SNAPSHOT_FORMAT,
    IntentSnapshot,
    SnapshotEntry,
    SnapshotError,
    SnapshotIntentProvider,
    build_snapshot,
    load_snapshot,
    save_snapshot,
    select_head_queries,
)
from app.intelligence.telemetry import IntentOutcome, ObservedIntentAdapter

QUERIES = Path(__file__).parent / "fixtures" / "golden" / "queries.json"


def test_head_queries_are_the_most_frequent_normalised_ones() -> None:
    texts = ["Racing", "racing ", "puzzle", "puzzle", "puzzle", "rare one", "  "]

    assert select_head_queries(texts, top_n=2) == ["puzzle", "racing"]
    assert select_head_queries(texts, top_n=5, min_count=2) == ["puzzle", "racing"]
    with pytest.raises(ValueError):
        select_head_queries(texts, top_n=0)


def test_build_keeps_only_accepted_nonempty_answers_and_applies_the_guardrail() -> None:
    provider = ScriptedIntentProvider(
        expansions=("racing adventure", "quarterly tax filing"),
        rewrites=(IntentRewrite(kind=RewriteKind.BROADER, text="zzz unrelated"),),
    )
    snapshot = build_snapshot(["racing", "Racing "], provider, source="scripted")

    assert snapshot.attempted == 1 and list(snapshot.entries) == ["racing"]
    entry = snapshot.entries["racing"]
    assert entry.expansions == ("racing adventure",) and entry.rewrites == ()  # off-domain suggestions dropped

    for mode in (ScriptedIntentMode.TIMEOUT, ScriptedIntentMode.MALFORMED, ScriptedIntentMode.INJECTION):
        assert build_snapshot(["racing"], ScriptedIntentProvider(mode), source="x").entries == {}
    assert build_snapshot(["racing"], ScriptedIntentProvider(), source="x").entries == {}  # nothing to store


def test_snapshot_round_trips_and_writes_atomically(tmp_path: Path) -> None:
    snapshot = build_snapshot(["racing"], ScriptedIntentProvider(expansions=("racing adventure",)), source="scripted")
    path = tmp_path / "sub" / "snap.json"

    save_snapshot(snapshot, path)

    assert load_snapshot(path) == snapshot
    assert [p.name for p in path.parent.iterdir()] == ["snap.json"]  # no temp file left behind


def test_load_refuses_bad_files(tmp_path: Path) -> None:
    good = IntentSnapshot(source="s", attempted=0)
    path = tmp_path / "snap.json"
    for payload in (
        "not json",
        json.dumps({**good.model_dump(mode="json"), "intent_version": "imd-intent-v0"}),
        json.dumps({**good.model_dump(mode="json"), "format": "other"}),
        json.dumps({**good.model_dump(mode="json"), "entries": {"q": {"expansions": ["bad\u0000"], "lexical_text": "x"}}}),
    ):
        path.write_text(payload)
        with pytest.raises(SnapshotError):
            load_snapshot(path)
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path / "missing.json")
    assert good.format == SNAPSHOT_FORMAT and good.intent_version == INTENT_VERSION


def test_serving_hits_misses_and_never_trusts_the_entry() -> None:
    snapshot = IntentSnapshot(
        source="s", attempted=1, entries={"racing": SnapshotEntry(expansions=("racing adventure",))}
    )
    provider = SnapshotIntentProvider(snapshot)
    observed = ObservedIntentAdapter(BoundedIntentAdapter(provider))

    hit = observed.resolve("  Racing ")
    miss = observed.resolve("something never seen")

    assert hit.used_fallback is False and hit.intent.expansions == ("racing adventure",)
    assert miss.used_fallback is True and miss.fallback_reason == "provider_miss"
    assert [r.outcome for r in observed.records] == [IntentOutcome.PROVIDER_OK, IntentOutcome.PROVIDER_MISS]
    assert (provider.hits, provider.misses) == (1, 1)
    with pytest.raises(IntentMiss):
        provider.generate("nope")


def test_entry_cannot_change_parser_owned_meaning() -> None:
    snapshot = IntentSnapshot(source="s", attempted=1, entries={"lantern harbor": SnapshotEntry(expansions=("x game",))})
    result = BoundedIntentAdapter(SnapshotIntentProvider(snapshot)).resolve("Lantern Harbor")

    assert result.used_fallback is False
    assert result.intent.preserves(build_intent("Lantern Harbor"))


def test_cli_scripted_run_reports_serving_from_a_held_out_split(tmp_path: Path) -> None:
    log = tmp_path / "log.json"
    texts = json.loads(QUERIES.read_text())
    log.write_text(json.dumps(texts[:15]))
    messages: list[str] = []

    code = main(
        [
            "--log", str(log), "--top", "15", "--eval-queries", str(QUERIES),
            "--snapshot-out", str(tmp_path / "snap.json"), "--report-out", str(tmp_path / "rep.json"),
        ],
        printer=messages.append,
    )

    report = json.loads((tmp_path / "rep.json").read_text())
    serving = report["serving"]
    assert code == 0 and report["scope"] == SCOPE and len(texts) == 30
    assert report["snapshot_entries_kept"] > 0 and report["http_requests_sent"] is None
    assert serving["model_calls_while_serving"] == 0 and serving["queries"] == 30
    assert serving["held_out_queries"] > 0 and serving["held_out_hit_rate"] == 0.0  # tail falls back
    assert serving["hit_rate"] > 0 and serving["parser_meaning_preserved_rate"] == 1.0
    assert "text" not in (tmp_path / "rep.json").read_text().replace('"scope"', "")


def test_cli_azure_needs_confirmation_endpoint_and_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = ["--log", str(QUERIES), "--snapshot-out", str(tmp_path / "s.json"), "--report-out", str(tmp_path / "r.json"), "--provider", "azure"]
    messages: list[str] = []

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider must not be built")

    assert main(base, provider_factory=boom, printer=messages.append) == 2 and "--confirm-live" in messages[-1]  # type: ignore[arg-type]
    assert main([*base, "--confirm-live"], provider_factory=boom, printer=messages.append) == 2  # type: ignore[arg-type]
    monkeypatch.delenv("PRECOMPUTE_KEY", raising=False)
    args = [*base, "--confirm-live", "--endpoint", "https://x", "--deployment", "d", "--api-key-env", "PRECOMPUTE_KEY"]
    assert main(args, provider_factory=boom, printer=messages.append) == 2 and "PRECOMPUTE_KEY" in messages[-1]  # type: ignore[arg-type]
    assert not (tmp_path / "s.json").exists()
