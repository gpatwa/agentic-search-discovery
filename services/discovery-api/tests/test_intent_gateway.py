import pytest
from pydantic import ValidationError

from app.intelligence.adapter import BoundedIntentAdapter, ScriptedIntentMode, ScriptedIntentProvider
from app.intelligence.cache import CachedIntentProvider, normalize_query
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.intent import (
    INTENT_VERSION,
    MAX_REWRITES,
    IntentRewrite,
    RewriteKind,
    StructuredDiscoveryIntent,
    build_intent,
)


def _rewrite(kind: RewriteKind, text: str) -> IntentRewrite:
    return IntentRewrite(kind=kind, text=text)


# ---- step 1: typed rewrites ----


def test_rewrites_are_typed_bounded_and_deduplicated() -> None:
    synonym = _rewrite(RewriteKind.SYNONYM, "space racing")
    intent = build_intent("racing", rewrites=(synonym, synonym, _rewrite(RewriteKind.BROADER, "driving")))

    assert intent.rewrites == (synonym, _rewrite(RewriteKind.BROADER, "driving"))
    assert intent.intent_version == INTENT_VERSION == "imd-intent-v2"


def test_rewrite_contract_rejects_unknown_kind_blank_text_and_overflow() -> None:
    with pytest.raises(ValidationError):
        StructuredDiscoveryIntent.model_validate({"rewrites": ({"kind": "invented", "text": "x"},)})
    with pytest.raises(ValidationError):
        IntentRewrite(kind=RewriteKind.SYNONYM, text="   ")
    with pytest.raises(ValidationError):
        IntentRewrite(kind=RewriteKind.SYNONYM, text="bad\nvalue")
    with pytest.raises(ValidationError):
        build_intent(
            "racing",
            rewrites=tuple(_rewrite(RewriteKind.SYNONYM, f"rewrite {index}") for index in range(MAX_REWRITES + 1)),
        )


def test_rewrites_flow_through_the_adapter() -> None:
    rewrite = _rewrite(RewriteKind.SYNONYM, "space racing")
    result = BoundedIntentAdapter(ScriptedIntentProvider(rewrites=(rewrite,))).resolve("racing")

    assert result.used_fallback is False
    assert result.intent.rewrites == (rewrite,)


# ---- step 2: catalogue guardrail ----


def test_guardrail_drops_off_vocabulary_suggestions_and_counts_them() -> None:
    vocabulary = CatalogVocabulary(terms=("Lantern Harbor",))
    provider = ScriptedIntentProvider(
        expansions=("harbor mystery", "buy cryptocurrency now"),
        rewrites=(
            _rewrite(RewriteKind.SYNONYM, "racing game"),  # shares a word with the query
            _rewrite(RewriteKind.SUBSTITUTE, "mystery"),  # a catalogue theme
            _rewrite(RewriteKind.BROADER, "quarterly tax filing"),
        ),
    )
    result = BoundedIntentAdapter(provider, vocabulary=vocabulary).resolve("racing")

    assert result.used_fallback is False
    assert result.intent.expansions == ("harbor mystery",)
    assert [item.text for item in result.intent.rewrites] == ["racing game", "mystery"]
    assert result.dropped_by_guardrail == 2


def test_guardrail_never_touches_parser_owned_fields() -> None:
    provider = ScriptedIntentProvider(expansions=("zzz unrelated",))
    result = BoundedIntentAdapter(provider, vocabulary=CatalogVocabulary()).resolve('"Lantern Harbor" racing')

    assert result.intent.preserves(build_intent('"Lantern Harbor" racing'))
    assert result.intent.expansions == ()
    assert result.dropped_by_guardrail == 1


def test_without_a_vocabulary_behaviour_is_unchanged() -> None:
    provider = ScriptedIntentProvider(expansions=("zzz unrelated",))
    result = BoundedIntentAdapter(provider).resolve("racing")

    assert result.intent.expansions == ("zzz unrelated",)
    assert result.dropped_by_guardrail == 0


