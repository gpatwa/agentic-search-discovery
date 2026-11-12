"""Catalogue-vocabulary guardrail for optional provider expansions and rewrites.

A provider may only suggest words that connect to the catalogue or to the user's own query.
This is a lexical check, a deterministic stand-in for the embedding-similarity filter that
production systems use; it keeps hallucinated or off-domain suggestions out of retrieval.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.models import Genre, Theme
from app.intelligence.intent import StructuredDiscoveryIntent

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.casefold()))


class CatalogVocabulary:
    """Casefolded word set built from the domain enums and any extra catalogue terms."""

    def __init__(self, terms: Iterable[str] = ()) -> None:
        words: set[str] = set()
        for item in (*Genre, *Theme):
            words |= _tokens(item.value)
        for term in terms:
            words |= _tokens(term)
        self.words = frozenset(words)

    def allows(self, text: str, *, query_words: frozenset[str] = frozenset()) -> bool:
        """Keep a suggestion only if at least one word is catalogue or query vocabulary."""
        return bool(_tokens(text) & (self.words | query_words))


def apply_catalog_guardrail(
    intent: StructuredDiscoveryIntent,
    vocabulary: CatalogVocabulary,
    raw_query: str,
) -> tuple[StructuredDiscoveryIntent, int]:
    """Drop off-vocabulary expansions and rewrites; return the intent and how many were dropped."""
    query_words = _tokens(raw_query)
    expansions = tuple(value for value in intent.expansions if vocabulary.allows(value, query_words=query_words))
    rewrites = tuple(value for value in intent.rewrites if vocabulary.allows(value.text, query_words=query_words))
    dropped = (len(intent.expansions) - len(expansions)) + (len(intent.rewrites) - len(rewrites))
    if not dropped:
        return intent, 0
    return intent.model_copy(update={"expansions": expansions, "rewrites": rewrites}), dropped
