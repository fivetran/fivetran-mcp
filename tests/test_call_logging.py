"""Coverage for P9's per-call structured logging (_resolve_client_name,
_build_call_record, _record_tool_call) and its wiring into call_tool.

_resolve_client_name is tested against lightweight fake objects monkeypatched
onto server.mcp_server rather than the real SDK's contextvar-backed
request_context — it just reads whatever server.mcp_server currently is.

call_tool logging is tested end-to-end, calling the undecorated function
directly (same style as tests/test_error_contract.py), capturing stdout/
stderr with capsys and parsing the emitted JSON line.
"""
import json

import httpx
import pytest

import server
from server import (
    SCOPE_TIERS,
    Credentials,
    call_tool,
    configure,
    set_credentials_resolver,
)


CREDS = Credentials(authorization="Basic test")


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def request(self, *, method, url, headers, params, json):
        return self._response


def _install_fake_client(monkeypatch, response):
    monkeypatch.setattr(server, "_http_client", _FakeAsyncClient(response))


def _grant_write():
    configure(SCOPE_TIERS["read/write/delete"], set())


async def _fake_resolver():
    return CREDS


# --- _resolve_client_name -----------------------------------------------------


class _ClientInfo:
    def __init__(self, name):
        self.name = name


class _ClientParams:
    def __init__(self, name):
        self.clientInfo = _ClientInfo(name)


class _Session:
    def __init__(self, client_params):
        self.client_params = client_params


class _RequestContext:
    def __init__(self, session=None, request=None):
        self.session = session
        self.request = request


class _FakeServer:
    def __init__(self, request_context):
        self.request_context = request_context


class _RaisingServer:
    @property
    def request_context(self):
        raise LookupError("no request context")


def test_resolve_client_name_stdio_uses_client_info(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(session=_Session(_ClientParams("Claude Code"))))
    )
    assert server._resolve_client_name() == "Claude Code"


def test_resolve_client_name_stdio_no_client_params(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(session=_Session(None))))
    assert server._resolve_client_name() == "unknown"


def test_resolve_client_name_stdio_no_request_context(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(server, "mcp_server", _RaisingServer())
    assert server._resolve_client_name() == "unknown"


def test_resolve_client_name_http_uses_user_agent(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    fake_request = type("Req", (), {"headers": {"User-Agent": "Cursor/1.2"}})()
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(request=fake_request)))
    assert server._resolve_client_name() == "Cursor/1.2"


def test_resolve_client_name_http_no_request(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(request=None)))
    assert server._resolve_client_name() == "unknown"


def test_resolve_client_name_http_no_request_context(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _RaisingServer())
    assert server._resolve_client_name() == "unknown"


# --- call_tool: structured logging --------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_logs_to_stderr_in_stdio_mode(capsys):
    server.MODE = "stdio"
    await call_tool("list_endpoints", {})
    captured = capsys.readouterr()
    assert captured.out == ""
    record = json.loads(captured.err.strip())
    assert record["mode"] == "stdio"
    assert record["tool"] == "list_endpoints"
    assert record["endpoint"] is None
    assert record["upstream_status"] is None
    assert record["client"] == "unknown"
    assert isinstance(record["request_id"], str) and record["request_id"]
    assert isinstance(record["latency_ms"], (int, float))


@pytest.mark.asyncio
async def test_call_tool_logs_to_stdout_in_http_mode(capsys):
    server.MODE = "streamable-http"
    await call_tool("get_schema", {"name": "list_connections"})
    captured = capsys.readouterr()
    assert captured.err == ""
    record = json.loads(captured.out.strip())
    assert record["mode"] == "streamable-http"
    assert record["tool"] == "get_schema"
    assert record["endpoint"] == "list_connections"


@pytest.mark.asyncio
async def test_call_tool_logs_grant_not_allowed_with_no_upstream_status(capsys):
    server.MODE = "stdio"
    # No grants configured (reset_server_state leaves ALLOWED_GRANTS empty).
    configure(SCOPE_TIERS["read"], set())
    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "read"))
    await call_tool(tool_name, {"name": "list_connections"})
    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert record["tool"] == tool_name
    assert record["endpoint"] == "list_connections"
    assert record["upstream_status"] is None


@pytest.mark.asyncio
async def test_call_tool_logs_upstream_403(capsys, monkeypatch):
    server.MODE = "stdio"
    _grant_write()
    set_credentials_resolver(_fake_resolver)
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(403, request=req, json={})
    _install_fake_client(monkeypatch, resp)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    await call_tool(tool_name, {"name": "sync_connection", "path_params": {"connectionId": "x"}})

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert record["upstream_status"] == 403


@pytest.mark.asyncio
async def test_call_tool_logs_upstream_200(capsys, monkeypatch):
    server.MODE = "stdio"
    _grant_write()
    set_credentials_resolver(_fake_resolver)
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(200, request=req, json={"code": "Success"})
    _install_fake_client(monkeypatch, resp)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    await call_tool(tool_name, {"name": "sync_connection", "path_params": {"connectionId": "x"}})

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert record["upstream_status"] == 200


@pytest.mark.asyncio
async def test_call_tool_logs_upstream_5xx_before_raising(capsys, monkeypatch):
    server.MODE = "stdio"
    _grant_write()
    set_credentials_resolver(_fake_resolver)
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(500, request=req, text="internal error")
    _install_fake_client(monkeypatch, resp)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    with pytest.raises(httpx.HTTPStatusError):
        await call_tool(tool_name, {"name": "sync_connection", "path_params": {"connectionId": "x"}})

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert record["upstream_status"] == 500


@pytest.mark.asyncio
async def test_call_tool_never_logs_request_body(capsys, monkeypatch):
    server.MODE = "stdio"
    _grant_write()
    set_credentials_resolver(_fake_resolver)
    req = httpx.Request("PATCH", "https://api.fivetran.com/v1/connections/x")
    resp = httpx.Response(200, request=req, json={"code": "Success"})
    _install_fake_client(monkeypatch, resp)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    secret = "sk_live_totally_secret_marker"
    await call_tool(
        tool_name,
        {
            "name": "modify_connection",
            "path_params": {"connectionId": "x"},
            "body": {"config": {"api_key": secret}},
        },
    )

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
