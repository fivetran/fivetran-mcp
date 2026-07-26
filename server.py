#!/usr/bin/env python3
"""Fivetran MCP server — 3-tool router over the endpoints manifest.

Exposes three tools:
  - list_endpoints(category?, search?, include_deprecated?) — tiered discovery
  - get_schema(name, service?) — full schema for a given endpoint
  - call(name, path_params?, query?, body?) — execute
"""
import base64
import json
import os
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

try:
    __version__ = version("fivetran-mcp")
except PackageNotFoundError:
    __version__ = "unknown"

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

load_dotenv()

FIVETRAN_API_KEY = os.getenv("FIVETRAN_API_KEY")
FIVETRAN_API_SECRET = os.getenv("FIVETRAN_API_SECRET")
BASE_URL = "https://api.fivetran.com"
SERVER_DIR = Path(__file__).parent
OPENAPI_DIR = SERVER_DIR / "open-api-definitions"


def _load_manifest() -> tuple[
    list[dict], dict[str, dict], dict[str, list[dict]], list[dict]
]:
    doc = json.loads((OPENAPI_DIR / "endpoints.json").read_text())
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


def _parse_scope() -> tuple[str, ...]:
    """FIVETRAN_SCOPE ∈ {read, read/write, read/write/delete}. Case-insensitive.
    Default: read."""
    raw = os.getenv("FIVETRAN_SCOPE", "").strip().lower()
    if not raw:
        return SCOPE_TIERS["read"]
    if raw not in SCOPE_TIERS:
        raise ValueError(
            f"Invalid FIVETRAN_SCOPE={raw!r}. Valid: {sorted(SCOPE_TIERS)}."
        )
    return SCOPE_TIERS[raw]


def _parse_disallowed_actions(all_resources: set[str]) -> set[tuple[str, str]]:
    """DISALLOWED_ACTIONS is a comma-separated list of `resource:action` tokens.
    Case-insensitive. Empty/unset => no denies. Validated against the manifest.

    Each token cascades via ACTION_CASCADE — denying read also denies write and
    delete on the same resource; denying write also denies delete. Matches how
    FIVETRAN_SCOPE cascades positively (read/write implies read).

    Some resources contain `-` (e.g. `system-keys`, `connector-sdk`) but never `:`,
    so a single `:` is an unambiguous separator between resource and action.
    """
    raw = os.getenv("DISALLOWED_ACTIONS", "").strip()
    if not raw:
        return set()
    denies: set[tuple[str, str]] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(
                f"Invalid DISALLOWED_ACTIONS token {token!r}. "
                f"Expected `resource:action`."
            )
        resource, action = token.split(":", 1)
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
        # Directly-written pairs must correspond to a real tool. A typo like
        # `hvr:delete` (HVR has no DELETE endpoints) fails loudly here rather
        # than silently disallowing something that never existed.
        if (resource, action) not in TOOL_PAIRS:
            raise ValueError(
                f"DISALLOWED_ACTIONS token {token!r} does not correspond to any tool. "
                f"Available pairs: {sorted(f'{r}:{a}' for r, a in TOOL_PAIRS)}."
            )
        for cascaded in ACTION_CASCADE[action]:
            denies.add((resource, cascaded))
    return denies


def _build_allowed_grants(
    all_resources: set[str],
) -> tuple[set[tuple[str, str]], tuple[str, ...], set[tuple[str, str]]]:
    """Product of scope × resources, minus DISALLOWED_ACTIONS."""
    scope_actions = _parse_scope()
    denies = _parse_disallowed_actions(all_resources)
    allowed = {(r, a) for r in all_resources for a in scope_actions} - denies
    return allowed, scope_actions, denies


ALLOWED_GRANTS, SCOPE_ACTIONS, DISALLOWED = _build_allowed_grants(
    set(ENDPOINTS_BY_RESOURCE)
)


def _get_auth_header() -> dict[str, str]:
    if not FIVETRAN_API_KEY or not FIVETRAN_API_SECRET:
        raise ValueError("FIVETRAN_API_KEY and FIVETRAN_API_SECRET must be set in environment")
    credentials = f"{FIVETRAN_API_KEY}:{FIVETRAN_API_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "User-Agent": f"fivetran-official-mcp/{__version__}",
    }


async def _fivetran_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=_get_auth_header(),
            params=params,
            json=json_body,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


def load_endpoint_schema(schema_file: str) -> dict[str, Any]:
    """Read one per-endpoint schema file. Kept as a discrete helper so a
    'require prior get_schema before call' enforcement layer could be re-added
    on top of it later without a rewrite."""
    path = OPENAPI_DIR / schema_file
    if not path.exists():
        raise ValueError(f"Schema file not found: '{schema_file}'")
    return json.loads(path.read_text())


def _load_service_config(kind: str, service: str) -> dict[str, Any]:
    path = OPENAPI_DIR / "_service-configs" / kind / f"{service}.json"
    if not path.exists():
        raise ValueError(f"Unknown {kind[:-1]} service: {service!r}")
    return json.loads(path.read_text())


def _splice_service_config(schema: dict, cfg: dict) -> None:
    body = schema.get("request_body", {}).get("content", {}).get("application/json")
    if not isinstance(body, dict):
        return
    body_props = body.setdefault("properties", {})
    for k, v in cfg.get("properties", {}).items():
        body_props[k] = v


