"""Test scaffolding for server.py.

Ensures the repo root is on sys.path (so `import server` works from tests/) and
resets `server`'s module-level state between tests so `configure()` calls from
one test don't bleed into the next.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

import server


_DISCOVERY_NAMES = {"list_endpoints", "get_schema"}


@pytest.fixture(autouse=True)
def reset_server_state():
    """Drop grants and generated tools between tests. Discovery tools stay put."""
    yield
    server.ALLOWED_GRANTS = set()
    server.SCOPE_ACTIONS = ()
    server.DISALLOWED = set()
    server.ENDPOINT_DENIES = set()
    server.MODE = "stdio"
    server.GENERATED_TOOLS.clear()
    server.TOOLS_BY_NAME.clear()
    server._TOOLS[:] = [t for t in server._TOOLS if t.name in _DISCOVERY_NAMES]
    server._http_client = None
