"""Relevance comparison on the golden corpus: search with and without precomputed intent suggestions.

Runs the real `LocalSearchService` over the 48 synthetic golden experiences for each golden query, once as
deterministic baseline and once with a snapshot provider, and scores both against the golden graded judgments
(recall, MRR, nDCG at k). The snapshot is built from query text only, never from judgments.

Read the numbers with care: 30 synthetic queries and 62 judgments (unjudged results count as grade 0) are far
too few to support a quality claim. The report prints win/loss/tie counts next to the means for that reason.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from app.contracts.discovery import DiscoveryRequestContext
from app.domain.models import ExperienceRecord, UserProfile
from app.evaluation.metrics import mean_reciprocal_rank, ndcg_at_k, recall_at_k
from app.intelligence.adapter import BoundedIntentAdapter
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.precompute import IntentSnapshot, SnapshotIntentProvider, load_snapshot
from app.routes.search import LocalSearchService, SearchRequest
from app.search.mapper import CatalogDocumentInput, CatalogDocumentMappingError, map_catalog_document

SCOPE = "golden corpus (30 synthetic queries, 62 judgments); direction only, not a quality claim"
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _load(directory: Path, name: str) -> list[dict]:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def load_golden(directory: Path) -> tuple[tuple, tuple[UserProfile, ...], list[dict], dict[str, dict[str, int]]]:
    records = [ExperienceRecord.model_validate(item) for item in _load(directory, "experiences.json")]
    documents = []
    for record in records:
        try:
            documents.append(
                map_catalog_document(
                    CatalogDocumentInput(
                        record=record,
                        tenant_id=record.tenant_id,
                        source_type="fixture",
                        source_id=record.experience_id,
                        provenance_ref=f"fixture://{record.experience_id}",
                        content_version="v1",
                        permission_version="v1",
                        embedding=tuple(0.01 for _ in range(384)),
                        created_at=EPOCH,
                        updated_at=EPOCH,
                    )
                )
            )
        except CatalogDocumentMappingError:  # blocked or unavailable records are never indexed, in any config
            continue
    documents = tuple(documents)
    profiles = tuple(UserProfile.model_validate(item) for item in _load(directory, "users.json"))
    judgments: dict[str, dict[str, int]] = defaultdict(dict)
    for item in _load(directory, "judgments.json"):
        judgments[item["query_id"]][item["experience_id"]] = int(item["grade"])
    return documents, profiles, _load(directory, "queries.json"), dict(judgments)


def run_queries(service: LocalSearchService, queries: Sequence[dict]) -> dict[str, list[str]]:
    """Ranked experience IDs per query (empty when the search failed or found nothing)."""
    ranked: dict[str, list[str]] = {}
    for query in queries:
        context = DiscoveryRequestContext(
            tenant_id=query["tenant_id"],
            principal_id=query["user_id"],
            request_id=f"eval-{query['query_id']}",
            purpose="search",
            locale="en-US",
            device="web",
            age=16,
        )
        response = service.search(SearchRequest(query=query["text"], context=context, page_size=50))
        ranked[query["query_id"]] = [item.experience_id for item in response.results]
    return ranked


def score(ranked: Mapping[str, Sequence[str]], judgments: Mapping[str, Mapping[str, int]], k: int) -> dict[str, dict[str, float]]:
    return {
        query_id: {
            "ndcg": ndcg_at_k(ranked.get(query_id, ()), relevance, k=k),
            "recall": recall_at_k(ranked.get(query_id, ()), relevance, k=k),
            "mrr": mean_reciprocal_rank(ranked.get(query_id, ()), relevance, k=k),
        }
        for query_id, relevance in judgments.items()
        if any(grade >= 1 for grade in relevance.values())  # a query with no relevant item cannot score
    }


def compare(
    baseline: Mapping[str, Mapping[str, float]],
    variant: Mapping[str, Mapping[str, float]],
    categories: Mapping[str, str],
) -> dict[str, object]:
    ids = sorted(baseline)
    n = len(ids)
    out: dict[str, object] = {"queries_scored": n}
    for metric in ("ndcg", "recall", "mrr"):
        base = sum(baseline[i][metric] for i in ids) / n if n else 0.0
        var = sum(variant[i][metric] for i in ids) / n if n else 0.0
        wins = sum(1 for i in ids if variant[i][metric] > baseline[i][metric] + 1e-9)
        losses = sum(1 for i in ids if variant[i][metric] < baseline[i][metric] - 1e-9)
        out[metric] = {
            "baseline": round(base, 4),
            "with_suggestions": round(var, 4),
            "delta": round(var - base, 4),
            "wins": wins,
            "losses": losses,
            "ties": n - wins - losses,
        }
    changed = [
        {"category": categories.get(i, "?"), "ndcg_delta": round(variant[i]["ndcg"] - baseline[i]["ndcg"], 4)}
        for i in ids
        if abs(variant[i]["ndcg"] - baseline[i]["ndcg"]) > 1e-9
    ]
    out["queries_with_changed_ndcg"] = sorted(changed, key=lambda item: (item["category"], item["ndcg_delta"]))
    return out


def build_report(golden_dir: Path, snapshot_path: Path, k: int = 10) -> dict[str, object]:
    documents, profiles, queries, judgments = load_golden(golden_dir)
    snapshot = load_snapshot(snapshot_path)
    categories = {item["query_id"]: item["category"] for item in queries}

    def service_for(candidate: IntentSnapshot | None) -> LocalSearchService:
        if candidate is None:
            return LocalSearchService(documents, profiles)
        adapter = BoundedIntentAdapter(SnapshotIntentProvider(candidate), vocabulary=CatalogVocabulary())
        return LocalSearchService(documents, profiles, intent_adapter=adapter)

    baseline = score(run_queries(service_for(None), queries), judgments, k)
    variant = score(run_queries(service_for(snapshot), queries), judgments, k)
    # Control: the same suggestions attached to the WRONG queries (values rotated by one). If the real gain were
    # just "any extra source changes the ranking", this would gain too.
    keys = sorted(snapshot.entries)
    rotated = {key: snapshot.entries[keys[(index + 1) % len(keys)]] for index, key in enumerate(keys)} if keys else {}
    control = score(run_queries(service_for(snapshot.model_copy(update={"entries": rotated})), queries), judgments, k)
    covered = sum(1 for item in queries if " ".join(item["text"].casefold().split()) in snapshot.entries)
    return {
        "scope": SCOPE,
        "k": k,
        "snapshot_source": snapshot.source,
        "queries_total": len(queries),
        "queries_with_snapshot_entry": covered,
        "comparison": compare(baseline, variant, categories),
        "control_shuffled_suggestions": compare(baseline, control, categories),
    }


def main(argv: Sequence[str] | None = None, *, printer=print) -> int:  # type: ignore[no-untyped-def]
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.evaluation.intent_relevance")
    parser.add_argument("--golden-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args(argv)
    report = build_report(args.golden_dir, args.snapshot, args.k)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printer(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
