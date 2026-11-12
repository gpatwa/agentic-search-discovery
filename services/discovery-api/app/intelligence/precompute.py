"""Offline head-query precompute: a model runs ahead of time; serving never calls a model.

Flow (the Instacart-style split, scoped to this repo):
1. `select_head_queries` picks the most frequent normalised queries from a query log.
2. `build_snapshot` resolves each one through the full `BoundedIntentAdapter` (strict validation plus the
   catalogue guardrail) with any `IntentProvider`, and keeps only answers that were accepted.
3. The snapshot is a small JSON file holding, per normalised query, ONLY `expansions` and `rewrites`.
4. `SnapshotIntentProvider` serves it. A query without an entry raises `IntentMiss`, and the adapter falls
   back to the deterministic intent. The served intent is rebuilt from the actual query's parse, so a snapshot
   entry can never change parser-owned meaning, identity or catalogue fields, and every hit is re-validated.

A snapshot is stamped with the intent version and a source label; loading refuses one from another intent
version. It contains query text and model suggestions: treat it as data of the same sensitivity as the query log.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.adapter import BoundedIntentAdapter, IntentMiss, IntentProvider
from app.intelligence.cache import normalize_query
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.intent import INTENT_VERSION, IntentRewrite, build_intent

SNAPSHOT_FORMAT = "intent-head-snapshot-v1"
MAX_SNAPSHOT_ENTRIES = 50_000


class SnapshotError(ValueError):
    """The snapshot file is unusable (wrong format, wrong intent version, or invalid entries)."""


class SnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    expansions: tuple[str, ...] = ()
    rewrites: tuple[IntentRewrite, ...] = ()


class IntentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format: str = SNAPSHOT_FORMAT
    intent_version: str = INTENT_VERSION
    source: str = Field(min_length=1, max_length=128)  # e.g. provider cache_name: model and prompt version
    attempted: int = Field(ge=0)
    entries: Mapping[str, SnapshotEntry] = Field(default_factory=dict)


def select_head_queries(texts: Iterable[str], *, top_n: int, min_count: int = 1) -> list[str]:
    """The `top_n` most frequent normalised queries seen at least `min_count` times (ties: alphabetical)."""
    if top_n <= 0 or min_count <= 0:
        raise ValueError("top_n and min_count must be positive")
    counts = Counter(normalize_query(text) for text in texts if text.strip())
    ranked = sorted((item for item in counts.items() if item[1] >= min_count), key=lambda item: (-item[1], item[0]))
    return [query for query, _count in ranked[:top_n]]


def build_snapshot(
    head_queries: Sequence[str],
    provider: IntentProvider,
    *,
    source: str,
    vocabulary: CatalogVocabulary | None = None,
) -> IntentSnapshot:
    """Run each head query through validation and the guardrail; keep only accepted, non-empty answers."""
    if len(head_queries) > MAX_SNAPSHOT_ENTRIES:
        raise ValueError(f"at most {MAX_SNAPSHOT_ENTRIES} head queries")
    adapter = BoundedIntentAdapter(provider, vocabulary=vocabulary or CatalogVocabulary())
    entries: dict[str, SnapshotEntry] = {}
    for query in dict.fromkeys(normalize_query(item) for item in head_queries):
        result = adapter.resolve(query)
        if result.used_fallback:
            continue
        if result.intent.expansions or result.intent.rewrites:
            entries[query] = SnapshotEntry(expansions=result.intent.expansions, rewrites=result.intent.rewrites)
    return IntentSnapshot(source=source, attempted=len(set(map(normalize_query, head_queries))), entries=entries)


def save_snapshot(snapshot: IntentSnapshot, path: Path) -> None:
    """Write atomically so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".snapshot-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def load_snapshot(path: Path) -> IntentSnapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot = IntentSnapshot.model_validate_json(json.dumps(data))
    except (OSError, ValueError) as error:  # includes pydantic ValidationError
        raise SnapshotError("snapshot file is missing or invalid") from error
    if snapshot.format != SNAPSHOT_FORMAT:
        raise SnapshotError("unsupported snapshot format")
    if snapshot.intent_version != INTENT_VERSION:
        raise SnapshotError(f"snapshot is for {snapshot.intent_version}, this build is {INTENT_VERSION}")
    if len(snapshot.entries) > MAX_SNAPSHOT_ENTRIES:
        raise SnapshotError("snapshot has too many entries")
    return snapshot


class SnapshotIntentProvider:
    """Serve precomputed head-query answers; never calls a model, never touches the network."""

    def __init__(self, snapshot: IntentSnapshot) -> None:
        self.snapshot = snapshot
        self.hits = 0
        self.misses = 0

    def generate(self, raw_query: str) -> object:
        entry = self.snapshot.entries.get(normalize_query(raw_query))
        if entry is None:
            self.misses += 1
            raise IntentMiss("no precomputed intent for this query")
        self.hits += 1
        return build_intent(raw_query, expansions=entry.expansions, rewrites=entry.rewrites).model_dump()
