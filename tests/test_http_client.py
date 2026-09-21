"""Coverage for the shared outbound httpx.AsyncClient lifecycle (P6).

_fivetran_request used to open a new httpx.AsyncClient per call; these tests
pin the shared-client contract: get_http_client() only works inside
http_client_lifespan(), and _fivetran_request drives whatever client that
returns rather than creating its own.
"""
import httpx
import pytest

import server
from server import Credentials, get_http_client, http_client_lifespan, _fivetran_request


CREDS = Credentials(authorization="Basic test")


@pytest.mark.asyncio
async def test_get_http_client_raises_before_lifespan_entered():
    with pytest.raises(RuntimeError):
        get_http_client()


@pytest.mark.asyncio
async def test_lifespan_provides_configured_client():
    async with http_client_lifespan():
        client = get_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert client.timeout == server._HTTP_TIMEOUT


@pytest.mark.asyncio
async def test_get_http_client_raises_after_lifespan_exits():
    async with http_client_lifespan():
        pass
    with pytest.raises(RuntimeError):
        get_http_client()


class _FakeAsyncClient:
    """Records the kwargs _fivetran_request passes through to .request()."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def request(self, *, method, url, headers, params, json):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        )
        return self._response


def _install_fake_client(monkeypatch, response):
    fake = _FakeAsyncClient(response)
    monkeypatch.setattr(server, "_http_client", fake)
    return fake


@pytest.mark.asyncio
async def test_fivetran_request_uses_shared_client(monkeypatch):
    req = httpx.Request("GET", "https://api.fivetran.com/v1/connections/x")
    response = httpx.Response(200, request=req, json={"data": {"id": "x"}})
    fake = _install_fake_client(monkeypatch, response)

    result = await _fivetran_request(CREDS, "GET", "/v1/connections/x", params={"a": "1"})

    assert result == {"data": {"id": "x"}}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.fivetran.com/v1/connections/x"
    assert call["headers"] == server._get_auth_header(CREDS)
    assert call["params"] == {"a": "1"}
    assert call["json"] is None


@pytest.mark.asyncio
async def test_fivetran_request_short_circuits_on_empty_body(monkeypatch):
    req = httpx.Request("DELETE", "https://api.fivetran.com/v1/connections/x")
    response = httpx.Response(204, request=req)
    _install_fake_client(monkeypatch, response)

    result = await _fivetran_request(CREDS, "DELETE", "/v1/connections/x")

    assert result == {"status": "success", "code": 204}


@pytest.mark.asyncio
async def test_fivetran_request_propagates_http_status_error(monkeypatch):
    req = httpx.Request("GET", "https://api.fivetran.com/v1/connections/x")
    response = httpx.Response(
        404, request=req, json={"code": "NotFound_Object", "message": "not found"}
    )
    _install_fake_client(monkeypatch, response)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await _fivetran_request(CREDS, "GET", "/v1/connections/x")

    # call_tool's error handler reads e.response.json() / e.response.text
    # directly off the raised exception — pin that shape here too.
    assert exc_info.value.response.status_code == 404
    assert exc_info.value.response.json() == {"code": "NotFound_Object", "message": "not found"}
