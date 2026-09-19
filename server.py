#!/usr/bin/env python3
"""Fivetran MCP server — scope-filtered router over the endpoints manifest.

Exposes two discovery tools plus generated resource/action tools:
  - list_endpoints(category?, search?, include_deprecated?) — tiered discovery
  - get_schema(name, service?) — full schema for a given endpoint
  - one tool per allowed (resource, action) pair — execute an endpoint in that group

Generated tools are filtered by FIVETRAN_SCOPE and DISALLOWED_ACTIONS.
"""
import argparse
import base64
import contextlib
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

try:
    __version__ = version("fivetran-mcp")
except PackageNotFoundError:
    __version__ = "unknown"

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool, TextContent, ToolAnnotations
from starlette.applications import Starlette
from starlette.routing import Mount

load_dotenv()

BASE_URL = "https://api.fivetran.com"
SERVER_DIR = Path(__file__).parent
OPENAPI_DIR = SERVER_DIR / "open-api-definitions"


# Opaque Authorization header value ("Basic ..." today, "Bearer ..." once
# Fivetran's OAuth lands). Kept opaque so switching auth schemes is a
# resolver-side change with no downstream refactor.
@dataclass(frozen=True, slots=True)
class Credentials:
    authorization: str


CredentialsResolver = Callable[[], Awaitable[Credentials]]


class CredentialsError(Exception):
    """Resolver could not produce Fivetran credentials for this request."""


_credentials_resolver: CredentialsResolver | None = None


def set_credentials_resolver(resolver: CredentialsResolver) -> None:
    """Register the resolver that supplies credentials for each API-hitting call.

    Called once at process start by the entrypoint for the active transport
    (stdio → env_resolver, HTTP → header_resolver, OAuth → oauth_resolver).
    """
    global _credentials_resolver
    _credentials_resolver = resolver


async def _resolve_credentials() -> Credentials:
    if _credentials_resolver is None:
        raise CredentialsError(
            "No credentials resolver registered. Call set_credentials_resolver() "
            "before serving requests."
        )
    return await _credentials_resolver()


def env_resolver() -> CredentialsResolver:
    async def _resolve() -> Credentials:
        key = os.getenv("FIVETRAN_API_KEY")
        secret = os.getenv("FIVETRAN_API_SECRET")
        if not key or not secret:
            raise CredentialsError(
                "FIVETRAN_API_KEY and FIVETRAN_API_SECRET environment variables must be set. "
                "Configure them in your .mcp.json or .env file."
            )
        encoded = base64.b64encode(f"{key}:{secret}".encode()).decode()
        return Credentials(authorization=f"Basic {encoded}")

    return _resolve


def _load_manifest() -> tuple[
    list[dict], dict[str, dict], dict[str, list[dict]], list[dict]
]:
    doc = json.loads((OPENAPI_DIR / "endpoints.json").read_text(encoding="utf-8"))
    entries = doc["endpoints"]
    tools = doc.get("tools", [])
    by_name = {e["name"]: e for e in entries}
    by_resource: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_resource[e["resource"]].append(e)
    return entries, by_name, dict(by_resource), tools


ENDPOINTS, ENDPOINTS_BY_NAME, ENDPOINTS_BY_RESOURCE, TOOLS_INDEX = _load_manifest()

# (resource, action) pairs that correspond to at least one live tool.
# Used to validate directly-written DISALLOWED_ACTIONS tokens (cascade-expanded
# pairs are inert if they don't exist, so we only strict-check what the user typed).
TOOL_PAIRS: set[tuple[str, str]] = {(t["resource"], t["action"]) for t in TOOLS_INDEX}


SCOPE_TIERS: dict[str, tuple[str, ...]] = {
    "read": ("read",),
    "read/write": ("read", "write"),
    "read/write/delete": ("read", "write", "delete"),
}

# Denying an action cascades to all "higher" actions on the same resource,
# mirroring FIVETRAN_SCOPE's positive cascade (read/write implies read).
# "You can't observe it, so you can't touch it either."
ACTION_CASCADE: dict[str, tuple[str, ...]] = {
    "read": ("read", "write", "delete"),
    "write": ("write", "delete"),
    "delete": ("delete",),
}

# Hosted-mode denies, as a code constant (not env) — see local/hosted-mcp-plan.md
# "Flag for future review": which credential-minting endpoints (get_user_api_key,
# connect_card, etc.) to exclude from hosted read/write before preview. Empty until
# that decision lands; populating it is a one-line change, tracked separately.
HOSTED_DISALLOWED_ACTIONS: str = ""


