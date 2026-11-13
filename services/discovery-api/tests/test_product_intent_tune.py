import json
from pathlib import Path

from tests.test_product_search_eval import built  # noqa: F401  (fixture)

from app.evaluation.product_intent_tune import SCOPE, choose, dev_grid, main, memoized
from app.evaluation.product_search import EvalQuery
from app.intelligence.adapter import ScriptedIntentProvider
from app.intelligence.precompute import IntentSnapshot, SnapshotEntry


def test_choose_prefers_best_dev_delta_then_smaller_weight_then_fewer_suggestions() -> None:
    grid = [
        {"weight": 0.5, "limit": 3, "mean_delta": 0.02},
        {"weight": 0.2, "limit": 3, "mean_delta": 0.02},
        {"weight": 0.2, "limit": 1, "mean_delta": 0.02},
        {"weight": 1.0, "limit": 3, "mean_delta": -0.1},
    ]

    assert choose(grid) == {"weight": 0.2, "limit": 1, "mean_delta": 0.02}


def test_memoized_retriever_calls_once_per_key() -> None:
    calls: list[tuple[str, int]] = []

    def base(text: str, k: int) -> list[str]:
        calls.append((text, k))
        return ["p1"]

    run = memoized(base)
    run("a", 5)
    run("a", 5)
    run("a", 6)

    assert calls == [("a", 5), ("a", 6)]


def test_zero_weight_equals_baseline_and_the_grid_is_complete() -> None:
    queries = [EvalQuery("q1", "shoes", {"p9": "E"})]
    table = {"shoes": ["x1"], "footwear": ["p9"]}
    snapshot = IntentSnapshot(source="t", attempted=1, entries={"shoes": SnapshotEntry(expansions=("footwear",))})

    grid = dev_grid(lambda text, k: table.get(text, [])[:k], queries, snapshot)

    assert len(grid) == 12
    assert all(row["mean_delta"] == 0.0 for row in grid if row["weight"] == 0.0)
    assert any(row["mean_delta"] > 0 for row in grid if row["weight"] > 0)


def test_cli_needs_confirmation_to_build_dev_snapshot_and_runs_with_a_fake(built, tmp_path: Path) -> None:  # noqa: F811
    subset, examples = built
    held_snapshot = tmp_path / "held.json"
    from app.intelligence.precompute import save_snapshot

    save_snapshot(IntentSnapshot(source="t", attempted=0), held_snapshot)
    base = [
        "--subset", str(subset), "--examples", str(examples), "--heldout-snapshot", str(held_snapshot),
        "--dev-snapshot", str(tmp_path / "dev.json"), "--out", str(tmp_path / "rep.json"),
        "--sample", "10", "--held-out-fraction", "0.5", "--endpoint", "https://x", "--deployment", "d",
    ]
    messages: list[str] = []

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("no provider without confirmation")

    assert main(base, provider_factory=boom, printer=messages.append) == 2 and "--confirm-live" in messages[0]  # type: ignore[arg-type]
    assert not (tmp_path / "dev.json").exists()

    code = main(
        [*base, "--confirm-live"],
        provider_factory=lambda *a, **k: ScriptedIntentProvider(expansions=("running shoes",)),  # type: ignore[arg-type]
        printer=messages.append,
    )

    report = json.loads((tmp_path / "rep.json").read_text())
    assert code == 0 and report["scope"] == SCOPE and len(report["dev_grid"]) == 12
    assert report["dev_snapshot_built_now"] is True and "chosen_on_dev" in report
