"""Coverage for do_call's GRANT_NOT_ALLOWED handling.

do_call returns a shaped error dict for caller-correctable failures rather than
raising. These tests exercise the SCOPE_TOO_LOW / EXPLICITLY_DISALLOWED
branches — the actual HTTP call path is exercised elsewhere (integration).
"""
import pytest

import server
from server import SCOPE_TIERS, configure, do_call, Credentials


CREDS = Credentials(authorization="Basic test")


@pytest.mark.asyncio
async def test_scope_too_low_returns_grant_not_allowed():
    configure(SCOPE_TIERS["read"], set())
    result = await do_call(CREDS, name="sync_connection",
                           path_params={"connectionId": "x"})
    assert result["error"] == "GRANT_NOT_ALLOWED"
    assert result["cause"] == "SCOPE_TOO_LOW"
    assert result["endpoint"] == "sync_connection"
    assert result["required_grant"] == "connections:write"


@pytest.mark.asyncio
async def test_pair_deny_returns_explicitly_disallowed():
    configure(
        SCOPE_TIERS["read/write/delete"],
        {("connections", "write"), ("connections", "delete")},
    )
    result = await do_call(CREDS, name="sync_connection",
                           path_params={"connectionId": "x"})
    assert result["cause"] == "EXPLICITLY_DISALLOWED"


@pytest.mark.asyncio
async def test_endpoint_deny_returns_explicitly_disallowed():
    configure(
        SCOPE_TIERS["read/write/delete"],
        set(),
        endpoint_denies={"sync_connection"},
    )
    result = await do_call(CREDS, name="sync_connection",
                           path_params={"connectionId": "x"})
    assert result["cause"] == "EXPLICITLY_DISALLOWED"
    assert "sync_connection" in result["endpoint_disallowed"]


@pytest.mark.asyncio
async def test_grant_not_allowed_shape_is_uniform_across_causes():
    # Both SCOPE_TOO_LOW and EXPLICITLY_DISALLOWED responses carry the same
    # keys so agents don't need to branch on cause to render.
    configure(SCOPE_TIERS["read"], set())
    scope_error = await do_call(CREDS, name="sync_connection",
                                path_params={"connectionId": "x"})

    configure(SCOPE_TIERS["read/write/delete"], set(),
              endpoint_denies={"sync_connection"})
    deny_error = await do_call(CREDS, name="sync_connection",
                               path_params={"connectionId": "x"})

    assert set(scope_error) == set(deny_error)


@pytest.mark.asyncio
async def test_grant_not_allowed_message_does_not_repeat_boilerplate():
    # The "use the dashboard or REST API" guidance lives once in
    # _SERVER_INSTRUCTIONS. Per-call messages stay short and stable.
    configure(SCOPE_TIERS["read/write/delete"], set(),
              endpoint_denies={"sync_connection"})
    result = await do_call(CREDS, name="sync_connection",
                           path_params={"connectionId": "x"})
    assert "dashboard" not in result["message"].lower()
    assert "rest api" not in result["message"].lower()
    assert result["message"].startswith("Endpoint 'sync_connection' is not callable")