def _parse_scope() -> tuple[str, ...]:
    """Effective scope. Precedence:
      1. FIVETRAN_SCOPE ∈ {read, read/write, read/write/delete}
      2. FIVETRAN_ALLOW_WRITES=true  → read/write   (backwards compat)
      3. default: read

    If both FIVETRAN_SCOPE and FIVETRAN_ALLOW_WRITES are set, FIVETRAN_SCOPE
    wins and ALLOW_WRITES is ignored (with a stderr warning).
    """
    scope_raw = os.getenv("FIVETRAN_SCOPE", "").strip().lower()
    allow_writes = os.getenv("FIVETRAN_ALLOW_WRITES", "").strip().lower() == "true"

    if scope_raw:
        if scope_raw not in SCOPE_TIERS:
            raise ValueError(
                f"Invalid FIVETRAN_SCOPE={scope_raw!r}. Valid: {sorted(SCOPE_TIERS)}."
            )
        if allow_writes:
            print(
                "Warning: FIVETRAN_ALLOW_WRITES is ignored because FIVETRAN_SCOPE is set.",
                file=sys.stderr,
            )
        return SCOPE_TIERS[scope_raw]

    if allow_writes:
        return SCOPE_TIERS["read/write"]

    return SCOPE_TIERS["read"]


def _parse_disallowed_actions(
    raw: str,
    all_resources: set[str],
) -> tuple[set[tuple[str, str]], set[str]]:
    """`raw` is a comma-separated list of tokens (the DISALLOWED_ACTIONS env var
    for stdio, or HOSTED_DISALLOWED_ACTIONS for streamable-http). Two forms:

      resource:action                 → deny that (resource, action) pair
      resource:action:endpoint_name   → deny one specific endpoint in that pair

    Case-insensitive. Empty string => no denies. Validated against the manifest.
    Mode-neutral — reads no env vars itself, so one function serves both the
    env-parsed stdio path and the hosted code-constant path.

    Pair tokens cascade via ACTION_CASCADE — denying read also denies write and
    delete on the same resource; denying write also denies delete. Matches how
    FIVETRAN_SCOPE cascades positively (read/write implies read).

    The 3-part endpoint form does not cascade — it's a scalpel for silencing a
    single endpoint without touching the rest of the pair. `endpoint_name` must
    exist and belong to that exact (resource, action) pair, or the parse fails.

    Some resources contain `-` (e.g. `system-keys`, `connector-sdk`) but never `:`;
    endpoint names never contain `:`. So splitting on `:` up to twice is
    unambiguous across both forms.

    Returns (pair_denies, endpoint_denies). endpoint_denies is a set of
    endpoint names; the (resource, action) they belong to is recoverable via
    ENDPOINTS_BY_NAME when needed.
    """
    raw = raw.strip()
    if not raw:
        return set(), set()
    pair_denies: set[tuple[str, str]] = set()
    endpoint_denies: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        parts = token.split(":", 2)
        if len(parts) < 2:
            raise ValueError(
                f"Invalid DISALLOWED_ACTIONS token {token!r}. "
                f"Expected `resource:action` or `resource:action:endpoint_name`."
            )
        resource, action = parts[0], parts[1]
        endpoint_name = parts[2] if len(parts) == 3 else None
        if resource not in all_resources:
            raise ValueError(
                f"Unknown resource in DISALLOWED_ACTIONS token {token!r}: {resource!r}. "
                f"Known: {sorted(all_resources)}."
            )
        if action not in ("read", "write", "delete"):
            raise ValueError(
                f"Invalid action in DISALLOWED_ACTIONS token {token!r}: {action!r}. "
                f"Valid: ['read', 'write', 'delete']."
            )
        # Both forms: the (resource, action) pair must correspond to a real tool.
        # A typo like `hvr:delete` (HVR has no DELETE endpoints) fails loudly here
        # rather than silently disallowing something that never existed.
        if (resource, action) not in TOOL_PAIRS:
            raise ValueError(
                f"DISALLOWED_ACTIONS token {token!r} does not correspond to any tool. "
                f"Available pairs: {sorted(f'{r}:{a}' for r, a in TOOL_PAIRS)}."
            )
        if endpoint_name is None:
            for cascaded in ACTION_CASCADE[action]:
                pair_denies.add((resource, cascaded))
        else:
            ep = ENDPOINTS_BY_NAME.get(endpoint_name)
            if ep is None:
                raise ValueError(
                    f"Unknown endpoint in DISALLOWED_ACTIONS token {token!r}: "
                    f"{endpoint_name!r}. Discoverable via list_endpoints."
                )
            if (ep["resource"], ep["scope"]) != (resource, action):
                raise ValueError(
                    f"DISALLOWED_ACTIONS token {token!r} names an endpoint that "
                    f"belongs to {ep['resource']}:{ep['scope']}, not {resource}:{action}."
                )
            endpoint_denies.add(endpoint_name)
    return pair_denies, endpoint_denies


