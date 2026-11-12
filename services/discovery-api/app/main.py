"""Discovery API entry point: health plus the local search, home and interaction routes.

The routes run over a fictional, seeded demo catalog with fake providers. By default nothing here reads
a database, a search cluster or a model. With DISCOVERY_SEARCH_BACKEND=opensearch, search ranks through
OpenSearch and falls back to the in-memory catalog when the cluster is unreachable.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from app.config import settings_from_env
from app.demo_data import load_demo_catalog
from app.embedding import create_embedder
from app.intelligence.adapter import BoundedIntentAdapter
from app.intelligence.guardrail import CatalogVocabulary
from app.intelligence.precompute import SnapshotError, SnapshotIntentProvider, load_snapshot
from app.intelligence.telemetry import ObservedIntentAdapter
from app.opensearch.client import OpenSearchClient
from app.opensearch.retriever import OpenSearchRetriever
from app.opensearch.service import OpenSearchSearchService
from app.routes import home, interactions, products, search
from app.security import TokenVerifier

API_PREFIX = "/api/discovery"


logger = logging.getLogger(__name__)


def build_intent_observer(snapshot_path: str | None) -> ObservedIntentAdapter | None:
    """Serve precomputed intent suggestions from a snapshot file; None (deterministic search) if unset or unusable.

    No model is called while serving. A missing or invalid snapshot is logged and ignored so search stays up.
    """
    if not snapshot_path:
        return None
    try:
        snapshot = load_snapshot(Path(snapshot_path))
    except SnapshotError as error:
        logger.warning("intent snapshot ignored, search stays deterministic: %s", error)
        return None
    provider = SnapshotIntentProvider(snapshot)
    return ObservedIntentAdapter(BoundedIntentAdapter(provider, vocabulary=CatalogVocabulary()))


def create_app() -> FastAPI:
    experiences, documents, users = load_demo_catalog()
    settings = settings_from_env()
    embedder = create_embedder(settings)
    intent_observer = build_intent_observer(settings.intent_snapshot_path)
    billable = getattr(embedder, "is_billable", False)
    if settings.search_backend == "opensearch":
        client = OpenSearchClient(
            settings.opensearch_url or "",
            username=settings.opensearch_username,
            password=settings.opensearch_password,
            verify_tls=settings.opensearch_verify_tls,
            timeout=settings.request_timeout_seconds,
        )
        products.service = products.OpenSearchProductService(client)
        search.service = OpenSearchSearchService(
            OpenSearchRetriever(client),
            documents,
            users,
            embedder=None if billable else embedder,
            remote_embedder=embedder,
            features=settings.features,
            intent_adapter=intent_observer,
        )
    else:
        products.service = products.UnconfiguredProductService()
        search.service = search.LocalSearchService(
            documents, users, embedder=embedder, features=settings.features, intent_adapter=intent_observer
        )
    home.service = home.LocalHomeService(experiences, users)

    application = FastAPI(title="Compass Discovery API", version="0.1.0")
    application.state.intent_observer = intent_observer
    application.state.auth_verifier = (
        TokenVerifier(
            entra_tenant_id=settings.entra_tenant_id or "",
            audience=settings.entra_audience or "",
            tenant_map=settings.entra_tenant_map,
            principal_map=settings.entra_principal_map,
            jwks_url=settings.entra_jwks_url,
            required_scope=settings.entra_required_scope,
        )
        if settings.auth_enabled
        else None
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        """Return a stable, dependency-free service health response."""

        return {"service": "discovery-api", "status": "ok"}

    for router in (search.router, products.router, home.router, interactions.router):
        application.include_router(router, prefix=API_PREFIX)
    return application


app = create_app()
