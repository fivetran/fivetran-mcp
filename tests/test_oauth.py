"""Coverage for OAuth resource-server plumbing (auth.py + build_http_app wiring).

FivetranOAuthTokenVerifier.verify_token has no real body yet (see auth.py),
so there's no real crypto to test against. These tests drive the real
Starlette/StreamableHTTPSessionManager stack end-to-end over
httpx.ASGITransport (same no-mocking style as test_http_transport.py),
substituting a trivial in-memory fake TokenVerifier via
build_http_app(token_verifier=...) to exercise the gate itself: the
well-known route, the 401 + WWW-Authenticate response, and a valid token
reaching tool dispatch.
"""
import httpx
import pytest
from mcp.server.auth.provider import AccessToken

import auth
import server
from server import build_http_app, select_credentials_resolver


RESOURCE_URL = "http://testserver/mcp"
ISSUER = "https://auth.fivetran.com"


class _FakeVerifier:
    """Accepts the literal token "good", rejects everything else."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "good":
            return None
        return AccessToken(token=token, client_id="test-client", scopes=[], resource=RESOURCE_URL)


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("FIVETRAN_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("MCP_RESOURCE_URL", raising=False)


async def _post_mcp(app, headers=None):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            return await client.post("/mcp", json={}, headers=headers)


async def _get(app, path):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            return await client.get(path)


def test_oauth_mode_off_by_default_no_well_known_route():
    app = build_http_app()
    assert not any(getattr(r, "path", "").startswith("/.well-known") for r in app.routes)


def test_oauth_mode_off_by_default_selects_header_resolver():
    resolver = select_credentials_resolver("streamable-http")
    assert resolver.__qualname__.startswith("header_resolver")


@pytest.mark.asyncio
async def test_oauth_mode_off_mcp_still_works_unauthenticated():
    app = build_http_app()
    resp = await _post_mcp(
        app,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    # No 401 gate at all when FIVETRAN_AUTH_ISSUER is unset — an empty/invalid
    # JSON-RPC body still gets routed to the MCP app itself (400/406-class
    # response from the session manager, never a bearer-auth 401).
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_oauth_mode_on_well_known_route_shape(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_RESOURCE_URL", RESOURCE_URL)
    app = build_http_app(token_verifier=_FakeVerifier())
    resp = await _get(app, "/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == RESOURCE_URL
    assert body["authorization_servers"] == [f"{ISSUER}/"]


@pytest.mark.asyncio
async def test_oauth_mode_on_missing_token_401(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_RESOURCE_URL", RESOURCE_URL)
    app = build_http_app(token_verifier=_FakeVerifier())
    resp = await _post_mcp(app)
    assert resp.status_code == 401
    www_auth = resp.headers["www-authenticate"]
    assert "Bearer" in www_auth
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' in www_auth


@pytest.mark.asyncio
async def test_oauth_mode_on_invalid_token_401(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_RESOURCE_URL", RESOURCE_URL)
    app = build_http_app(token_verifier=_FakeVerifier())
    resp = await _post_mcp(app, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_oauth_mode_on_valid_token_reaches_dispatch(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_RESOURCE_URL", RESOURCE_URL)
    app = build_http_app(token_verifier=_FakeVerifier())
    resp = await _post_mcp(
        app,
        headers={
            "Authorization": "Bearer good",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    # Reaches the MCP app itself (an empty JSON-RPC body is invalid there,
    # but that's a 400-class response from session dispatch, not a 401 from
    # the auth gate) — proves the valid token cleared RequireAuthMiddleware.
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_fivetran_oauth_token_verifier_not_implemented():
    with pytest.raises(NotImplementedError):
        await auth.FivetranOAuthTokenVerifier().verify_token("anything")


def test_malformed_issuer_fails_loudly(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", "not-a-url")
    with pytest.raises(Exception):
        build_http_app()


def test_non_https_issuer_fails_loudly(monkeypatch):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", "http://auth.fivetran.com")
    with pytest.raises(ValueError, match="HTTPS"):
        build_http_app()


def test_stdio_warns_but_ignores_http_only_env_vars(monkeypatch, capsys):
    monkeypatch.setenv("FIVETRAN_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_RESOURCE_URL", RESOURCE_URL)

    server._warn_ignored_env_vars(server._HTTP_ONLY_ENV_VARS, "they only take effect in HTTP mode.")

    captured = capsys.readouterr()
    assert "FIVETRAN_AUTH_ISSUER" in captured.err
    assert "MCP_RESOURCE_URL" in captured.err


def test_warn_ignored_env_vars_silent_when_none_set(capsys):
    server._warn_ignored_env_vars(server._HTTP_ONLY_ENV_VARS, "irrelevant here.")
    captured = capsys.readouterr()
    assert captured.err == ""
