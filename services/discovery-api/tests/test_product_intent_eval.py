import json
from pathlib import Path

from tests.test_product_search_eval import built  # noqa: F401  (fixture)

from app.evaluation.product_intent_eval import (
    SCOPE,
    evaluate_configs,
    fused_ranking,
    main,
    paired_bootstrap,
    sample_held_out,
    suggestions_for,
)
from app.evaluation.product_search import EvalQuery
from app.intelligence.adapter import ScriptedIntentProvider
from app.intelligence.precompute import IntentSnapshot, SnapshotEntry


def _retriever(table: dict[str, list[str]]):
    return lambda text, k: table.get(text, [])[:k]


def test_suggestions_are_deduplicated_and_capped_at_three() -> None:
    assert suggestions_for(None) == []
    entry = SnapshotEntry(expansions=("a", "b", "a", "c", "d"))
    assert suggestions_for(entry) == ["a", "b", "c"]


def test_fusion_keeps_the_original_query_leading_and_adds_new_products() -> None:
    retriever = _retriever({"q": ["p1", "p2"], "s": ["p9", "p1"]})

    ranked = fused_ranking(retriever, "q", ["s"], k=10)

    assert ranked[0] == "p1"  # found by both lists
    assert set(ranked) == {"p1", "p2", "p9"}
    assert fused_ranking(retriever, "q", [], k=10) == ["p1", "p2"]


def test_bootstrap_interval_brackets_the_mean_and_is_deterministic() -> None:
    deltas = [0.1, 0.0, 0.2, -0.1, 0.3, 0.0, 0.1, 0.2]
    low, high = paired_bootstrap(deltas)

    assert low <= sum(deltas) / len(deltas) <= high and paired_bootstrap(deltas) == (low, high)
    assert paired_bootstrap([]) == (0.0, 0.0)


def test_good_suggestions_beat_baseline_and_wrong_ones_do_not() -> None:
    queries = [
        EvalQuery("q1", "shoes", {"p9": "E"}),
        EvalQuery("q2", "chair", {"c9": "E"}),
    ]
    retriever = _retriever(
        {"shoes": ["x1"], "footwear": ["p9"], "chair": ["x2"], "seat": ["c9"]}
    )
    snapshot = IntentSnapshot(
        source="t",
        attempted=2,
        entries={"shoes": SnapshotEntry(expansions=("footwear",)), "chair": SnapshotEntry(expansions=("seat",))},
    )

    result = evaluate_configs(retriever, queries, snapshot)

    real = result["all_sampled"]["with_suggestions"]
    control = result["all_sampled"]["control_shuffled"]
    assert real["baseline_ndcg"] == 0.0 and real["ndcg"] > 0.3 and real["wins"] == 2 and real["losses"] == 0
    assert control["wins"] == 0  # swapped suggestions find nothing relevant
    assert result["queries_with_suggestions"] == 2


def test_sample_is_held_out_only_and_deterministic() -> None:
    queries = [EvalQuery(f"q{i}", f"text {i}", {"p": "E"}) for i in range(200)]

    first = sample_held_out(queries, seed=7, held_out_fraction=0.3, size=20)
    second = sample_held_out(queries, seed=7, held_out_fraction=0.3, size=20)

    assert first == second and len(first) == 20
    from app.evaluation.product_search import split_queries

    _dev, held = split_queries(queries, seed=7, held_out_fraction=0.3)
    assert {q.query_id for q in first} <= {q.query_id for q in held}


def test_cli_refuses_without_confirmation_and_runs_with_a_fake_provider(built, tmp_path: Path) -> None:  # noqa: F811
    subset, examples = built
    base = [
        "--subset", str(subset), "--examples", str(examples), "--snapshot-out", str(tmp_path / "snap.json"),
        "--out", str(tmp_path / "rep.json"), "--endpoint", "https://x", "--deployment", "d", "--sample", "10", "--held-out-fraction", "0.99",
    ]
    messages: list[str] = []

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("no provider without confirmation")

    assert main(base, provider_factory=boom, printer=messages.append) == 2 and "--confirm-live" in messages[0]  # type: ignore[arg-type]

    code = main(
        [*base, "--confirm-live"],
        provider_factory=lambda *a, **k: ScriptedIntentProvider(expansions=("running shoes",)),  # type: ignore[arg-type]
        printer=messages.append,
    )

    report = json.loads((tmp_path / "rep.json").read_text())
    assert code == 0 and report["scope"] == SCOPE and "Reddy" in report["dataset"]
    assert report["evaluation"]["queries_sampled"] > 0
