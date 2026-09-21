"""Credential endpoints: reachability warning and the configs that deny them."""
import json
import re
from pathlib import Path

import pytest

import server
from server import CREDENTIAL_ENDPOINTS, _parse_disallowed_actions


REPO_ROOT = Path(__file__).resolve().parent.parent
ALL_RESOURCES = set(server.ENDPOINTS_BY_RESOURCE)


def _example_config_tokens() -> dict[str, str]:
    """DISALLOWED_ACTIONS from each shipped example config, keyed by filename."""
    mcp_json = json.loads((REPO_ROOT / ".mcp.example.json").read_text())
    codex_toml = (REPO_ROOT / ".codex" / "config.example.toml").read_text()
    match = re.search(r'^DISALLOWED_ACTIONS\s*=\s*"([^"]+)"', codex_toml, re.M)
    assert match, "no DISALLOWED_ACTIONS in .codex/config.example.toml"
    return {
        ".mcp.example.json": mcp_json["mcpServers"]["fivetran"]["env"]["DISALLOWED_ACTIONS"],
        ".codex/config.example.toml": match.group(1),
    }


def test_every_credential_endpoint_is_in_the_manifest():
    # The warning is silent on names it can't resolve, so a typo or a renamed
    # endpoint would quietly stop being reported.
    missing = [n for n in CREDENTIAL_ENDPOINTS if n not in server.ENDPOINTS_BY_NAME]
    assert missing == []


@pytest.mark.parametrize("filename", sorted(_example_config_tokens()))
def test_example_config_disallowed_actions_parses(filename):
    # v0.3.1 left `system-keys:write` in both example configs after the token
    # stopped naming a live tool pair, so copying either one crashed at startup.
    _parse_disallowed_actions(_example_config_tokens()[filename], ALL_RESOURCES)


@pytest.mark.parametrize("filename", sorted(_example_config_tokens()))
def test_example_config_denies_every_credential_endpoint(filename):
    tokens = _example_config_tokens()[filename]
    pair_denies, endpoint_denies = _parse_disallowed_actions(tokens, ALL_RESOURCES)
    server.configure(("read", "write", "delete"), pair_denies, endpoint_denies)

    reachable = [n for n in CREDENTIAL_ENDPOINTS
                 if server._is_callable(server.ENDPOINTS_BY_NAME[n])]
    assert reachable == []


def test_example_configs_agree():
    tokens = set(_example_config_tokens().values())
    assert len(tokens) == 1, f"example configs disagree: {tokens}"


def test_warning_names_reachable_endpoints_at_read_scope(capsys):
    server.configure(("read",), set(), set())
    server._warn_reachable_credential_endpoints()

    warning = capsys.readouterr().err
    # Only the two GETs are in scope; the mutations need write/delete.
    assert "get_user_api_key" in warning
    assert "list_api_keys" in warning
    assert "create_system_key" not in warning


def test_warning_covers_every_credential_endpoint_at_full_scope(capsys):
    server.configure(("read", "write", "delete"), set(), set())
    server._warn_reachable_credential_endpoints()

    warning = capsys.readouterr().err
    for name in CREDENTIAL_ENDPOINTS:
        assert name in warning


def test_warning_is_silent_once_denied(capsys):
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        _example_config_tokens()[".mcp.example.json"], ALL_RESOURCES
    )
    server.configure(("read", "write", "delete"), pair_denies, endpoint_denies)
    server._warn_reachable_credential_endpoints()

    assert capsys.readouterr().err == ""


def test_warning_goes_to_stderr_not_stdout(capsys):
    # stdout is the MCP JSON-RPC channel under stdio; a line there corrupts it.
    server.configure(("read",), set(), set(), mode="stdio")
    server._warn_reachable_credential_endpoints()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "get_user_api_key" in captured.err