# ---- step 3: head-query cache ----


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_hit_skips_the_provider_but_is_still_validated() -> None:
    provider = ScriptedIntentProvider(expansions=("space racing",))
    cached = CachedIntentProvider(provider, provider_name="scripted")
    adapter = BoundedIntentAdapter(cached)

    first = adapter.resolve("Racing")
    second = adapter.resolve("  racing ")  # normalises to the same key

    assert provider.calls == 1
    assert (cached.stats.hits, cached.stats.misses, cached.stats.stored) == (1, 1, 1)
    assert first.intent == second.intent
    assert second.used_fallback is False


def test_a_poisoned_cache_entry_is_rejected_by_the_adapter() -> None:
    cached = CachedIntentProvider(ScriptedIntentProvider(), provider_name="scripted")
    adapter = BoundedIntentAdapter(cached)
    adapter.resolve("racing")
    key = next(iter(cached._entries))
    stored_at, payload = cached._entries[key]
    cached._entries[key] = (stored_at, {**payload, "lexical_text": "tampered"})

    result = adapter.resolve("racing")

    assert cached.stats.hits == 1
    assert result.used_fallback is True
    assert result.fallback_reason == "provider_invalid"


def test_ttl_expiry_calls_the_provider_again() -> None:
    clock = _Clock()
    provider = ScriptedIntentProvider()
    cached = CachedIntentProvider(provider, provider_name="scripted", ttl_seconds=10, clock=clock)

    cached.generate("racing")
    clock.now = 11
    cached.generate("racing")

    assert provider.calls == 2
    assert cached.stats.expired == 1


def test_failures_and_invalid_output_are_not_cached() -> None:
    for mode in (ScriptedIntentMode.TIMEOUT, ScriptedIntentMode.MALFORMED, ScriptedIntentMode.INJECTION):
        provider = ScriptedIntentProvider(mode)
        cached = CachedIntentProvider(provider, provider_name="scripted")
        adapter = BoundedIntentAdapter(cached)

        assert adapter.resolve("racing").used_fallback is True
        assert adapter.resolve("racing").used_fallback is True
        assert provider.calls == 2
        assert cached.stats.stored == 0


def test_entries_are_scoped_by_provider_name_and_by_intent_version(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedIntentProvider()
    a = CachedIntentProvider(provider, provider_name="a")
    b = CachedIntentProvider(provider, provider_name="b")
    a.generate("racing")
    b.generate("racing")
    assert provider.calls == 2  # separate instances never share entries

    # A version bump changes the key, so an old-version entry is never served.
    old_keys = set(a._entries)
    monkeypatch.setattr("app.intelligence.cache.INTENT_VERSION", "imd-intent-vNEXT")
    a.generate("racing")
    assert provider.calls == 3
    assert set(a._entries) - old_keys


def test_caller_catalog_ids_are_never_cached_across_callers() -> None:
    class IdProvider:
        calls = 0

        def generate(self, raw_query: str) -> object:
            IdProvider.calls += 1
            return build_intent(raw_query, explicit_catalog_ids=("exp-1",)).model_dump()

    cached = CachedIntentProvider(IdProvider(), provider_name="ids")
    cached.generate("racing")
    cached.generate("racing")

    assert IdProvider.calls == 2
    assert cached.stats.stored == 0


def test_cache_is_size_bounded_and_rejects_bad_settings() -> None:
    cached = CachedIntentProvider(ScriptedIntentProvider(), provider_name="scripted", max_entries=2)
    for query in ("one", "two", "three"):
        cached.generate(query)

    assert len(cached._entries) == 2
    with pytest.raises(ValueError):
        CachedIntentProvider(ScriptedIntentProvider(), provider_name="x", ttl_seconds=0)


def test_normalize_query() -> None:
    assert normalize_query("  Lantern   HARBOR\n") == "lantern harbor"
