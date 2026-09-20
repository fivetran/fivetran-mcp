"""Coverage for the streamable-http transport (P1).

Drives the real Starlette app + StreamableHTTPSessionManager stack end-to-end
over an in-process ASGI transport, using the SDK's own client-side
streamable_http_client + ClientSession rather than hand-rolled JSON-RPC frames
or mocks. httpx.ASGITransport only handles the "http" scope, not lifespan, so
each test enters `app.router.lifespan_context(app)` directly to start/stop
the StreamableHTTPSessionManager and the shared outbound HTTP client.
"""
import contextlib

import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

import server
from server import Credentials, TRANSPORT_MODES, build_http_app


MCP_URL = "http://testserver/mcp"


def _find_exception(exc: BaseException, exc_type: type) -> BaseException | None:
    """anyio task groups wrap failures in (nested) BaseExceptionGroups; dig one out."""
    if isinstance(exc, exc_type):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_exception(sub, exc_type)
            if found is not None:
                return found
    return None


@contextlib.asynccontextmanager
async def _running_app(headers: dict[str, str] | None = None):
    """Build the HTTP app, run its lifespan, and yield a connected ClientSession."""
    app = server.build_http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, headers=headers) as http_client:
            async with streamable_http_client(MCP_URL, http_client=http_client) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


@pytest.fixture(autouse=True)
def _no_shared_key(monkeypatch):
    # build_http_app() would otherwise fail eagerly (P2) in a dev shell with
    # FIVETRAN_API_KEY/SECRET already exported.
    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)


def test_build_http_app_configures_grants_from_env(monkeypatch):
    monkeypatch.setenv("FIVETRAN_SCOPE", "read/write")
    build_http_app()

    assert server.MODE == "streamable-http"
    assert all(action != "delete" for _resource, action in server.TOOLS_BY_NAME.values())
    tool_names = {t.name for t in server._TOOLS}
    assert {"list_endpoints", "get_schema"}.issubset(tool_names)
    assert not any(name.endswith("_delete") for name in tool_names)


def test_build_http_app_discovery_marks_delete_unavailable(monkeypatch):
    monkeypatch.setenv("FIVETRAN_SCOPE", "read/write")
    build_http_app()

    result = server.do_list_endpoints(category="connections")
    delete_rows = [e for e in result["endpoints"] if e["scope"] == "delete"]
    assert delete_rows
    assert all(not e["callable"] for e in delete_rows)


def test_build_http_app_fails_if_shared_key_env_set(monkeypatch):
    monkeypatch.setenv("FIVETRAN_API_KEY", "key123")
    monkeypatch.setenv("FIVETRAN_API_SECRET", "secret456")
    with pytest.raises(ValueError, match="FIVETRAN_API_KEY"):
        build_http_app()


@pytest.mark.asyncio
async def test_mcp_initialize_and_tools_list_over_http(monkeypatch):
    monkeypatch.setenv("FIVETRAN_SCOPE", "read/write")
    async with _running_app() as session:
        result = await session.list_tools()
        names = {t.name for t in result.tools}
        assert {"list_endpoints", "get_schema"}.issubset(names)
        assert not any(n.endswith("_delete") for n in names)


@pytest.mark.asyncio
async def test_request_context_request_populated_under_http(monkeypatch):
    captured: dict[str, Credentials] = {}

    async def _fake_request(creds, method, endpoint, params=None, json_body=None):
        captured["creds"] = creds
        return {"status": "success", "code": 200}

    monkeypatch.setattr(server, "_fivetran_request", _fake_request)

    async with _running_app(headers={"Authorization": "Bearer testtoken"}) as session:
        read_tool = next(
            name for name, pair in server.TOOLS_BY_NAME.items() if pair[1] == "read"
        )
        endpoint = next(
            e["name"] for e in server.ENDPOINTS_BY_RESOURCE[server.TOOLS_BY_NAME[read_tool][0]]
            if e["scope"] == "read" and not e.get("deprecated") and not e.get("parameters")
        )
        await session.call_tool(read_tool, {"name": endpoint})

    assert captured["creds"] == Credentials(authorization="Bearer testtoken")


@pytest.mark.asyncio
async def test_disallowed_origin_rejected_with_configured_origins(monkeypatch):
    # allowed_hosts must also be configured, or the Host header ("testserver",
    # from the test URL) fails validation first (421) instead of Origin (403).
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://allowed.example")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    with pytest.raises(BaseException) as exc_info:
        async with _running_app(headers={"Origin": "https://evil.example"}) as session:
            await session.list_tools()

    status_error = _find_exception(exc_info.value, httpx.HTTPStatusError)
    assert status_error is not None
    assert status_error.response.status_code == 403


@pytest.mark.asyncio
async def test_allowed_origin_accepted(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://allowed.example")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
    async with _running_app(headers={"Origin": "https://allowed.example"}) as session:
        result = await session.list_tools()
        assert any(t.name == "list_endpoints" for t in result.tools)


def test_no_origin_config_warns_and_disables_protection(capsys):
    server.build_http_app()
    captured = capsys.readouterr()
    assert "MCP_ALLOWED_ORIGINS" in captured.err


@pytest.mark.asyncio
async def test_arbitrary_origin_accepted_when_protection_disabled():
    async with _running_app(headers={"Origin": "https://anything.example"}) as session:
        result = await session.list_tools()
        assert any(t.name == "list_endpoints" for t in result.tools)


def test_arg_parser_defaults(monkeypatch):
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    args = server._build_arg_parser().parse_args([])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_arg_parser_env_var_precedence(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")
    args = server._build_arg_parser().parse_args([])
    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_arg_parser_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    args = server._build_arg_parser().parse_args(["--transport", "stdio", "--port", "1234"])
    assert args.transport == "stdio"
    assert args.port == 1234


def test_arg_parser_rejects_unknown_transport():
    with pytest.raises(SystemExit):
        server._build_arg_parser().parse_args(["--transport", "bogus"])


def test_transport_modes_constant_matches_arg_parser_choices():
    parser = server._build_arg_parser()
    transport_action = next(a for a in parser._actions if a.dest == "transport")
    assert set(transport_action.choices) == set(TRANSPORT_MODES)
