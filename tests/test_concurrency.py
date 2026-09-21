"""P10: concurrency — the shared outbound httpx.AsyncClient (P6) and the
stateless-per-request session design (P1) must never mix up which caller's
credentials go out with which request. Each caller's token flows through
do_call's `creds` parameter and, in HTTP mode, through a fresh per-request
ServerSession — never any shared/module-level state — so N concurrent
callers with distinct bearer tokens must each see their own token reach
the outbound Fivetran call, exactly once.
"""
import asyncio

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

import server
from server import Credentials, SCOPE_TIERS, build_http_app, configure, do_call


CREDS = Credentials(authorization="Basic test")
MCP_URL = "http://testserver/mcp"


class _FakeAsyncClient:
    """Echoes back the Authorization header it was called with, after
    yielding control, so overlapping calls actually interleave."""

    async def request(self, *, method, url, headers, params, json, timeout=None):
        await asyncio.sleep(0)
        req = httpx.Request(method, url)
        return httpx.Response(200, request=req, json={"seen_auth": headers["Authorization"]})


@pytest.mark.asyncio
async def test_concurrent_do_calls_do_not_leak_credentials_across_callers(monkeypatch):
    configure(SCOPE_TIERS["read"], set())
    monkeypatch.setattr(server, "_http_client", _FakeAsyncClient())

    endpoint = next(
        e["name"] for e in server.ENDPOINTS_BY_RESOURCE["connections"]
        if e["scope"] == "read" and not e.get("deprecated") and "{" not in e["path"]
    )

    async def _call(i):
        creds = Credentials(authorization=f"Bearer token-{i}")
        result = await do_call(creds, name=endpoint)
        return i, result["seen_auth"]

    results = await asyncio.gather(*(_call(i) for i in range(20)))
    for i, seen_auth in results:
        assert seen_auth == f"Bearer token-{i}"


@pytest.fixture(autouse=True)
def _no_shared_key(monkeypatch):
    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("FIVETRAN_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)


@pytest.mark.asyncio
async def test_concurrent_stateless_http_sessions_keep_credentials_isolated(monkeypatch):
    captured: list[str] = []
    lock = asyncio.Lock()

    async def _fake_request(creds, method, endpoint, params=None, json_body=None):
        await asyncio.sleep(0.01)
        async with lock:
            captured.append(creds.authorization)
        return {"status": "success", "code": 200}

    monkeypatch.setattr(server, "_fivetran_request", _fake_request)

    app = build_http_app()
    read_tool = next(name for name, pair in server.TOOLS_BY_NAME.items() if pair[1] == "read")
    endpoint = next(
        e["name"] for e in server.ENDPOINTS_BY_RESOURCE[server.TOOLS_BY_NAME[read_tool][0]]
        if e["scope"] == "read" and not e.get("deprecated") and "{" not in e["path"]
    )

    async def _run_session(i):
        token = f"Bearer token-{i}"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, headers={"Authorization": token}) as http_client:
            async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _sid):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.call_tool(read_tool, {"name": endpoint})
        return token

    async with app.router.lifespan_context(app):
        tokens = await asyncio.gather(*(_run_session(i) for i in range(8)))

    assert sorted(captured) == sorted(tokens)
    assert len(captured) == len(set(captured))