def _parse_scope_and_denies_from_env() -> tuple[
    tuple[str, ...], set[tuple[str, str]], set[str]
]:
    """Read FIVETRAN_SCOPE / FIVETRAN_ALLOW_WRITES / DISALLOWED_ACTIONS from env.

    Returns (scope_actions, pair_denies, endpoint_denies). Wraps _parse_scope
    and _parse_disallowed_actions so the mode-neutral core (configure) doesn't
    touch os.environ. The HTTP entrypoint (P1) will build its own args and
    pass them to configure() directly rather than calling this.
    """
    scope_actions = _parse_scope()
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        os.getenv("DISALLOWED_ACTIONS", ""), set(ENDPOINTS_BY_RESOURCE)
    )
    return scope_actions, pair_denies, endpoint_denies


# Grants and generated tools are populated by configure() at entrypoint time.
# They start empty so `import server` in tests doesn't quietly hit os.environ.
MODE: str = "stdio"
ALLOWED_GRANTS: set[tuple[str, str]] = set()
SCOPE_ACTIONS: tuple[str, ...] = ()
DISALLOWED: set[tuple[str, str]] = set()
ENDPOINT_DENIES: set[str] = set()


def _get_auth_header(creds: Credentials) -> dict[str, str]:
    return {
        "Authorization": creds.authorization,
        "Accept": "application/json",
        "User-Agent": f"fivetran-official-mcp/{__version__}",
    }


