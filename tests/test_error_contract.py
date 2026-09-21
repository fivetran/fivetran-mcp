"""Coverage for the unified error contract (P8).

do_call/do_get_schema return shaped {"error": CODE, ..., "message": ...}
dicts for caller-correctable failures instead of raising; call_tool shapes
UNKNOWN_TOOL/CREDENTIALS_MISSING the same way. Upstream 5xx and transport
errors still raise — the SDK's own @mcp_server.call_tool() decorator turns
an uncaught exception into a proper isError=True result, so letting them
propagate is not letting them go unhandled.

Reuses the existing "construct real httpx.Request/Response objects, no
mocking library" style from tests/test_http_client.py.
"""
import httpx
import pytest

import server
from server import (
    SCOPE_TIERS,
    Credentials,
    CredentialsError,
    configure,
    do_call,
    do_get_schema,
    call_tool,
    set_credentials_resolver,
)


CREDS = Credentials(authorization="Basic test")


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def request(self, *, method, url, headers, params, json, timeout=None):
        return self._response


def _install_fake_client(monkeypatch, response):
    monkeypatch.setattr(server, "_http_client", _FakeAsyncClient(response))


def _grant_write():
    configure(SCOPE_TIERS["read/write/delete"], set())


# --- do_call: caller-correctable input errors -------------------------------


@pytest.mark.asyncio
async def test_do_call_unknown_endpoint():
    _grant_write()
    result = await do_call(CREDS, name="not_a_real_endpoint")
    assert result == {
        "error": "UNKNOWN_ENDPOINT",
        "endpoint": "not_a_real_endpoint",
        "message": "Unknown endpoint: 'not_a_real_endpoint'. Discoverable via list_endpoints.",
    }


@pytest.mark.asyncio
async def test_do_call_missing_path_param():
    _grant_write()
    result = await do_call(CREDS, name="sync_connection", path_params={})
    assert result["error"] == "MISSING_PATH_PARAM"
    assert result["endpoint"] == "sync_connection"
    assert result["missing"] == ["connectionId"]


@pytest.mark.asyncio
async def test_do_call_invalid_body():
    _grant_write()
    result = await do_call(
        CREDS, name="sync_connection", path_params={"connectionId": "x"}, body="{not json"
    )
    assert result["error"] == "INVALID_BODY"
    assert result["endpoint"] == "sync_connection"


# --- do_call: upstream 4xx classification ------------------------------------


@pytest.mark.asyncio
async def test_do_call_401_shapes_upstream_unauthorized(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(401, request=req, json={"code": "Unauthorized", "message": "bad creds"})
    _install_fake_client(monkeypatch, resp)

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result == {"error": "UPSTREAM_UNAUTHORIZED", "status": 401, "message": "bad creds"}


@pytest.mark.asyncio
async def test_do_call_403_shapes_upstream_forbidden(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(403, request=req, json={})
    _install_fake_client(monkeypatch, resp)

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result["error"] == "UPSTREAM_FORBIDDEN"
    assert result["status"] == 403
    assert "RBAC role" in result["message"]


@pytest.mark.asyncio
async def test_do_call_429_with_retry_after(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(429, request=req, headers={"Retry-After": "30"}, json={})
    _install_fake_client(monkeypatch, resp)

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result["error"] == "UPSTREAM_RATE_LIMITED"
    assert result["retry_after"] == "30"


@pytest.mark.asyncio
async def test_do_call_429_without_retry_after(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(429, request=req, json={})
    _install_fake_client(monkeypatch, resp)

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result["error"] == "UPSTREAM_RATE_LIMITED"
    assert "retry_after" not in result


@pytest.mark.asyncio
async def test_do_call_other_4xx_shapes_upstream_error(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(
        422, request=req, json={"code": "Invalid_Config", "message": "bad config"}
    )
    _install_fake_client(monkeypatch, resp)

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result == {
        "error": "UPSTREAM_ERROR",
        "status": 422,
        "code": "Invalid_Config",
        "message": "bad config",
    }


@pytest.mark.asyncio
async def test_do_call_timeout_raises_with_readable_message(monkeypatch):
    """httpx timeout exceptions stringify to "", which reaches the caller as an
    error with no text — indistinguishable from a write that never ran."""
    _grant_write()

    class _TimingOutClient:
        async def request(self, *, method, url, headers, params, json, timeout=None):
            raise httpx.ReadTimeout("", request=httpx.Request(method, url))

    monkeypatch.setattr(server, "_http_client", _TimingOutClient())

    with pytest.raises(server.UpstreamTimeout) as exc_info:
        await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert str(exc_info.value) == (
        "The request timed out but the action may still have been "
        "successful upstream. Check before retrying."
    )
    # Still an httpx transport error, so existing handling is unchanged.
    assert isinstance(exc_info.value, httpx.TimeoutException)


@pytest.mark.asyncio
async def test_do_call_5xx_still_raises(monkeypatch):
    _grant_write()
    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(500, request=req, text="internal error")
    _install_fake_client(monkeypatch, resp)

    with pytest.raises(httpx.HTTPStatusError):
        await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})


# --- do_get_schema ------------------------------------------------------------


def test_do_get_schema_unknown_endpoint():
    result = do_get_schema("not_a_real_endpoint")
    assert result == {
        "error": "UNKNOWN_ENDPOINT",
        "endpoint": "not_a_real_endpoint",
        "message": "Unknown endpoint: 'not_a_real_endpoint'. Discoverable via list_endpoints.",
    }


# --- call_tool: UNKNOWN_TOOL / CREDENTIALS_MISSING ----------------------------


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_name():
    result = await call_tool("not_a_real_tool", {})
    assert len(result) == 1
    import json
    body = json.loads(result[0].text)
    assert body == {"error": "UNKNOWN_TOOL", "message": "Unknown tool: 'not_a_real_tool'"}


@pytest.mark.asyncio
async def test_call_tool_credentials_missing(monkeypatch):
    _grant_write()

    async def _raise_creds_error():
        raise CredentialsError("no key configured")

    set_credentials_resolver(_raise_creds_error)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    result = await call_tool(tool_name, {"name": "sync_connection", "path_params": {"connectionId": "x"}})

    import json
    body = json.loads(result[0].text)
    assert body == {"error": "CREDENTIALS_MISSING", "message": "no key configured"}


@pytest.mark.asyncio
async def test_call_tool_5xx_propagates_end_to_end(monkeypatch):
    _grant_write()

    async def _resolver():
        return CREDS

    set_credentials_resolver(_resolver)

    req = httpx.Request("POST", "https://api.fivetran.com/v1/connections/x/sync")
    resp = httpx.Response(500, request=req, text="internal error")
    _install_fake_client(monkeypatch, resp)

    tool_name = next(n for n, pair in server.TOOLS_BY_NAME.items() if pair == ("connections", "write"))
    with pytest.raises(httpx.HTTPStatusError):
        await call_tool(tool_name, {"name": "sync_connection", "path_params": {"connectionId": "x"}})
