"""Coverage for the 3-part DISALLOWED_ACTIONS grammar."""
import pytest

import server
from server import _parse_disallowed_actions


ALL_RESOURCES = set(server.ENDPOINTS_BY_RESOURCE)


def test_two_part_form_still_works():
    pair_denies, endpoint_denies = _parse_disallowed_actions("connections:write", ALL_RESOURCES)
    # write cascades to delete on the same resource.
    assert ("connections", "write") in pair_denies
    assert ("connections", "delete") in pair_denies
    assert endpoint_denies == set()


def test_three_part_form_denies_one_endpoint():
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        "connections:write:sync_connection", ALL_RESOURCES
    )
    assert endpoint_denies == {"sync_connection"}
    assert pair_denies == set()


def test_three_part_form_does_not_cascade():
    # Denying a read endpoint touches only that endpoint — no cascade to write/delete.
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        "connections:read:list_connections", ALL_RESOURCES
    )
    assert pair_denies == set()
    assert endpoint_denies == {"list_connections"}


def test_two_and_three_part_mix():
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        "groups:delete, connections:write:sync_connection", ALL_RESOURCES
    )
    assert ("groups", "delete") in pair_denies
    assert endpoint_denies == {"sync_connection"}


def test_unknown_endpoint_name_fails_loudly():
    with pytest.raises(ValueError, match="Unknown endpoint"):
        _parse_disallowed_actions("connections:write:not_an_endpoint", ALL_RESOURCES)


def test_endpoint_wrong_pair_fails_loudly():
    # sync_connection is under connections:write, not connections:read.
    with pytest.raises(ValueError, match="belongs to"):
        _parse_disallowed_actions("connections:read:sync_connection", ALL_RESOURCES)


def test_unknown_resource_still_fails():
    with pytest.raises(ValueError, match="Unknown resource"):
        _parse_disallowed_actions("bogus:read:whatever", ALL_RESOURCES)


def test_bad_action_still_fails():
    with pytest.raises(ValueError, match="Invalid action"):
        _parse_disallowed_actions("connections:admin:whatever", ALL_RESOURCES)


def test_empty_string_means_no_denies():
    pair_denies, endpoint_denies = _parse_disallowed_actions("", ALL_RESOURCES)
    assert pair_denies == set()
    assert endpoint_denies == set()
