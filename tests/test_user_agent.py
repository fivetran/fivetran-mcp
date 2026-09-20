"""Coverage for the outbound User-Agent format:
fivetran-official-mcp-{client}/{version}
"""
import server
from server import _resolve_ua_client_slug, _sanitize_client_slug


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
    def __init__(self, session=None):
        self.session = session


class _FakeServer:
    def __init__(self, request_context):
        self.request_context = request_context


class _RaisingServer:
    @property
    def request_context(self):
        raise LookupError("no request context")


# --- _sanitize_client_slug -----------------------------------------------


def test_sanitize_client_slug_basic():
    assert _sanitize_client_slug("Claude Code") == "claude-code"


def test_sanitize_client_slug_mixed_punctuation():
    assert _sanitize_client_slug("  Weird!!Name__123 ") == "weird-name-123"


def test_sanitize_client_slug_empty_string():
    assert _sanitize_client_slug("") == "unknown"


def test_sanitize_client_slug_all_punctuation():
    assert _sanitize_client_slug("----") == "unknown"


# --- _resolve_ua_client_slug -----------------------------------------------


def test_resolve_ua_client_slug_sanitizes_client_info(monkeypatch):
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(session=_Session(_ClientParams("Claude Code"))))
    )
    assert _resolve_ua_client_slug() == "claude-code"


def test_resolve_ua_client_slug_unrecognized_client_still_sanitized(monkeypatch):
    monkeypatch.setattr(
        server, "mcp_server", _FakeServer(_RequestContext(session=_Session(_ClientParams("Some Odd Tool/2.0"))))
    )
    assert _resolve_ua_client_slug() == "some-odd-tool-2-0"


def test_resolve_ua_client_slug_no_client_params(monkeypatch):
    monkeypatch.setattr(server, "mcp_server", _FakeServer(_RequestContext(session=_Session(None))))
    assert _resolve_ua_client_slug() == "unknown"


def test_resolve_ua_client_slug_no_request_context(monkeypatch):
    monkeypatch.setattr(server, "mcp_server", _RaisingServer())
    assert _resolve_ua_client_slug() == "unknown"