# Matches the timeout every call used to pass individually to
# httpx.AsyncClient(...).request(). Uniform across connect/read/write/pool.
_HTTP_TIMEOUT = httpx.Timeout(30.0)
_HTTP_LIMITS = httpx.Limits()

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return the shared outbound httpx.AsyncClient.

    Raises if called before http_client_lifespan() has been entered, so a
    leaked or missing client fails loudly rather than silently reopening a
    new connection pool per call.
    """
    if _http_client is None:
        raise RuntimeError(
            "HTTP client not started. Enter http_client_lifespan() before "
            "serving requests."
        )
    return _http_client


@contextlib.asynccontextmanager
async def http_client_lifespan():
    """Own the shared httpx.AsyncClient's lifecycle for one server run.

    Standalone and transport-agnostic: takes no arguments, references no
    Starlette app. stdio's async_main() enters it directly around
    mcp_server.run(). The HTTP transport (P1) will pass this (or a thin
    wrapper matching Starlette's lifespan signature) as the Starlette app's
    `lifespan=` — each uvicorn worker then owns its own client and closes it
    on shutdown via this same context manager.
    """
    global _http_client
    _http_client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, limits=_HTTP_LIMITS)
    try:
        yield
    finally:
        await _http_client.aclose()
        _http_client = None


async def _fivetran_request(
    creds: Credentials,
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{BASE_URL}{endpoint}"
    client = get_http_client()
    response = await client.request(
        method=method,
        url=url,
        headers=_get_auth_header(creds),
        params=params,
        json=json_body,
    )
    response.raise_for_status()
    # Empty body (204 or otherwise) would raise JSONDecodeError and
    # surface as an opaque "Expecting value" error.
    if response.status_code == 204 or not response.content:
        return {"status": "success", "code": response.status_code}
    return response.json()


def load_endpoint_schema(schema_file: str) -> dict[str, Any]:
    """Read one per-endpoint schema file. Kept as a discrete helper so a
    'require prior get_schema before call' enforcement layer could be re-added
    on top of it later without a rewrite."""
    path = OPENAPI_DIR / schema_file
    if not path.exists():
        raise ValueError(f"Schema file not found: '{schema_file}'")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_service_config(kind: str, service: str) -> dict[str, Any]:
    path = OPENAPI_DIR / "_service-configs" / kind / f"{service}.json"
    if not path.exists():
        raise ValueError(f"Unknown {kind[:-1]} service: {service!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def _splice_service_config(schema: dict, cfg: dict) -> None:
    body = schema.get("request_body", {}).get("content", {}).get("application/json")
    if not isinstance(body, dict):
        return
    body_props = body.setdefault("properties", {})
    for k, v in cfg.get("properties", {}).items():
        body_props[k] = v


def _is_callable(ep: dict) -> bool:
    return (
        (ep["resource"], ep["scope"]) in ALLOWED_GRANTS
        and ep["name"] not in ENDPOINT_DENIES
    )


def do_list_endpoints(
    category: str | None = None,
    search: str | None = None,
    include_deprecated: bool = False,
) -> dict[str, Any]:
    if not category and not search:
        counts: dict[str, int] = {}
        callable_counts: dict[str, int] = {}
        for resource, eps in ENDPOINTS_BY_RESOURCE.items():
            visible = [e for e in eps if include_deprecated or not e.get("deprecated")]
            if not visible:
                continue
            counts[resource] = len(visible)
            callable_counts[resource] = sum(1 for e in visible if _is_callable(e))
        return {
            "categories": counts,
            "callable_counts": callable_counts,
            "total": sum(counts.values()),
        }

    pool = ENDPOINTS
    if category:
        pool = ENDPOINTS_BY_RESOURCE.get(category, [])
    if search:
        s = search.lower()
        pool = [
            e for e in pool
            if s in e["name"].lower()
            or s in e.get("summary", "").lower()
            or s in e["path"].lower()
        ]
    if not include_deprecated:
        pool = [e for e in pool if not e.get("deprecated")]

    return {
        "endpoints": [
            {
                "name": e["name"],
                "method": e["method"],
                "path": e["path"],
                "summary": e.get("summary", ""),
                "scope": e["scope"],
                "callable": _is_callable(e),
            }
            for e in pool
        ]
    }


def do_get_schema(name: str, service: str | None = None) -> dict[str, Any]:
    ep = ENDPOINTS_BY_NAME.get(name)
    if not ep:
        return {
            "error": "UNKNOWN_ENDPOINT",
            "endpoint": name,
            "message": f"Unknown endpoint: {name!r}. Discoverable via list_endpoints.",
        }
    schema = load_endpoint_schema(ep["schema_file"])

    if service:
        if name in ("create_connection", "modify_connection"):
            cfg = _load_service_config("connectors", service)
        elif name in ("create_destination", "modify_destination"):
            cfg = _load_service_config("destinations", service)
        else:
            raise ValueError(
                f"service argument is only supported for "
                f"create_connection / modify_connection / "
                f"create_destination / modify_destination; got name={name!r}"
            )
        _splice_service_config(schema, cfg)

    return schema


def _shape_upstream_error(e: httpx.HTTPStatusError) -> dict[str, Any]:
    """Classify a confirmed-4xx Fivetran API error into a shaped, caller-correctable dict.

    5xx is handled by the caller (do_call) before this is reached — this
    function never sees a 5xx and never raises.
    """
    status = e.response.status_code
    try:
        detail = e.response.json()
    except Exception:
        detail = {}
    upstream_message = detail.get("message") if isinstance(detail, dict) else None
    upstream_code = detail.get("code") if isinstance(detail, dict) else None

    if status == 401:
        return {
            "error": "UPSTREAM_UNAUTHORIZED",
            "status": status,
            "message": upstream_message or "Fivetran rejected the request's credentials.",
        }
    if status == 403:
        return {
            "error": "UPSTREAM_FORBIDDEN",
            "status": status,
            "message": upstream_message or (
                "Fivetran rejected this operation — check the authenticated "
                "user's RBAC role in the Fivetran dashboard."
            ),
        }
    if status == 429:
        result: dict[str, Any] = {
            "error": "UPSTREAM_RATE_LIMITED",
            "status": status,
            "message": upstream_message or "Fivetran rate-limited this request.",
        }
        retry_after = e.response.headers.get("Retry-After")
        if retry_after is not None:
            result["retry_after"] = retry_after
        return result
    return {
        "error": "UPSTREAM_ERROR",
        "status": status,
        "code": upstream_code,
        "message": upstream_message or e.response.text,
    }


async def do_call(
    creds: Credentials,
    name: str,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
    expected_pair: tuple[str, str] | None = None,
) -> dict[str, Any]:
    ep = ENDPOINTS_BY_NAME.get(name)
    if not ep:
        return {
            "error": "UNKNOWN_ENDPOINT",
            "endpoint": name,
            "message": f"Unknown endpoint: {name!r}. Discoverable via list_endpoints.",
        }

    # The tool name is the boundary — reject if the caller invoked
    # `metadata_read(name="delete_connection")`. Without this check the tool
    # namespace is decorative. Returned as a shaped error rather than raised
    # because the JSON Schema for `name` can't constrain it per generated tool,
    # so agents will mis-route sometimes and need something to self-correct on.
    if expected_pair is not None and (ep["resource"], ep["scope"]) != expected_pair:
        expected_tool = f"{expected_pair[0].replace('-', '_')}_{expected_pair[1]}"
        actual_tool = f"{ep['resource'].replace('-', '_')}_{ep['scope']}"
        return {
            "error": "ENDPOINT_TOOL_MISMATCH",
            "endpoint": name,
            "expected_tool": expected_tool,
            "actual_tool": actual_tool,
            "message": (
                f"{name!r} belongs to {actual_tool!r}; "
                f"call it through that tool instead of {expected_tool!r}."
            ),
        }

    required = (ep["resource"], ep["scope"])
    required_grant = f"{ep['resource']}:{ep['scope']}"
    if name in ENDPOINT_DENIES or required not in ALLOWED_GRANTS:
        cause = (
            "EXPLICITLY_DISALLOWED"
            if name in ENDPOINT_DENIES or required in DISALLOWED
            else "SCOPE_TOO_LOW"
        )
        return {
            "error": "GRANT_NOT_ALLOWED",
            "cause": cause,
            "endpoint": name,
            "required_grant": required_grant,
            "scope": "/".join(SCOPE_ACTIONS),
            "disallowed": sorted(f"{r}:{a}" for r, a in DISALLOWED),
            "endpoint_disallowed": sorted(ENDPOINT_DENIES),
            "message": (
                f"Endpoint {name!r} is not callable in the current configuration. "
                f"See server instructions for how to direct the user."
            ),
        }

    # Validate + URL-encode path params. Raw substitution would let a value like
    # "../groups/gr_123" reach a different resource and bypass DISALLOWED_ACTIONS
    # (the grant check runs on the endpoint name, not the final URL).
    expected = re.findall(r"{([^{}]+)}", ep["path"])
    provided = set((path_params or {}).keys())
    missing = [p for p in expected if p not in provided]
    if missing:
        return {
            "error": "MISSING_PATH_PARAM",
            "endpoint": name,
            "missing": missing,
            "message": (
                f"Missing path_params {missing} for {ep['path']!r}; "
                f"provided keys: {sorted(provided)}"
            ),
        }

    endpoint = ep["path"]
    for k, v in (path_params or {}).items():
        endpoint = endpoint.replace("{" + k + "}", quote(str(v), safe=""))

    if "{" in endpoint:
        raise ValueError(f"Unexpected placeholder remaining in {endpoint!r}")

    json_body = body
    if isinstance(json_body, str):
        try:
            json_body = json.loads(json_body)
        except json.JSONDecodeError as e:
            return {"error": "INVALID_BODY", "endpoint": name, "message": f"Invalid JSON in body: {e}"}

    try:
        return await _fivetran_request(
            creds,
            ep["method"],
            endpoint,
            params=query or None,
            json_body=json_body,
        )
    except httpx.HTTPStatusError as e:
        # 5xx is not caller-correctable — let it raise and surface as a
        # genuine MCP tool error (isError=True) rather than a shaped result.
        if e.response.status_code >= 500:
            raise
        return _shape_upstream_error(e)


_TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "read":   ToolAnnotations(readOnlyHint=True,  openWorldHint=False),
    "write":  ToolAnnotations(readOnlyHint=False, openWorldHint=False),
    "delete": ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False),
}

_TOOLS = [
    Tool(
        name="list_endpoints",
        description=(
            "Discover Fivetran API endpoints. With no arguments, returns "
            "{categories: {category: count}, callable_counts: {category: n}, total: N}. "
            "Provide `category` (e.g. 'connections') for endpoints in that category, "
            "or `search` to substring-match across name, summary, and path. Each "
            "endpoint row carries `callable: bool` — false means the endpoint isn't "
            "reachable through this MCP; see server instructions. Deprecated "
            "endpoints are hidden by default; set include_deprecated=true to show them."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Resource category (e.g., 'connections', 'destinations', 'groups'). Omit for counts.",
                },
                "search": {
                    "type": "string",
                    "description": "Substring to match against endpoint name, summary, or path.",
                },
                "include_deprecated": {
                    "type": "boolean",
                    "description": "Include deprecated endpoints. Default: false.",
                },
            },
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    ),
    Tool(
        name="get_schema",
        description=(
            "Return the full schema for a Fivetran endpoint — description, parameters, "
            "request body schema, response schema. Provide `service` (e.g. 'postgres') "
            "on create_connection / modify_connection / create_destination / "
            "modify_destination to splice in the per-service config shape."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Endpoint name (e.g. 'create_connection'). Discover via list_endpoints.",
                },
                "service": {
                    "type": "string",
                    "description": "Service identifier (e.g., 'postgres', 'salesforce'). Only meaningful for create/modify connection or destination endpoints.",
                },
            },
            "required": ["name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    ),
]


_ACTION_WARNING = {
    "read": "",
    "write": "WARNING. WRITE OPERATION. CONFIRM WITH USER BEFORE INITIATING ACTION. ",
    "delete": "DESTRUCTIVE - confirm with user before calling. ",
}


def _tool_description(tool: dict) -> str:
    """Generate a tool description from the manifest. Called by configure().

    Lists every non-deprecated endpoint in the pair, including any denied by
    DISALLOWED_ACTIONS. Availability is marked per-row by list_endpoints and
    signaled at call time by GRANT_NOT_ALLOWED — hiding endpoints here would
    prevent the agent from telling the user what exists but isn't reachable.
    """
    live = [
        e for e in ENDPOINTS_BY_RESOURCE.get(tool["resource"], [])
        if e["scope"] == tool["action"] and not e.get("deprecated")
    ]
    names = ", ".join(e["name"] for e in live[:8])
    more = f", + {len(live) - 8} more" if len(live) > 8 else ""
    resource_human = tool["resource"].replace("-", " ")
    return (
        f"{_ACTION_WARNING[tool['action']]}"
        f"{tool['action'].capitalize()} operations on Fivetran {resource_human} "
        f"({len(live)} endpoints: {names}{more}). "
        f"Pass the endpoint name in `name`. "
        f"Call list_endpoints(category='{tool['resource']}') for the full list."
    )


_RESOURCE_ACTION_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Endpoint name within this resource:action group (from list_endpoints).",
        },
        "path_params": {
            "type": "object",
            "description": "Values for path placeholders like {connectionId}, {groupId}.",
        },
        "query": {
            "type": "object",
            "description": "Query-string parameters.",
        },
        "body": {
            "description": "Request body — dict or JSON string. Required for POST/PATCH endpoints.",
        },
    },
    "required": ["name"],
}


# _meta markers on specific tools. Kept separate from the resource:action loop
# because these are per-tool overrides driven by client conventions, not the
# manifest.
#
# openai/profile: ChatGPT calls this tool to identify the connected account
# when a user has multiple sessions of the same connector, so it can show
# "You're using Fivetran (Ryan)" vs "Fivetran (Acme)".
_TOOL_META: dict[str, dict[str, Any]] = {
    "account_read": {"openai/profile": True},
}

# Snapshot of the always-present discovery tools. configure() truncates _TOOLS
# back to this and appends generated tools; keeping the snapshot avoids
# re-instantiating the discovery Tool objects on reconfigure.
_DISCOVERY_TOOLS: tuple[Tool, ...] = tuple(_TOOLS)

# Populated by configure().
GENERATED_TOOLS: list[dict] = []
TOOLS_BY_NAME: dict[str, tuple[str, str]] = {}


def configure(
    scope_actions: tuple[str, ...],
    pair_denies: set[tuple[str, str]],
    endpoint_denies: set[str] | None = None,
    mode: str = "stdio",
) -> None:
    """Compute grants and (re)build the generated tool list.

    Sets ALLOWED_GRANTS, SCOPE_ACTIONS, DISALLOWED, ENDPOINT_DENIES, and MODE
    from the arguments, then rebuilds GENERATED_TOOLS, TOOLS_BY_NAME, and
    _TOOLS from scratch (truncating _TOOLS to the discovery tools before
    appending generated ones). Idempotent: repeated calls with the same
    arguments produce the same state.

    ENDPOINT_DENIES surfaces on list_endpoints as `callable: false` per row
    and is enforced at call time by do_call. MODE steers GRANT_NOT_ALLOWED
    messages between stdio config advice and hosted-user redirects.

    The stdio entrypoint calls this with env-parsed values and mode="stdio".
    The HTTP entrypoint (P1) will call it with mode="streamable-http" and
    SCOPE_TIERS["read/write"].
    """
    global ALLOWED_GRANTS, SCOPE_ACTIONS, DISALLOWED, ENDPOINT_DENIES, MODE
    all_resources = set(ENDPOINTS_BY_RESOURCE)
    ALLOWED_GRANTS = {(r, a) for r in all_resources for a in scope_actions} - pair_denies
    SCOPE_ACTIONS = scope_actions
    DISALLOWED = set(pair_denies)
    ENDPOINT_DENIES = set(endpoint_denies or ())
    MODE = mode

    GENERATED_TOOLS.clear()
    GENERATED_TOOLS.extend(
        t for t in TOOLS_INDEX if (t["resource"], t["action"]) in ALLOWED_GRANTS
    )

    TOOLS_BY_NAME.clear()
    TOOLS_BY_NAME.update((t["name"], (t["resource"], t["action"])) for t in GENERATED_TOOLS)

    _TOOLS[:] = list(_DISCOVERY_TOOLS)
    for _t in GENERATED_TOOLS:
        kwargs: dict[str, Any] = {
            "name": _t["name"],
            "description": _tool_description(_t),
            "inputSchema": _RESOURCE_ACTION_INPUT_SCHEMA,
            "annotations": _TOOL_ANNOTATIONS[_t["action"]],
        }
        # Tool.meta is aliased to `_meta` in the MCP schema; must pass by alias.
        if _t["name"] in _TOOL_META:
            kwargs["_meta"] = _TOOL_META[_t["name"]]
        _TOOLS.append(Tool(**kwargs))


_SERVER_INSTRUCTIONS = (
    "Router over the Fivetran REST API. Tools are grouped by resource "
    "(connections, destinations, groups, ...) and action (read/write/delete); "
    "each dispatches to a specific endpoint by `name`. "
    "Flow: call `list_endpoints` to browse or search, then `get_schema` for "
    "the full request/response shape, then invoke the matching "
    "resource:action tool. Confirm with the user before any write or delete. "
    "Every `list_endpoints` row carries `callable: bool`. When `callable: false`, "
    "the endpoint cannot be invoked here — tell the user to use the Fivetran "
    "dashboard or REST API directly for that operation. A call attempt on a "
    "non-callable endpoint returns `error: GRANT_NOT_ALLOWED`."
)

mcp_server = Server("fivetran", instructions=_SERVER_INSTRUCTIONS)


def header_resolver() -> CredentialsResolver:
    """Forward the incoming HTTP `Authorization` header as-is (Basic or Bearer).

    For self-hosted HTTP deployments where the caller already provides valid
    Fivetran credentials. Reads from `mcp_server.request_context.request.headers`
    (starlette Request when running under streamable_http transport).
    """
    async def _resolve() -> Credentials:
        try:
            request = mcp_server.request_context.request
        except LookupError as e:
            raise CredentialsError("No request context available (running under stdio?)") from e
        if request is None:
            raise CredentialsError("Transport did not attach an HTTP request to the context")
        auth = request.headers.get("Authorization", "")
        if not auth:
            raise CredentialsError("Missing Authorization header")
        return Credentials(authorization=auth)

    return _resolve


def oauth_resolver() -> CredentialsResolver:
    """Forward the incoming HTTP `Authorization` header (already verified).

    By the time this runs, the OAuth middleware wrapping the MCP app (see
    auth.py) has already validated the bearer token against Fivetran's OAuth
    issuer and rejected anything invalid with a 401 before dispatch ever
    reaches here. This function's only job is forwarding the still-attached
    header unchanged — same mechanics as header_resolver, since forwarding
    doesn't depend on token format. Verifying the token's signature/claims
    is auth.FivetranOAuthTokenVerifier's job, not this one.
    """
    async def _resolve() -> Credentials:
        try:
            request = mcp_server.request_context.request
        except LookupError as e:
            raise CredentialsError("No request context available (running under stdio?)") from e
        if request is None:
            raise CredentialsError("Transport did not attach an HTTP request to the context")
        auth = request.headers.get("Authorization", "")
        if not auth:
            raise CredentialsError("Missing Authorization header")
        return Credentials(authorization=auth)

    return _resolve


TRANSPORT_MODES: tuple[str, ...] = ("stdio", "streamable-http")


def select_credentials_resolver(mode: str) -> CredentialsResolver:
    """Choose the credentials resolver for the active transport.

    stdio reads FIVETRAN_API_KEY/SECRET directly — one operator, one
    process. streamable-http picks between two resolvers depending on
    whether FIVETRAN_AUTH_ISSUER is set: unset means the interim path
    (header_resolver forwards whatever Authorization header the caller
    already has); set means OAuth resource-server mode (oauth_resolver,
    paired with the bearer-auth middleware build_http_app wraps the MCP app
    in — see auth.py). Never more than one resolver registered at once.

    streamable-http fails here, at selection time, if FIVETRAN_API_KEY or
    FIVETRAN_API_SECRET is set: a shared key baked into a multi-tenant HTTP
    process would silently apply one operator's credentials to every
    caller. Checked eagerly (unlike env_resolver's own lazy, per-call
    check) because it's a startup misconfiguration, not a per-request one.
    """
    if mode not in TRANSPORT_MODES:
        raise ValueError(f"Unknown transport mode {mode!r}. Expected one of {TRANSPORT_MODES}.")
    if mode == "stdio":
        return env_resolver()
    if os.getenv("FIVETRAN_API_KEY") or os.getenv("FIVETRAN_API_SECRET"):
        raise ValueError(
            "FIVETRAN_API_KEY / FIVETRAN_API_SECRET must not be set in "
            "streamable-http mode: a shared key in a multi-tenant process "
            "is a misconfiguration. HTTP mode gets credentials from the "
            "incoming Authorization header, either forwarded as-is or via "
            "OAuth once FIVETRAN_AUTH_ISSUER is configured."
        )
    if os.getenv("FIVETRAN_AUTH_ISSUER"):
        return oauth_resolver()
    return header_resolver()


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "list_endpoints":
            result = do_list_endpoints(
                category=arguments.get("category"),
                search=arguments.get("search"),
                include_deprecated=bool(arguments.get("include_deprecated", False)),
            )
        elif name == "get_schema":
            result = do_get_schema(
                name=arguments["name"],
                service=arguments.get("service"),
            )
        elif name in TOOLS_BY_NAME:
            creds = await _resolve_credentials()
            result = await do_call(
                creds,
                name=arguments["name"],
                path_params=arguments.get("path_params"),
                query=arguments.get("query"),
                body=arguments.get("body"),
                expected_pair=TOOLS_BY_NAME[name],
            )
        else:
            result = {"error": "UNKNOWN_TOOL", "message": f"Unknown tool: {name!r}"}
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except CredentialsError as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": "CREDENTIALS_MISSING", "message": str(e)}, indent=2),
        )]


def _warn_ignored_env_vars(var_names: tuple[str, ...], reason: str) -> None:
    """Print a stderr warning listing whichever of `var_names` are set, or do
    nothing if none are. Shared by both directions of the stdio/streamable-http
    env-var split so the wording stays consistent."""
    set_vars = [v for v in var_names if os.getenv(v)]
    if set_vars:
        print(f"Warning: {', '.join(set_vars)} set but ignored: {reason}", file=sys.stderr)


async def async_main():
    _warn_ignored_env_vars(
        _HTTP_ONLY_ENV_VARS, "they only take effect with --transport streamable-http."
    )
    scope_actions, pair_denies, endpoint_denies = _parse_scope_and_denies_from_env()
    configure(scope_actions, pair_denies, endpoint_denies, mode="stdio")
    set_credentials_resolver(select_credentials_resolver("stdio"))
    async with http_client_lifespan():
        async with stdio_server() as (read_stream, write_stream):
            await mcp_server.run(
                read_stream, write_stream, mcp_server.create_initialization_options()
            )


def _build_security_settings(mode: str) -> TransportSecuritySettings | None:
    """Build DNS-rebinding protection from MCP_ALLOWED_ORIGINS/MCP_ALLOWED_HOSTS.

    Both env vars are comma-separated. If neither is set, protection stays
    off (the SDK's own backwards-compatible default) with a startup warning —
    an empty allowed_origins/allowed_hosts list would otherwise reject every
    request once protection is enabled.
    """
    origins = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not origins and not hosts:
        print(
            f"Warning: MCP_ALLOWED_ORIGINS/MCP_ALLOWED_HOSTS not set in {mode} mode; "
            "running without DNS-rebinding protection.",
            file=sys.stderr,
        )
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_origins=origins,
        allowed_hosts=hosts,
    )


_STDIO_ONLY_ENV_VARS: tuple[str, ...] = ("FIVETRAN_SCOPE", "DISALLOWED_ACTIONS", "FIVETRAN_ALLOW_WRITES")

# HTTP-only: configure OAuth resource-server mode. Unset FIVETRAN_AUTH_ISSUER
# means the interim path (header_resolver forwards the caller's own
# Authorization header as-is); set means OAuth (oauth_resolver plus the
# bearer-auth middleware wrapping the MCP app — see auth.py).
_HTTP_ONLY_ENV_VARS: tuple[str, ...] = ("FIVETRAN_AUTH_ISSUER", "MCP_RESOURCE_URL")

_DEFAULT_RESOURCE_URL = "https://mcp.fivetran.com/mcp"


def build_http_app(token_verifier: Any = None) -> Starlette:
    """Build the Starlette app for streamable-http mode: mounts /mcp, plus the
    OAuth well-known route when FIVETRAN_AUTH_ISSUER is set.

    `token_verifier` (an auth.TokenVerifier) lets tests substitute a fake
    verifier to exercise the bearer-auth gate without real crypto; production
    callers omit it and get auth.FivetranOAuthTokenVerifier(). /health is not
    added here.
    """
    _warn_ignored_env_vars(
        _STDIO_ONLY_ENV_VARS,
        "hosted grants come from HOSTED_DISALLOWED_ACTIONS, not env vars.",
    )

    all_resources = set(ENDPOINTS_BY_RESOURCE)
    pair_denies, endpoint_denies = _parse_disallowed_actions(
        HOSTED_DISALLOWED_ACTIONS, all_resources
    )
    configure(SCOPE_TIERS["read/write"], pair_denies, endpoint_denies, mode="streamable-http")
    # Raises eagerly if a shared API key is set in the environment — enough to
    # enforce the "no shared key in a multi-tenant HTTP process" startup guard.
    set_credentials_resolver(select_credentials_resolver("streamable-http"))

    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,
        security_settings=_build_security_settings("streamable-http"),
    )

    mcp_app = session_manager.handle_request
    routes = []
    middleware = []
    issuer = os.getenv("FIVETRAN_AUTH_ISSUER")
    if issuer:
        import auth

        resource_url = os.getenv("MCP_RESOURCE_URL", _DEFAULT_RESOURCE_URL)
        # oauth_authentication_middleware must run on every request, before
        # routing, so it's installed at the Starlette level, not wrapped
        # around mcp_app directly. require_oauth then gates just this app.
        mcp_app = auth.require_oauth(mcp_app, resource_url)
        middleware.append(auth.oauth_authentication_middleware(resource_url, token_verifier))
        routes.extend(auth.oauth_protected_resource_routes(issuer, resource_url))
    routes.append(Mount("/mcp", app=mcp_app))

    @contextlib.asynccontextmanager
    async def _lifespan(app: Starlette):
        async with http_client_lifespan():
            async with session_manager.run():
                yield

    return Starlette(routes=routes, middleware=middleware, lifespan=_lifespan)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fivetran-mcp")
    parser.add_argument(
        "--transport",
        choices=TRANSPORT_MODES,
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport to serve over. Default: stdio (or MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "127.0.0.1"),
        help="Bind host for streamable-http mode. Default: 127.0.0.1 (or MCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8000")),
        help="Bind port for streamable-http mode. Default: 8000 (or MCP_PORT).",
    )
    return parser


def main():
    import asyncio

    args = _build_arg_parser().parse_args()
    if args.transport == "stdio":
        asyncio.run(async_main())
        return

    import uvicorn

    uvicorn.run(build_http_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
