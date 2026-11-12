import json
from pathlib import Path

from app.evaluation.intent_live_eval import SCOPE, main, percentile, run_live_eval
from app.intelligence.adapter import ScriptedIntentMode, ScriptedIntentProvider

QUERIES = Path(__file__).parent / "fixtures" / "golden" / "queries.json"


def test_refuses_without_confirmation_and_builds_no_provider(tmp_path: Path) -> None:
    messages: list[str] = []

    def factory(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider must not be built")

    code = main(
        ["--queries", str(QUERIES), "--out", str(tmp_path / "r.json"), "--endpoint", "https://x", "--deployment", "d"],
        provider_factory=factory,  # type: ignore[arg-type]
        printer=messages.append,
    )

    assert code == 2 and "--confirm-live" in messages[0] and not (tmp_path / "r.json").exists()


def test_report_has_counts_and_latencies_but_no_query_text(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    code = main(
        [
            "--queries", str(QUERIES), "--out", str(out), "--endpoint", "https://x", "--deployment", "d",
            "--confirm-live",
        ],
        provider_factory=lambda *a, **k: ScriptedIntentProvider(expansions=("racing adventure",)),  # type: ignore[arg-type]
        printer=lambda _m: None,
    )

    report = json.loads(out.read_text())
    assert code == 0 and report["scope"] == SCOPE and report["queries"] == 30
    assert report["fallback_rate"] == 0.0 and report["parser_meaning_preserved_rate"] == 1.0
    assert "text" not in out.read_text().replace('"scope"', "")  # no query or model text fields


def test_failures_count_as_fallbacks() -> None:
    report = run_live_eval(ScriptedIntentProvider(ScriptedIntentMode.TIMEOUT), ["racing", "puzzle"])

    assert report["fallback_rate"] == 1.0 and report["outcomes"] == {"provider_timeout": 2}


def test_percentile() -> None:
    assert percentile([], 0.95) == 0.0
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
    assert percentile([1, 2, 3, 4, 100], 0.5) == 3


def test_missing_key_env_stops_before_building_a_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("INTENT_EVAL_KEY", raising=False)
    messages: list[str] = []

    code = main(
        [
            "--queries", str(QUERIES), "--out", str(tmp_path / "r.json"), "--endpoint", "https://x",
            "--deployment", "d", "--confirm-live", "--api-key-env", "INTENT_EVAL_KEY",
        ],
        provider_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no provider")),  # type: ignore[arg-type]
        printer=messages.append,
    )

    assert code == 2 and "INTENT_EVAL_KEY" in messages[0]
