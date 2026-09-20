"""Coverage for P11's outbound User-Agent format:
fivetran-official-mcp-{mode}-{client}/{version}

Reuses the lightweight fake mcp_server/request_context/session objects
established in tests/test_call_logging.py, duplicated locally (this repo's
existing per-file-independence style).
"""
import pytest

import server
from server import Credentials, _resolve_ua_client_slug, _sanitize_client_slug


CREDS = Credentials(authorization="Basic test")


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


def _fake_request(user_agent):
    return type("Req", (), {"headers": {"User-Agent": user_agent}})()


# --- _sanitize_client_slug -----------------------------------------------


def test_sanitize_client_slug_basic():
    assert _sanitize_client_slug("Claude Code") == "claude-code"


def test_sanitize_client_slug_mixed_punctuation():
    assert _sanitize_client_slug("  Weird!!Name__123 ") == "weird-name-123"


def test_sanitize_client_slug_empty_string():
    assert _sanitize_client_slug("") == "unknown"


def test_sanitize_client_slug_all_punctuation():
    assert _sanitize_client_slug("----") == "unknown"


# --- _resolve_ua_client_slug: stdio ---------------------------------------


def test_resolve_ua_client_slug_stdio_sanitizes_client_info(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(session=_Session(_ClientParams("Claude Code"))))
    )
    assert _resolve_ua_client_slug() == "claude-code"


def test_resolve_ua_client_slug_stdio_no_client_params(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(session=_Session(None))))
    assert _resolve_ua_client_slug() == "unknown"


def test_resolve_ua_client_slug_stdio_no_request_context(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(server, "mcp_server", _RaisingServer())
    assert _resolve_ua_client_slug() == "unknown"


# --- _resolve_ua_client_slug: streamable-http -----------------------------


@pytest.mark.parametrize(
    "user_agent,expected",
    [
        ("Claude-User/1.0", "claude"),
        ("ChatGPT-User/2.0", "chatgpt"),
        ("OpenAI/1.0", "chatgpt"),
        ("Cursor/1.2.3", "cursor"),
        ("codex-cli/0.9", "codex"),
        ("Gemini-CLI/1.0", "gemini"),
    ],
)
def test_resolve_ua_client_slug_http_known_clients(monkeypatch, user_agent, expected):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(request=_fake_request(user_agent))))
    assert _resolve_ua_client_slug() == expected


def test_resolve_ua_client_slug_http_unrecognized_client_returns_raw(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(request=_fake_request("SomeTool/2.1")))
    )
    assert _resolve_ua_client_slug() == "SomeTool/2.1"


def test_resolve_ua_client_slug_http_no_request(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(request=None)))
    assert _resolve_ua_client_slug() == "unknown"


def test_resolve_ua_client_slug_http_no_request_context(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _RaisingServer())
    assert _resolve_ua_client_slug() == "unknown"


# --- _get_auth_header end-to-end ------------------------------------------


def test_get_auth_header_stdio_format(monkeypatch):
    monkeypatch.setattr(server, "MODE", "stdio")
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(session=_Session(_ClientParams("Claude Code"))))
    )
    header = server._get_auth_header(CREDS)
    assert header["Authorization"] == "Basic test"
    assert header["Accept"] == "application/json"
    assert header["User-Agent"] == f"fivetran-official-mcp-stdio-claude-code/{server.__version__}"


def test_get_auth_header_http_format(monkeypatch):
    monkeypatch.setattr(server, "MODE", "streamable-http")
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(request=_fake_request("Cursor/1.2"))))
    header = server._get_auth_header(CREDS)
    assert header["User-Agent"] == f"fivetran-official-mcp-http-cursor/{server.__version__}"
