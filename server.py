#!/usr/bin/env python3
"""Fivetran MCP Server - Read-only access to Fivetran connections, destinations, and groups."""

import json
import os
import base64
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

load_dotenv()

FIVETRAN_API_KEY = os.getenv("FIVETRAN_APIKEY")
FIVETRAN_API_SECRET = os.getenv("FIVETRAN_APISECRET")
BASE_URL = "https://api.fivetran.com/v1"
SERVER_DIR = Path(__file__).parent


def get_auth_header() -> dict[str, str]:
    """Create Basic Auth header for Fivetran API."""
    if not FIVETRAN_API_KEY or not FIVETRAN_API_SECRET:
        raise ValueError("FIVETRAN_APIKEY and FIVETRAN_APISECRET must be set in environment")
    credentials = f"{FIVETRAN_API_KEY}:{FIVETRAN_API_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
    }


async def fivetran_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a request to the Fivetran API."""
    url = f"{BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=get_auth_header(),
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


def validate_and_read_schema(schema_file: str) -> dict[str, Any]:
    """Read and validate the schema file before allowing API call.

    This function MUST be called before any API request to ensure the caller
    has acknowledged the schema file path.

    Args:
        schema_file: Path to the schema file (e.g., 'open-api-definitions/connections/list_connections.json')

    Returns:
        The parsed schema content

    Raises:
        ValueError: If schema file is missing, invalid path, or invalid JSON
    """
    if not schema_file:
        raise ValueError(
            "schema_file is required. You must first read the schema file, "
            "then provide its path to confirm you understand the response structure."
        )

    # Validate path format
    if not schema_file.startswith("open-api-definitions/"):
        raise ValueError(
            f"Invalid schema_file path: '{schema_file}'. "
            "Path must start with 'open-api-definitions/'"
        )

    # Resolve and validate the file exists
    schema_path = SERVER_DIR / schema_file

    if not schema_path.exists():
        raise ValueError(
            f"Schema file not found: '{schema_file}'. "
            "Please check the path and ensure you've run the OpenAPI split script."
        )

    # Read and parse the schema
    try:
        with open(schema_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in schema file '{schema_file}': {e}")


# Tool definitions with their expected schema files
TOOLS = {
    "list_connections": {
        "description": "List Fivetran connections in your account. PAGINATION: Results paginated (default 100, max 1000). Follow next_cursor until null for complete results.",
        "schema_file": "open-api-definitions/connections/list_connections.json",
        "params": ["limit", "cursor"],
    },
    "get_connection_details": {
        "description": "Get detailed information about a specific connection.",
        "schema_file": "open-api-definitions/connections/connection_details.json",
        "required": ["connection_id"],
    },
    "get_connection_state": {
        "description": "Get the current sync state of a connection.",
        "schema_file": "open-api-definitions/connections/connection_state.json",
        "required": ["connection_id"],
    },
    "get_connection_schema_config": {
        "description": "Get the schema configuration for a connection, showing which schemas and tables are enabled for sync.",
        "schema_file": "open-api-definitions/connections/connection_schema_config.json",
        "required": ["connection_id"],
    },
    "list_destinations": {
        "description": "List data warehouse destinations in your Fivetran account. PAGINATION: Results paginated (default 100, max 1000). Follow next_cursor until null for complete results.",
        "schema_file": "open-api-definitions/destinations/list_destinations.json",
        "params": ["limit", "cursor"],
    },
    "get_destination_details": {
        "description": "Get detailed information about a specific destination.",
        "schema_file": "open-api-definitions/destinations/destination_details.json",
        "required": ["destination_id"],
    },
    "list_groups": {
        "description": "List groups in your Fivetran account. Groups organize connections and destinations together. PAGINATION: Results paginated (default 100, max 1000). Follow next_cursor until null for complete results.",
        "schema_file": "open-api-definitions/groups/list_all_groups.json",
        "params": ["limit", "cursor"],
    },
    "get_group_details": {
        "description": "Get detailed information about a specific group.",
        "schema_file": "open-api-definitions/groups/group_details.json",
        "required": ["group_id"],
    },
    "list_connections_in_group": {
        "description": "List connections within a specific group. PAGINATION: Results paginated (default 100, max 1000). Follow next_cursor until null for complete results.",
        "schema_file": "open-api-definitions/groups/list_all_connections_in_group.json",
        "required": ["group_id"],
        "params": ["limit", "cursor"],
    },
}


def build_tool_schema(tool_name: str, tool_config: dict) -> Tool:
    """Build a Tool object with schema_file as a required parameter."""
    properties = {
        "schema_file": {
            "type": "string",
            "description": f"REQUIRED: You must first read the schema file at '{tool_config['schema_file']}', then provide this exact path here to confirm.",
        },
    }

    required = ["schema_file"]

    # Add tool-specific required parameters
    for param in tool_config.get("required", []):
        if param == "connection_id":
            properties["connection_id"] = {
                "type": "string",
                "description": "The unique identifier for the connection",
            }
        elif param == "destination_id":
            properties["destination_id"] = {
                "type": "string",
                "description": "The unique identifier for the destination",
            }
        elif param == "group_id":
            properties["group_id"] = {
                "type": "string",
                "description": "The unique identifier for the group",
            }
        required.append(param)

    # Add optional pagination parameters
    if "limit" in tool_config.get("params", []):
        properties["limit"] = {
            "type": "integer",
            "description": "Number of records to fetch (1-1000, default 100)",
            "default": 100,
        }
    if "cursor" in tool_config.get("params", []):
        properties["cursor"] = {
            "type": "string",
            "description": "Pagination cursor for fetching next page",
        }

    return Tool(
        name=tool_name,
        description=tool_config["description"],
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


# Create the MCP server
server = Server("fivetran")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Fivetran tools."""
    return [build_tool_schema(name, config) for name, config in TOOLS.items()]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls with mandatory schema validation."""
    try:
        # Get tool config
        if name not in TOOLS:
            raise ValueError(f"Unknown tool: {name}")

        tool_config = TOOLS[name]
        expected_schema = tool_config["schema_file"]

        # MANDATORY: Validate schema file before proceeding
        provided_schema = arguments.get("schema_file", "")
        if provided_schema != expected_schema:
            raise ValueError(
                f"Invalid schema_file. Expected '{expected_schema}'. "
                f"You must read this file first, then provide the exact path."
            )

        # Validate the file exists and is readable
        validate_and_read_schema(provided_schema)

        # Execute the API call
        result = await execute_tool(name, arguments)
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


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute the actual API call after validation."""
    params = {}

    if arguments.get("limit"):
        params["limit"] = arguments["limit"]
    if arguments.get("cursor"):
        params["cursor"] = arguments["cursor"]

    if name == "list_connections":
        return await fivetran_request("GET", "/connections", params or None)

    elif name == "get_connection_details":
        connection_id = arguments["connection_id"]
        return await fivetran_request("GET", f"/connections/{connection_id}")

    elif name == "get_connection_state":
        connection_id = arguments["connection_id"]
        return await fivetran_request("GET", f"/connections/{connection_id}/state")

    elif name == "get_connection_schema_config":
        connection_id = arguments["connection_id"]
        return await fivetran_request("GET", f"/connections/{connection_id}/schemas")

    elif name == "list_destinations":
        return await fivetran_request("GET", "/destinations", params or None)

    elif name == "get_destination_details":
        destination_id = arguments["destination_id"]
        return await fivetran_request("GET", f"/destinations/{destination_id}")

    elif name == "list_groups":
        return await fivetran_request("GET", "/groups", params or None)

    elif name == "get_group_details":
        group_id = arguments["group_id"]
        return await fivetran_request("GET", f"/groups/{group_id}")

    elif name == "list_connections_in_group":
        group_id = arguments["group_id"]
        return await fivetran_request("GET", f"/groups/{group_id}/connections", params or None)

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
