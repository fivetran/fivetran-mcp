"""Regression coverage for the P5 Chunk A refactor.

The refactor moved import-time grant computation and tool generation into
`configure(scope_actions, pair_denies)`. These tests pin the observable outputs
(_TOOLS, TOOLS_BY_NAME, ALLOWED_GRANTS) at each scope tier so future changes to
the grant model can't silently drift.
"""
import server
from server import SCOPE_TIERS, configure


ALL_RESOURCES = set(server.ENDPOINTS_BY_RESOURCE)
DISCOVERY_NAMES = {"list_endpoints", "get_schema"}


def _tool_names() -> set[str]:
    return {t.name for t in server._TOOLS}


def _generated_names() -> set[str]:
    return _tool_names() - DISCOVERY_NAMES


def _tools_index_names(scope_actions: tuple[str, ...]) -> set[str]:
    """Expected generated tool names for a given scope, straight from the manifest."""
    return {t["name"] for t in server.TOOLS_INDEX if t["action"] in scope_actions}


def test_configure_read_scope():
    configure(SCOPE_TIERS["read"], set())

    assert server.SCOPE_ACTIONS == ("read",)
    assert server.ALLOWED_GRANTS == {(r, "read") for r in ALL_RESOURCES}
    assert _generated_names() == _tools_index_names(SCOPE_TIERS["read"])
    assert set(server.TOOLS_BY_NAME) == _generated_names()
    assert DISCOVERY_NAMES.issubset(_tool_names())


def test_configure_read_write_scope():
    configure(SCOPE_TIERS["read/write"], set())

    assert server.SCOPE_ACTIONS == ("read", "write")
    assert _generated_names() == _tools_index_names(SCOPE_TIERS["read/write"])
    assert all(name not in server.TOOLS_BY_NAME for name in server.TOOLS_BY_NAME
               if server.TOOLS_BY_NAME[name][1] == "delete")


def test_configure_read_write_delete_scope():
    configure(SCOPE_TIERS["read/write/delete"], set())

    assert server.SCOPE_ACTIONS == ("read", "write", "delete")
    # Every (resource, action) in the manifest tools index becomes a tool.
    assert _generated_names() == {t["name"] for t in server.TOOLS_INDEX}
    assert len(server.GENERATED_TOOLS) == len(server.TOOLS_INDEX)


def test_configure_pair_deny_cascade_at_call_site():
    # The parser cascades denies (ACTION_CASCADE) before it reaches configure().
    # Simulate a cascaded parse result to prove configure() honors what's passed.
    denies = {("connections", "read"), ("connections", "write"), ("connections", "delete")}
    configure(SCOPE_TIERS["read/write/delete"], denies)

    for pair in denies:
        assert pair not in server.ALLOWED_GRANTS
    assert not any(server.TOOLS_BY_NAME[n][0] == "connections" for n in server.TOOLS_BY_NAME)


def test_configure_is_idempotent():
    configure(SCOPE_TIERS["read/write"], set())
    first_tools = list(server._TOOLS)
    first_by_name = dict(server.TOOLS_BY_NAME)
    first_grants = set(server.ALLOWED_GRANTS)

    configure(SCOPE_TIERS["read/write"], set())

    assert [t.name for t in server._TOOLS] == [t.name for t in first_tools]
    assert server.TOOLS_BY_NAME == first_by_name
    assert server.ALLOWED_GRANTS == first_grants


def test_configure_switches_cleanly():
    configure(SCOPE_TIERS["read"], set())
    configure(SCOPE_TIERS["read/write"], set())
    after_switch = _generated_names()

    configure(SCOPE_TIERS["read/write"], set())
    fresh = _generated_names()

    assert after_switch == fresh


def test_discovery_tools_always_present_even_with_empty_scope():
    configure((), set())

    assert server.ALLOWED_GRANTS == set()
    assert server.GENERATED_TOOLS == []
    assert server.TOOLS_BY_NAME == {}
    assert _tool_names() == DISCOVERY_NAMES


def test_account_read_carries_openai_profile_meta():
    # _TOOL_META splice survives the refactor.
    configure(SCOPE_TIERS["read"], set())

    account_read = next(t for t in server._TOOLS if t.name == "account_read")
    assert account_read.meta == {"openai/profile": True}


def test_parse_scope_and_denies_from_env_defaults(monkeypatch):
    monkeypatch.delenv("FIVETRAN_SCOPE", raising=False)
    monkeypatch.delenv("FIVETRAN_ALLOW_WRITES", raising=False)
    monkeypatch.delenv("DISALLOWED_ACTIONS", raising=False)

    scope_actions, pair_denies, endpoint_denies = server._parse_scope_and_denies_from_env()

    assert scope_actions == ("read",)
    assert pair_denies == set()
    assert endpoint_denies == set()


def test_parse_scope_and_denies_from_env_disallowed_cascades(monkeypatch):
    monkeypatch.setenv("FIVETRAN_SCOPE", "read/write/delete")
    monkeypatch.setenv("DISALLOWED_ACTIONS", "connections:write")
    monkeypatch.delenv("FIVETRAN_ALLOW_WRITES", raising=False)

    scope_actions, pair_denies, endpoint_denies = server._parse_scope_and_denies_from_env()

    assert scope_actions == ("read", "write", "delete")
    # write cascades to delete on the same resource.
    assert ("connections", "write") in pair_denies
    assert ("connections", "delete") in pair_denies
    assert ("connections", "read") not in pair_denies
    assert endpoint_denies == set()
