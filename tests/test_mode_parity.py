"""P10: parity across both configure() entry points (stdio vs. hosted).

Prior tests exercise configure() directly with hand-built scope/deny args.
These drive the two *real* entry points instead — _parse_scope_and_denies_from_env()
(stdio) and build_http_app()'s HOSTED_DISALLOWED_ACTIONS parsing (hosted) — so a
wiring bug in either entry point itself, not just in configure(), would be caught.
"""
import pytest

import server
from server import Credentials, build_http_app, do_call, do_list_endpoints


CREDS = Credentials(authorization="Basic test")


def _configure_stdio(monkeypatch, scope="read/write/delete", disallowed=""):
    monkeypatch.setenv("FIVETRAN_SCOPE", scope)
    monkeypatch.setenv("DISALLOWED_ACTIONS", disallowed)
    monkeypatch.delenv("FIVETRAN_ALLOW_WRITES", raising=False)
    scope_actions, pair_denies, endpoint_denies = server._parse_scope_and_denies_from_env()
    server.configure(scope_actions, pair_denies, endpoint_denies, mode="stdio")


def _configure_hosted(monkeypatch, disallowed=""):
    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("FIVETRAN_AUTH_ISSUER", raising=False)
    monkeypatch.setattr(server, "HOSTED_DISALLOWED_ACTIONS", disallowed)
    build_http_app()


@pytest.mark.parametrize("configure_mode", ["stdio", "hosted"])
def test_discovery_never_filters_by_availability(monkeypatch, configure_mode):
    if configure_mode == "stdio":
        _configure_stdio(monkeypatch, disallowed="connections:write:sync_connection")
    else:
        _configure_hosted(monkeypatch, disallowed="connections:write:sync_connection")

    result = do_list_endpoints(category="connections")
    row = next(e for e in result["endpoints"] if e["name"] == "sync_connection")
    assert row["callable"] is False


@pytest.mark.parametrize("configure_mode", ["stdio", "hosted"])
def test_summary_carries_callable_counts_peer(monkeypatch, configure_mode):
    if configure_mode == "stdio":
        _configure_stdio(monkeypatch)
    else:
        _configure_hosted(monkeypatch)

    result = do_list_endpoints()
    assert set(result["callable_counts"]) == set(result["categories"])


@pytest.mark.asyncio
@pytest.mark.parametrize("configure_mode", ["stdio", "hosted"])
async def test_grant_not_allowed_shape_identical_across_modes(monkeypatch, configure_mode):
    if configure_mode == "stdio":
        _configure_stdio(monkeypatch, scope="read", disallowed="")
    else:
        # Hosted scope is always read/write; deny the same endpoint explicitly instead.
        _configure_hosted(monkeypatch, disallowed="connections:write:sync_connection")

    result = await do_call(CREDS, name="sync_connection", path_params={"connectionId": "x"})

    assert result["error"] == "GRANT_NOT_ALLOWED"
    assert set(result) == {
        "error", "cause", "endpoint", "required_grant", "scope",
        "disallowed", "endpoint_disallowed", "message",
    }


@pytest.mark.parametrize("bad_token", ["bogus:write", "connections:bogus:sync_connection"])
def test_invalid_disallowed_actions_token_rejected_in_both_entry_points(monkeypatch, bad_token):
    monkeypatch.setenv("FIVETRAN_SCOPE", "read/write/delete")
    monkeypatch.setenv("DISALLOWED_ACTIONS", bad_token)
    monkeypatch.delenv("FIVETRAN_ALLOW_WRITES", raising=False)
    with pytest.raises(ValueError):
        server._parse_scope_and_denies_from_env()

    monkeypatch.delenv("FIVETRAN_API_KEY", raising=False)
    monkeypatch.delenv("FIVETRAN_API_SECRET", raising=False)
    monkeypatch.delenv("FIVETRAN_AUTH_ISSUER", raising=False)
    monkeypatch.setattr(server, "HOSTED_DISALLOWED_ACTIONS", bad_token)
    with pytest.raises(ValueError):
        build_http_app()


def test_hosted_disallowed_actions_applied(monkeypatch):
    _configure_hosted(monkeypatch, disallowed="connections:write:sync_connection")

    assert "sync_connection" in server.ENDPOINT_DENIES
    result = do_list_endpoints(category="connections")
    row = next(e for e in result["endpoints"] if e["name"] == "sync_connection")
    assert row["callable"] is False
