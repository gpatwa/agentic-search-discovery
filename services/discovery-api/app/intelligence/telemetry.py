"""Per-request telemetry for the optional intent layer.

Records carry only coarse, safe dimensions: no raw query, no tenant or user identifiers, no
provider payloads. Latency is bucketed like the rest of discovery telemetry. The cost field is
an estimate supplied by the caller and is zero for every fake provider.
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.adapter import BoundedIntentAdapter, IntentResolution
from app.intelligence.cache import CachedIntentProvider
from app.telemetry.discovery import LatencyBucket


class CacheOutcome(StrEnum):
    HIT = "hit"
    MISS = "miss"
    NONE = "none"  # no cache in front of the provider, or no provider at all


class IntentOutcome(StrEnum):
    PROVIDER_OK = "provider_ok"
    MODEL_OFF = "model_off"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_INVALID = "provider_invalid"
    PROVIDER_MISS = "provider_miss"  # precomputed provider had no entry; the deterministic path served


class IntentTelemetryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outcome: IntentOutcome
    cache: CacheOutcome
    latency_bucket: LatencyBucket
    guardrail_dropped: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0, allow_inf_nan=False)


def bucket_latency(milliseconds: float) -> LatencyBucket:
    if milliseconds < 10:
        return LatencyBucket.LT_10_MS
    if milliseconds < 50:
        return LatencyBucket.MS_10_TO_49
    if milliseconds < 100:
        return LatencyBucket.MS_50_TO_99
    if milliseconds < 250:
        return LatencyBucket.MS_100_TO_249
    if milliseconds < 1000:
        return LatencyBucket.MS_250_TO_999
    return LatencyBucket.GE_1000_MS


class ObservedIntentAdapter:
    """Resolve intents exactly as `BoundedIntentAdapter` does and record one event per call."""

    def __init__(
        self,
        adapter: BoundedIntentAdapter,
        *,
        cache: CachedIntentProvider | None = None,
        cost_per_provider_call_usd: float = 0.0,
        clock: Callable[[], float] = time.perf_counter,
        max_records: int = 10_000,
    ) -> None:
        if cost_per_provider_call_usd < 0 or max_records <= 0:
            raise ValueError("cost must be non-negative and max_records positive")
        self.adapter = adapter
        self.cache = cache
        self.cost_per_provider_call_usd = cost_per_provider_call_usd
        self.clock = clock
        self.max_records = max_records
        self.records: list[IntentTelemetryRecord] = []

    def resolve(self, raw_query: str, **kwargs: object) -> IntentResolution:
        hits_before = self.cache.stats.hits if self.cache else 0
        started = self.clock()
        result = self.adapter.resolve(raw_query, **kwargs)  # type: ignore[arg-type]
        elapsed_ms = (self.clock() - started) * 1000

        if self.adapter.provider is None:
            cache = CacheOutcome.NONE
        elif self.cache is None:
            cache = CacheOutcome.NONE
        else:
            cache = CacheOutcome.HIT if self.cache.stats.hits > hits_before else CacheOutcome.MISS
        outcome = (
            IntentOutcome.PROVIDER_OK
            if not result.used_fallback
            else IntentOutcome(result.fallback_reason or IntentOutcome.MODEL_OFF.value)
        )
        called_provider = self.adapter.provider is not None and cache is not CacheOutcome.HIT
        self.records.append(
            IntentTelemetryRecord(
                outcome=outcome,
                cache=cache,
                latency_bucket=bucket_latency(elapsed_ms),
                guardrail_dropped=result.dropped_by_guardrail,
                estimated_cost_usd=self.cost_per_provider_call_usd if called_provider else 0.0,
            )
        )
        if len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]
        return result

    def summary(self) -> Mapping[str, object]:
        return summarize(self.records)


def summarize(records: Sequence[IntentTelemetryRecord]) -> Mapping[str, object]:
    total = len(records)
    outcomes = Counter(record.outcome.value for record in records)
    cache = Counter(record.cache.value for record in records)
    cost = sum(record.estimated_cost_usd for record in records)
    lookups = cache["hit"] + cache["miss"]
    return {
        "requests": total,
        "outcomes": dict(sorted(outcomes.items())),
        "cache": dict(sorted(cache.items())),
        "cache_hit_rate": cache["hit"] / lookups if lookups else 0.0,
        "guardrail_dropped": sum(record.guardrail_dropped for record in records),
        "estimated_cost_usd_per_1k": (cost / total * 1000) if total else 0.0,
    }
