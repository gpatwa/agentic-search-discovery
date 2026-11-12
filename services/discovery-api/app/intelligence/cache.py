"""Head-query cache for optional intent providers.

The cache wraps a provider and stores only output that already validates as a
`StructuredDiscoveryIntent` with no caller-owned catalog IDs. A cache hit is returned as raw
provider data, so `BoundedIntentAdapter` validates it again on every request: the cache never
bypasses validation. Entries are scoped by provider name and intent version, so a contract
change never serves an old shape. In-memory and per-process; no network.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from app.intelligence.adapter import IntentProvider
from app.intelligence.intent import INTENT_VERSION, StructuredDiscoveryIntent

_WHITESPACE = re.compile(r"\s+")


def normalize_query(raw_query: str) -> str:
    """Casefold and collapse whitespace so trivially different phrasings share an entry."""
    return _WHITESPACE.sub(" ", raw_query.strip().casefold())


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    expired: int = 0
    stored: int = 0
    skipped: int = 0  # provider output that was not cacheable (invalid or carried caller IDs)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class CachedIntentProvider:
    """Wrap an `IntentProvider` with an exact-match, TTL-bound, size-bounded cache."""

    def __init__(
        self,
        provider: IntentProvider,
        *,
        provider_name: str,
        ttl_seconds: float = 3600.0,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self.provider = provider
        self.provider_name = provider_name
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self.stats = CacheStats()
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, dict[str, object]]] = OrderedDict()

    def generate(self, raw_query: str) -> object:
        key = (self.provider_name, INTENT_VERSION, normalize_query(raw_query))
        now = self.clock()
        entry = self._entries.get(key)
        if entry is not None:
            stored_at, payload = entry
            if now - stored_at <= self.ttl_seconds:
                self._entries.move_to_end(key)
                self.stats.hits += 1
                return dict(payload)
            del self._entries[key]
            self.stats.expired += 1
        self.stats.misses += 1

        output = self.provider.generate(raw_query)  # errors and timeouts propagate and are never cached
        self._remember(key, output, now)
        return output

    def _remember(self, key: tuple[str, str, str], output: object, now: float) -> None:
        try:
            intent = StructuredDiscoveryIntent.model_validate(output)
        except Exception:  # invalid output is returned as-is for the adapter to reject, never stored
            self.stats.skipped += 1
            return
        if intent.explicit_catalog_ids:
            self.stats.skipped += 1
            return
        self._entries[key] = (now, intent.model_dump())
        self._entries.move_to_end(key)
        self.stats.stored += 1
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