def do_list_endpoints(
    category: str | None = None,
    search: str | None = None,
    include_deprecated: bool = False,
) -> dict[str, Any]:
    if not category and not search:
        counts: dict[str, int] = {}
        for resource, eps in ENDPOINTS_BY_RESOURCE.items():
            n = sum(1 for e in eps if include_deprecated or not e.get("deprecated"))
            if n:
                counts[resource] = n
        return {"categories": counts, "total": sum(counts.values())}

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
            }
            for e in pool
        ]
    }


def do_get_schema(name: str, service: str | None = None) -> dict[str, Any]:
    ep = ENDPOINTS_BY_NAME.get(name)
    if not ep:
        raise ValueError(f"Unknown endpoint: {name!r}")
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


async def do_call(
    name: str,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
    expected_pair: tuple[str, str] | None = None,
) -> dict[str, Any]:
    ep = ENDPOINTS_BY_NAME.get(name)
    if not ep:
        raise ValueError(f"Unknown endpoint: {name!r}")

    # The tool name is the boundary — reject if the caller invoked
    # `metadata_read(name="delete_connection")`. Without this check the tool
    # namespace is decorative.
    if expected_pair is not None and (ep["resource"], ep["scope"]) != expected_pair:
        expected_tool = f"{expected_pair[0].replace('-', '_')}_{expected_pair[1]}"
        actual_tool = f"{ep['resource'].replace('-', '_')}_{ep['scope']}"
        raise ValueError(
            f"{name!r} is {ep['resource']}:{ep['scope']}, "
            f"not {expected_pair[0]}:{expected_pair[1]}. "
            f"Call this endpoint via {actual_tool!r}, not {expected_tool!r}."
        )

    required = (ep["resource"], ep["scope"])
    if required not in ALLOWED_GRANTS:
        required_grant = f"{ep['resource']}:{ep['scope']}"
        if ep["scope"] not in SCOPE_ACTIONS:
            cause = "SCOPE_TOO_LOW"
            message = (
                f"Endpoint {name!r} requires action {ep['scope']!r}, which is not "
                f"in FIVETRAN_SCOPE={'/'.join(SCOPE_ACTIONS)!r}. "
                f"Raise FIVETRAN_SCOPE to include it."
            )
        else:
            cause = "EXPLICITLY_DISALLOWED"
            message = (
                f"Endpoint {name!r} is denied by DISALLOWED_ACTIONS "
                f"(matched {required_grant!r}). "
                f"Remove that token from DISALLOWED_ACTIONS to allow."
            )
        return {
            "error": "GRANT_NOT_ALLOWED",
            "cause": cause,
            "endpoint": name,
            "required_grant": required_grant,
            "scope": "/".join(SCOPE_ACTIONS),
            "disallowed": sorted(f"{r}:{a}" for r, a in DISALLOWED),
            "message": message,
        }

    endpoint = ep["path"]
    if path_params:
        for k, v in path_params.items():
            endpoint = endpoint.replace("{" + k + "}", str(v))

    json_body = body
    if isinstance(json_body, str):
        try:
            json_body = json.loads(json_body)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in body: {e}")

    return await _fivetran_request(
        ep["method"],
        endpoint,
        params=query or None,
        json_body=json_body,
    )


_TOOLS = [
    Tool(
        name="list_endpoints",
        description=(
            "Discover Fivetran API endpoints. With no arguments, returns "
            "{categories: {category: count}, total: N}. Provide `category` "
            "(e.g. 'connections') for endpoints in that category, or `search` "
            "to substring-match across name, summary, and path. Deprecated "
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
    ),
]


_ACTION_WARNING = {
    "read": "",
    "write": "WRITE OPERATIONS - confirm with user before calling. ",
    "delete": "DESTRUCTIVE - confirm with user before calling. ",
}


def _tool_description(tool: dict) -> str:
    """Generate a tool description at import time from the manifest."""
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


# Generate one Tool per surviving (resource, action) pair. Grant filter applied here
# so the exposed tool list is exactly the reachable surface — the host can then
# narrow further within the ceiling by hiding individual tools client-side.
GENERATED_TOOLS = [
    t for t in TOOLS_INDEX
    if (t["resource"], t["action"]) in ALLOWED_GRANTS
]

for _t in GENERATED_TOOLS:
    _TOOLS.append(
        Tool(
            name=_t["name"],
            description=_tool_description(_t),
            inputSchema=_RESOURCE_ACTION_INPUT_SCHEMA,
        )
    )

# tool_name -> (resource, action) for dispatch.
TOOLS_BY_NAME: dict[str, tuple[str, str]] = {
    t["name"]: (t["resource"], t["action"]) for t in GENERATED_TOOLS
}


mcp_server = Server("fivetran")


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
            result = await do_call(
                name=arguments["name"],
                path_params=arguments.get("path_params"),
                query=arguments.get("query"),
                body=arguments.get("body"),
                expected_pair=TOOLS_BY_NAME[name],
            )
        else:
            raise ValueError(f"Unknown tool: {name}")
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except httpx.HTTPStatusError as e:
        error_msg = f"Fivetran API error: {e.response.status_code}"
        try:
            error_detail = e.response.json()
            error_msg += f" - {error_detail.get('message', str(error_detail))}"
        except Exception:
            error_msg += f" - {e.response.text}"
        return [TextContent(type="text", text=error_msg)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def async_main():
    if not FIVETRAN_API_KEY or not FIVETRAN_API_SECRET:
        raise ValueError(
            "FIVETRAN_API_KEY and FIVETRAN_API_SECRET environment variables must be set. "
            "Configure them in your .mcp.json or .env file."
        )
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream, write_stream, mcp_server.create_initialization_options()
        )


def main():
    import asyncio
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
