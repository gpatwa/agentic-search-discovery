import json
from pathlib import Path

from app.evaluation.intent_relevance import SCOPE, build_report, compare, load_golden, main, score
from app.intelligence.adapter import ScriptedIntentProvider
from app.intelligence.precompute import IntentSnapshot, build_snapshot, save_snapshot

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def test_golden_loader_skips_unindexable_records_and_groups_judgments() -> None:
    documents, profiles, queries, judgments = load_golden(GOLDEN)

    assert 0 < len(documents) <= 48 and len(profiles) == 24 and len(queries) == 30
    assert sum(len(item) for item in judgments.values()) == 62


def test_score_ignores_queries_without_a_relevant_judgment() -> None:
    scored = score({"q1": ["a"], "q2": ["b"]}, {"q1": {"a": 3}, "q2": {"b": 0}}, k=10)

    assert list(scored) == ["q1"] and scored["q1"]["ndcg"] == 1.0


def test_compare_counts_wins_losses_and_ties() -> None:
    base = {"a": {"ndcg": 0.5, "recall": 0.5, "mrr": 0.5}, "b": {"ndcg": 0.5, "recall": 0.5, "mrr": 0.5}}
    var = {"a": {"ndcg": 1.0, "recall": 0.5, "mrr": 0.2}, "b": {"ndcg": 0.5, "recall": 0.5, "mrr": 0.5}}

    result = compare(base, var, {"a": "typo", "b": "alias"})

    assert result["ndcg"] == {"baseline": 0.5, "with_suggestions": 0.75, "delta": 0.25, "wins": 1, "losses": 0, "ties": 1}
    assert result["mrr"]["losses"] == 1
    assert result["queries_with_changed_ndcg"] == [{"category": "typo", "ndcg_delta": 0.5}]


def test_empty_snapshot_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    save_snapshot(IntentSnapshot(source="empty", attempted=0), path)

    report = build_report(GOLDEN, path)

    assert report["scope"] == SCOPE and report["queries_with_snapshot_entry"] == 0
    for key in ("comparison", "control_shuffled_suggestions"):
        assert all(report[key][metric]["delta"] == 0 for metric in ("ndcg", "recall", "mrr"))


def test_cli_writes_a_report_without_query_text(tmp_path: Path) -> None:
    queries = json.loads((GOLDEN / "queries.json").read_text())
    texts = [item["text"] for item in queries]
    snapshot = build_snapshot(texts, ScriptedIntentProvider(expansions=("racing adventure",)), source="scripted")
    snap_path, out = tmp_path / "snap.json", tmp_path / "rel.json"
    save_snapshot(snapshot, snap_path)

    code = main(["--golden-dir", str(GOLDEN), "--snapshot", str(snap_path), "--out", str(out)], printer=lambda _m: None)

    report = json.loads(out.read_text())
    assert code == 0 and report["queries_with_snapshot_entry"] > 0
    assert "text" not in out.read_text().replace('"scope"', "") and not any(t in out.read_text() for t in texts if len(t) > 12)
