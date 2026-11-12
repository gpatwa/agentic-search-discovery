"""Search with optional precomputed intent suggestions: off by default, bounded, never breaks search."""
import logging
from pathlib import Path

import pytest
from tests.test_search_endpoint import _documents, _request, _user

from app.config import settings_from_env
from app.intelligence.adapter import BoundedIntentAdapter
from app.intelligence.precompute import IntentSnapshot, SnapshotEntry, SnapshotIntentProvider, save_snapshot
from app.intelligence.telemetry import IntentOutcome, ObservedIntentAdapter
from app.main import build_intent_observer
from app.routes.search import LocalSearchService


def _observer(entries: dict[str, SnapshotEntry]) -> ObservedIntentAdapter:
    snapshot = IntentSnapshot(source="test", attempted=len(entries), entries=entries)
    return ObservedIntentAdapter(BoundedIntentAdapter(SnapshotIntentProvider(snapshot)))


def _ids(response) -> list[str]:
    return [item.experience_id for item in response.results]


def test_without_an_adapter_search_is_unchanged() -> None:
    response = LocalSearchService(_documents(), (_user(),)).search(_request(query="mystery"))

    assert _ids(response) == ["exp-001"]
    assert all(not code.startswith("intent_lexical") for code in response.decision_trace.stages[1].reason_codes)


def test_a_precomputed_suggestion_adds_a_bounded_extra_source() -> None:
    observer = _observer({"mystery": SnapshotEntry(expansions=("racing",))})
    service = LocalSearchService(_documents(), (_user(),), intent_adapter=observer)

    response = service.search(_request(query="mystery"))

    assert _ids(response)[0] == "exp-001"  # the original query still leads
    assert "exp-002" in _ids(response)  # found only through the suggestion
    assert "intent_lexical_1" in response.decision_trace.stages[1].reason_codes
    assert [record.outcome for record in observer.records] == [IntentOutcome.PROVIDER_OK]


def test_a_miss_falls_back_to_the_deterministic_result() -> None:
    observer = _observer({"something else": SnapshotEntry(expansions=("racing",))})
    plain = LocalSearchService(_documents(), (_user(),)).search(_request(query="mystery"))

    response = LocalSearchService(_documents(), (_user(),), intent_adapter=observer).search(_request(query="mystery"))

    assert _ids(response) == _ids(plain)
    assert observer.records[0].outcome is IntentOutcome.PROVIDER_MISS


def test_suggestions_respect_blocked_ids() -> None:
    observer = _observer({"mystery": SnapshotEntry(expansions=("racing",))})
    service = LocalSearchService(_documents(), (_user(),), intent_adapter=observer)

    request = _request(query="mystery").model_copy(update={"blocked_ids": ("exp-002",)})
    assert "exp-002" not in _ids(service.search(request))


def test_a_failing_adapter_never_breaks_search() -> None:
    class Broken:
        def resolve(self, raw_query: str):
            raise RuntimeError("boom")

    response = LocalSearchService(_documents(), (_user(),), intent_adapter=Broken()).search(_request(query="mystery"))

    assert response.error is None and _ids(response) == ["exp-001"]


def test_suggestion_count_is_capped() -> None:
    many = tuple(f"racing {word}" for word in ("a", "b", "c", "d", "e"))
    observer = _observer({"mystery": SnapshotEntry(expansions=many)})
    response = LocalSearchService(_documents(), (_user(),), intent_adapter=observer).search(_request(query="mystery"))

    extras = [code for code in response.decision_trace.stages[1].reason_codes if code.startswith("intent_lexical")]
    assert len(extras) == 3


# ---- startup wiring ----


def test_setting_is_off_by_default_and_reads_the_env_var() -> None:
    assert settings_from_env({}).intent_snapshot_path is None
    assert settings_from_env({"DISCOVERY_INTENT_SNAPSHOT_PATH": "/x/s.json"}).intent_snapshot_path == "/x/s.json"
    assert build_intent_observer(None) is None


def test_observer_loads_a_valid_snapshot_and_ignores_a_bad_one(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    good = tmp_path / "good.json"
    save_snapshot(IntentSnapshot(source="t", attempted=1, entries={"mystery": SnapshotEntry(expansions=("racing",))}), good)
    assert build_intent_observer(str(good)) is not None

    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    with caplog.at_level(logging.WARNING):
        assert build_intent_observer(str(bad)) is None
        assert build_intent_observer(str(tmp_path / "missing.json")) is None
    assert "search stays deterministic" in caplog.text
