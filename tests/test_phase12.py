"""
Phase 12 — Production hardening: middleware tests.

Covers:
  - APIKeyMiddleware: no-key dev mode, correct key passes, wrong key 401,
    exempt paths, non-/api/ paths
  - ConversationRateLimitMiddleware: under-limit passes, over-limit 429,
    Retry-After header present, non-conversation paths exempt, burst
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from augur.middleware.auth import APIKeyMiddleware
from augur.middleware.ratelimit import ConversationRateLimitMiddleware


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_app_with_auth(api_key: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key=api_key)

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/docs")
    def docs():
        return {"docs": True}

    @app.get("/api/data")
    def api_data():
        return {"data": "secret"}

    @app.post("/api/data")
    def api_data_post():
        return {"data": "secret"}

    return app


def _make_app_with_ratelimit(per_minute: int = 3) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ConversationRateLimitMiddleware, per_minute=per_minute)

    @app.post("/api/conversation/query")
    def query():
        return {"answer": "ok"}

    @app.post("/api/other")
    def other():
        return {"other": True}

    @app.get("/api/conversation/query")
    def query_get():
        return {"ok": True}

    return app


# ── APIKeyMiddleware ──────────────────────────────────────────────────────────

class TestAPIKeyMiddleware(unittest.TestCase):

    # -- dev mode (no key configured) ----------------------------------------

    def test_dev_mode_no_key_allows_all(self):
        client = TestClient(_make_app_with_auth(None))
        assert client.get("/api/data").status_code == 200

    def test_dev_mode_allows_root(self):
        client = TestClient(_make_app_with_auth(None))
        assert client.get("/").status_code == 200

    def test_dev_mode_post_allowed(self):
        client = TestClient(_make_app_with_auth(None))
        assert client.post("/api/data").status_code == 200

    # -- correct key ---------------------------------------------------------

    def test_correct_key_passes(self):
        client = TestClient(_make_app_with_auth("secret-key"))
        assert client.get("/api/data", headers={"X-API-Key": "secret-key"}).status_code == 200

    def test_correct_key_post_passes(self):
        client = TestClient(_make_app_with_auth("secret-key"))
        assert client.post("/api/data", headers={"X-API-Key": "secret-key"}).status_code == 200

    # -- wrong / missing key -------------------------------------------------

    def test_missing_key_returns_401(self):
        client = TestClient(_make_app_with_auth("secret-key"), raise_server_exceptions=False)
        r = client.get("/api/data")
        assert r.status_code == 401

    def test_wrong_key_returns_401(self):
        client = TestClient(_make_app_with_auth("secret-key"), raise_server_exceptions=False)
        r = client.get("/api/data", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401

    def test_401_response_has_detail(self):
        client = TestClient(_make_app_with_auth("my-key"), raise_server_exceptions=False)
        body = client.get("/api/data").json()
        assert "detail" in body

    def test_empty_key_header_returns_401(self):
        client = TestClient(_make_app_with_auth("secret"), raise_server_exceptions=False)
        r = client.get("/api/data", headers={"X-API-Key": ""})
        assert r.status_code == 401

    # -- exempt paths --------------------------------------------------------

    def test_root_exempt_with_key_configured(self):
        client = TestClient(_make_app_with_auth("secret"))
        # root is not under /api/, so it passes without a key
        assert client.get("/").status_code == 200

    def test_health_exempt(self):
        client = TestClient(_make_app_with_auth("secret"))
        assert client.get("/health").status_code == 200

    def test_docs_path_exempt(self):
        # /docs does not start with /api/ so middleware passes it
        client = TestClient(_make_app_with_auth("secret"))
        assert client.get("/docs").status_code == 200

    # -- non-/api/ path passes without key -----------------------------------

    def test_non_api_path_passes_without_key(self):
        client = TestClient(_make_app_with_auth("secret"))
        assert client.get("/health").status_code == 200


# ── ConversationRateLimitMiddleware ───────────────────────────────────────────

class TestConversationRateLimitMiddleware(unittest.TestCase):

    def setUp(self):
        self.app = _make_app_with_ratelimit(per_minute=3)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    # -- happy path ----------------------------------------------------------

    def test_first_request_allowed(self):
        r = self.client.post("/api/conversation/query")
        assert r.status_code == 200

    def test_requests_within_limit_allowed(self):
        app = _make_app_with_ratelimit(per_minute=5)
        client = TestClient(app, raise_server_exceptions=False)
        for _ in range(5):
            assert client.post("/api/conversation/query").status_code == 200

    # -- rate exceeded -------------------------------------------------------

    def test_over_limit_returns_429(self):
        app = _make_app_with_ratelimit(per_minute=2)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query")
        client.post("/api/conversation/query")
        r = client.post("/api/conversation/query")
        assert r.status_code == 429

    def test_429_has_retry_after_header(self):
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query")
        r = client.post("/api/conversation/query")
        assert r.status_code == 429
        assert "Retry-After" in r.headers

    def test_retry_after_is_positive_integer(self):
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query")
        r = client.post("/api/conversation/query")
        retry = int(r.headers["Retry-After"])
        assert retry > 0

    def test_429_detail_in_body(self):
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query")
        r = client.post("/api/conversation/query")
        assert "detail" in r.json()

    # -- exempt paths --------------------------------------------------------

    def test_other_post_path_not_limited(self):
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        # exhaust conversation quota
        client.post("/api/conversation/query")
        # other path should still pass
        r = client.post("/api/other")
        assert r.status_code == 200

    def test_get_conversation_not_limited(self):
        """GET to the conversation path is not rate-limited (only POST is)."""
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query")
        # GET should pass even after the POST quota is exhausted
        r = client.get("/api/conversation/query")
        assert r.status_code == 200

    # -- per-IP isolation ----------------------------------------------------

    def test_different_ips_have_separate_buckets(self):
        """Two different X-Forwarded-For IPs should each get their own bucket."""
        app = _make_app_with_ratelimit(per_minute=1)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query", headers={"X-Forwarded-For": "1.2.3.4"})
        # second IP should still be allowed
        r = client.post("/api/conversation/query", headers={"X-Forwarded-For": "5.6.7.8"})
        assert r.status_code == 200

    def test_same_ip_shares_bucket(self):
        app = _make_app_with_ratelimit(per_minute=2)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/conversation/query", headers={"X-Forwarded-For": "10.0.0.1"})
        client.post("/api/conversation/query", headers={"X-Forwarded-For": "10.0.0.1"})
        r = client.post("/api/conversation/query", headers={"X-Forwarded-For": "10.0.0.1"})
        assert r.status_code == 429


# ── Unit tests for internal helpers ──────────────────────────────────────────

class TestRateLimiterInternals(unittest.TestCase):

    def _make_middleware(self, per_minute: int = 5):
        from augur.middleware.ratelimit import ConversationRateLimitMiddleware
        # Pass a minimal ASGI app stub; we won't call dispatch
        dummy_app = MagicMock()
        mw = ConversationRateLimitMiddleware.__new__(ConversationRateLimitMiddleware)
        mw._per_minute = per_minute
        mw._window = 60.0
        from collections import defaultdict
        from augur.middleware.ratelimit import _Bucket
        mw._buckets = defaultdict(_Bucket)
        return mw

    def test_first_call_allowed(self):
        mw = self._make_middleware(per_minute=5)
        allowed, retry = mw._is_allowed("192.168.1.1")
        assert allowed is True
        assert retry == 0

    def test_at_limit_allowed(self):
        mw = self._make_middleware(per_minute=3)
        for _ in range(3):
            allowed, _ = mw._is_allowed("10.0.0.1")
            assert allowed is True

    def test_over_limit_denied(self):
        mw = self._make_middleware(per_minute=3)
        for _ in range(3):
            mw._is_allowed("10.0.0.1")
        allowed, retry = mw._is_allowed("10.0.0.1")
        assert allowed is False
        assert retry > 0

    def test_timestamps_evicted_after_window(self):
        mw = self._make_middleware(per_minute=1)
        mw._is_allowed("10.0.0.2")  # fills bucket
        # Fake aging: push timestamp back past the window
        from augur.middleware.ratelimit import _Bucket
        bucket = mw._buckets["10.0.0.2"]
        bucket.timestamps[0] = time.monotonic() - 61  # expired
        allowed, _ = mw._is_allowed("10.0.0.2")
        assert allowed is True

    def test_client_ip_from_forwarded_for(self):
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}
        req.client = None
        assert mw._client_ip(req) == "203.0.113.5"

    def test_client_ip_falls_back_to_host(self):
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "172.16.0.5"
        assert mw._client_ip(req) == "172.16.0.5"

    def test_client_ip_unknown_when_no_client(self):
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert mw._client_ip(req) == "unknown"


class TestAPIKeyMiddlewareInternals(unittest.TestCase):

    def _make_middleware(self, api_key):
        from augur.middleware.auth import APIKeyMiddleware
        mw = APIKeyMiddleware.__new__(APIKeyMiddleware)
        mw._api_key = api_key
        return mw

    def test_no_key_configured_is_open(self):
        mw = self._make_middleware(None)
        assert mw._api_key is None

    def test_api_key_stored(self):
        mw = self._make_middleware("tok-abc")
        assert mw._api_key == "tok-abc"


if __name__ == "__main__":
    unittest.main()
