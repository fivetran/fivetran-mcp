"""Coverage for GET /health.

Reachable without a running Fivetran session or credentials, and — the
important property, since it's a load-balancer health check — not gated by
auth.require_oauth even when FIVETRAN_AUTH_ISSUER is configured.
"""
import httpx
import pytest

import server
from server import build_http_app


async def _get(app, path):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            return await client.get(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("FIVETRAN_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("MCP_RESOURCE_URL", raising=False)


@pytest.mark.asyncio
async def test_health_returns_ok_with_version_and_checksum():
    app = build_http_app()
    resp = await _get(app, "/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "ok",
        "version": server.__version__,
        "manifest_checksum": server.MANIFEST_CHECKSUM,
    }
    assert len(body["manifest_checksum"]) == 64  # sha256 hex digest


@pytest.mark.asyncio
async def test_health_unauthenticated_even_with_oauth_configured(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", "https://auth.fivetran.com")
    app = build_http_app()
    resp = await _get(app, "/health")
    assert resp.status_code == 200
