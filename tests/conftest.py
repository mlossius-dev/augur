"""
Shared test fixtures for Augur unit and integration tests.

Integration tests that need a real database or real LLM calls are marked
@pytest.mark.integration and are skipped unless the required environment
variables are present.  Unit tests run without any external dependencies.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: marks tests that require real infra")


# ── Config fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def minimal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the minimal set of env vars required by Settings."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://augur:test@localhost:5432/augur_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-main")
    monkeypatch.setenv("OPENROUTER_FREE_TIER_API_KEY", "sk-or-v1-test-free")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


# ── LLM client fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def llm_client_kwargs() -> dict:
    """Keyword arguments for constructing an LLMClient in tests."""
    return dict(
        openrouter_api_key="sk-or-v1-test-main",
        openrouter_free_tier_api_key="sk-or-v1-test-free",
        openrouter_base_url="https://openrouter.ai/api/v1",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        daily_budget_usd=Decimal("5.00"),
    )


# ── Integration markers ───────────────────────────────────────────────────────


def requires_db() -> pytest.MarkDecorator:
    """Skip if DATABASE_URL is not pointing at a live database."""
    return pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL not set — skipping database integration test",
    )


def requires_openrouter() -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY", "").startswith("sk-or-v1-")
        or "test" in os.environ.get("OPENROUTER_API_KEY", ""),
        reason="Real OPENROUTER_API_KEY not set — skipping LLM integration test",
    )
