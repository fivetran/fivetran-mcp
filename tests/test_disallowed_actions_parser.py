"""Coverage for the 3-part DISALLOWED_ACTIONS grammar."""
import pytest

import server
from server import _parse_disallowed_actions


ALL_RESOURCES = set(server.ENDPOINTS_BY_RESOURCE)


def test_two_part_form_still_works(monkeypatch):
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:write")
    pair_denies, endpoint_denies = _parse_disallowed_actions(ALL_RESOURCES)
    # write cascades to delete on the same resource.
    assert ("connections", "write") in pair_denies
    assert ("connections", "delete") in pair_denies
    assert endpoint_denies == set()


def test_three_part_form_denies_one_endpoint(monkeypatch):
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:write:sync_connection")
    pair_denies, endpoint_denies = _parse_disallowed_actions(ALL_RESOURCES)
    assert endpoint_denies == {"sync_connection"}
    assert pair_denies == set()


def test_three_part_form_does_not_cascade(monkeypatch):
    # Denying a read endpoint touches only that endpoint — no cascade to write/delete.
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:read:list_connections")
    pair_denies, endpoint_denies = _parse_disallowed_actions(ALL_RESOURCES)
    assert pair_denies == set()
    assert endpoint_denies == {"list_connections"}


def test_two_and_three_part_mix(monkeypatch):
    monkeypatch.setenv(
        "DISALLOWED_ACTIONS",
        "groups:delete, connections:write:sync_connection",
    )
    pair_denies, endpoint_denies = _parse_disallowed_actions(ALL_RESOURCES)
    assert ("groups", "delete") in pair_denies
    assert endpoint_denies == {"sync_connection"}


def test_unknown_endpoint_name_fails_loudly(monkeypatch):
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:write:not_an_endpoint")
    with pytest.raises(ValueError, match="Unknown endpoint"):
        _parse_disallowed_actions(ALL_RESOURCES)


def test_endpoint_wrong_pair_fails_loudly(monkeypatch):
    # sync_connection is under connections:write, not connections:read.
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:read:sync_connection")
    with pytest.raises(ValueError, match="belongs to"):
        _parse_disallowed_actions(ALL_RESOURCES)


def test_unknown_resource_still_fails(monkeypatch):
    monkeypatch.setenv("DISALLOWED_ACTIONS", "bogus:read:whatever")
    with pytest.raises(ValueError, match="Unknown resource"):
        _parse_disallowed_actions(ALL_RESOURCES)


def test_bad_action_still_fails(monkeypatch):
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:admin:whatever")
    with pytest.raises(ValueError, match="Invalid action"):
        _parse_disallowed_actions(ALL_RESOURCES)
