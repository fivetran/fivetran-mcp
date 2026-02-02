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

# Credentials are configured in .mcp.json
FIVETRAN_API_KEY = os.getenv("FIVETRAN_APIKEY")
FIVETRAN_API_SECRET = os.getenv("FIVETRAN_APISECRET")
FIVETRAN_ALLOW_WRITES = os.getenv("FIVETRAN_ALLOW_WRITES", "false").lower() == "true"
BASE_URL = "https://api.fivetran.com"
SERVER_DIR = Path(__file__).parent

def check_write_permission(method: str) -> None:
    """Raise error if writes not allowed for non-GET methods."""
    if method != "GET" and not FIVETRAN_ALLOW_WRITES:
        raise ValueError(
            f"Write operations ({method}) are disabled. "
            "Set FIVETRAN_ALLOW_WRITES=true to enable POST, PATCH, and DELETE requests."
        )


def get_auth_header() -> dict[str, str]:
    """Create Basic Auth header for Fivetran API."""
    if not FIVETRAN_API_KEY or not FIVETRAN_API_SECRET:
        raise ValueError("FIVETRAN_APIKEY and FIVETRAN_APISECRET must be set in environment")
    credentials = f"{FIVETRAN_API_KEY}:{FIVETRAN_API_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "User-Agent": "fivetran-official-mcp",
    }


async def fivetran_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a request to the Fivetran API."""
    check_write_permission(method)
    url = f"{BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=get_auth_header(),
            params=params,
            json=json_body,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def fivetran_request_all_pages(
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make paginated GET requests to the Fivetran API and return all results.

    Automatically follows next_cursor until all pages are fetched.
    Note: This is always a GET request, so no write permission check needed.
    """
    all_items = []
    params = params or {}
    params["limit"] = 1000  # Use max limit for efficiency

    async with httpx.AsyncClient() as client:
        while True:
            url = f"{BASE_URL}{endpoint}"
            response = await client.request(
                method="GET",
                url=url,
                headers=get_auth_header(),
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()

            # Extract items from response
            data = result.get("data", {})
            items = data.get("items", [])
            all_items.extend(items)

            # Check for next page
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                break

            params["cursor"] = next_cursor

    # Return in same format as single-page response
    return {
        "code": "Success",
        "data": {
            "items": all_items,
            "_auto_paginated": True,
            "_total_items": len(all_items),
        }
    }


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


# Tool definitions organized by resource
# Each tool has: description, schema_file, method, endpoint, params (optional), auto_paginate (optional)
TOOLS = {
    # ============================================================================
    # ACCOUNT
    # ============================================================================
    "get_account_info": {
        "description": "Get account information associated with the API key.",
        "schema_file": "open-api-definitions/account/get_account_info.json",
        "method": "GET",
        "endpoint": "/v1/account/info",
    },

    # ============================================================================
    # CERTIFICATES (Deprecated)
    # ============================================================================
    "approve_certificate": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. (Deprecated) Approve a certificate for the account.",
        "schema_file": "open-api-definitions/certificates/approve_certificate.json",
        "method": "POST",
        "endpoint": "/v1/certificates",
        "params": ["request_body"],
    },

    # ============================================================================
    # CONNECTIONS
    # ============================================================================
    "list_connections": {
        "description": "List ALL Fivetran connections in your account. Automatically fetches all pages.",
        "schema_file": "open-api-definitions/connections/list_connections.json",
        "method": "GET",
        "endpoint": "/v1/connections",
        "auto_paginate": True,
    },
    "create_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new connection.",
        "schema_file": "open-api-definitions/connections/create_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections",
        "params": ["request_body"],
    },
    "get_connection_details": {
        "description": "Get detailed information about a specific connection.",
        "schema_file": "open-api-definitions/connections/connection_details.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id"],
    },
    "modify_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update an existing connection.",
        "schema_file": "open-api-definitions/connections/modify_connection.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id", "request_body"],
    },
    "delete_connection": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a connection permanently.",
        "schema_file": "open-api-definitions/connections/delete_connection.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id"],
    },
    "get_connection_state": {
        "description": "Get the current sync state of a connection.",
        "schema_file": "open-api-definitions/connections/connection_state.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/state",
        "params": ["connection_id"],
    },
    "modify_connection_state": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update the sync state of a connection.",
        "schema_file": "open-api-definitions/connections/modify_connection_state.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/state",
        "params": ["connection_id", "request_body"],
    },
    "sync_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Trigger a data sync for a connection.",
        "schema_file": "open-api-definitions/connections/sync_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/sync",
        "params": ["connection_id", "request_body"],
    },
    "resync_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Trigger a historical re-sync for a connection.",
        "schema_file": "open-api-definitions/connections/resync_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/resync",
        "params": ["connection_id", "request_body"],
    },
    "resync_tables": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Re-sync specific tables in a connection.",
        "schema_file": "open-api-definitions/connections/resync_tables.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/tables/resync",
        "params": ["connection_id", "request_body"],
    },
    "run_connection_setup_tests": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Run setup tests for a connection.",
        "schema_file": "open-api-definitions/connections/run_setup_tests.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/test",
        "params": ["connection_id"],
    },
    "create_connect_card": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a connect card token for a connection.",
        "schema_file": "open-api-definitions/connections/connect_card.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/connect-card",
        "params": ["connection_id", "request_body"],
    },
    "get_connection_schema_config": {
        "description": "Get the schema configuration for a connection, showing which schemas and tables are enabled for sync.",
        "schema_file": "open-api-definitions/connections/connection_schema_config.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/schemas",
        "params": ["connection_id"],
    },
    "reload_connection_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Reload the schema configuration for a connection from the source.",
        "schema_file": "open-api-definitions/connections/reload_connection_schema_config.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/reload",
        "params": ["connection_id", "request_body"],
    },
    "modify_connection_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update the schema configuration for a connection.",
        "schema_file": "open-api-definitions/connections/modify_connection_schema_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas",
        "params": ["connection_id", "request_body"],
    },
    "modify_connection_database_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update configuration for a specific database schema in a connection.",
        "schema_file": "open-api-definitions/connections/modify_connection_database_schema_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}",
        "params": ["connection_id", "schema_name", "request_body"],
    },
    "get_connection_column_config": {
        "description": "Get column configuration for a specific table in a connection.",
        "schema_file": "open-api-definitions/connections/connection_column_config.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}/columns",
        "params": ["connection_id", "schema_name", "table_name"],
    },
    "modify_connection_table_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update configuration for a specific table in a connection.",
        "schema_file": "open-api-definitions/connections/modify_connection_table_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}",
        "params": ["connection_id", "schema_name", "table_name", "request_body"],
    },
    "modify_connection_column_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update configuration for a specific column in a connection.",
        "schema_file": "open-api-definitions/connections/modify_connection_column_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}/columns/{column_name}",
        "params": ["connection_id", "schema_name", "table_name", "column_name", "request_body"],
    },
    "delete_connection_column_config": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Drop a blocked column from the destination permanently.",
        "schema_file": "open-api-definitions/connections/delete_column_connection_config.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}/columns/{column_name}",
        "params": ["connection_id", "schema_name", "table_name", "column_name"],
    },
    "delete_multiple_columns_connection_config": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Drop multiple blocked columns from the destination permanently.",
        "schema_file": "open-api-definitions/connections/delete_multiple_columns_connection_config.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/drop-columns",
        "params": ["connection_id", "request_body"],
    },
    "list_connection_certificates": {
        "description": "List certificates approved for a connection.",
        "schema_file": "open-api-definitions/connections/get_connection_certificates_list.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/certificates",
        "params": ["connection_id"],
    },
    "approve_connection_certificate": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approve a certificate for a connection.",
        "schema_file": "open-api-definitions/connections/approve_connection_certificate.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/certificates",
        "params": ["connection_id", "request_body"],
    },
    "get_connection_certificate_details": {
        "description": "Get details of a specific certificate for a connection.",
        "schema_file": "open-api-definitions/connections/get_connection_certificate_details.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/certificates/{hash}",
        "params": ["connection_id", "hash"],
    },
    "revoke_connection_certificate": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revoke a certificate for a connection.",
        "schema_file": "open-api-definitions/connections/revoke_connection_certificate.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}/certificates/{hash}",
        "params": ["connection_id", "hash"],
    },
    "list_connection_fingerprints": {
        "description": "List fingerprints approved for a connection.",
        "schema_file": "open-api-definitions/connections/get_connection_fingerprints_list.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/fingerprints",
        "params": ["connection_id"],
    },
    "approve_connection_fingerprint": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approve a fingerprint for a connection.",
        "schema_file": "open-api-definitions/connections/approve_connection_fingerprint.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/fingerprints",
        "params": ["connection_id", "request_body"],
    },
    "get_connection_fingerprint_details": {
        "description": "Get details of a specific fingerprint for a connection.",
        "schema_file": "open-api-definitions/connections/get_connection_fingerprint_details.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/fingerprints/{hash}",
        "params": ["connection_id", "hash"],
    },
    "revoke_connection_fingerprint": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revoke a fingerprint for a connection.",
        "schema_file": "open-api-definitions/connections/revoke_connection_fingerprint.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}/fingerprints/{hash}",
        "params": ["connection_id", "hash"],
    },

    # ============================================================================
    # DESTINATIONS
    # ============================================================================
    "list_destinations": {
        "description": "List ALL data warehouse destinations in your Fivetran account. Automatically fetches all pages.",
        "schema_file": "open-api-definitions/destinations/list_destinations.json",
        "method": "GET",
        "endpoint": "/v1/destinations",
        "auto_paginate": True,
    },
    "create_destination": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new destination. IMPORTANT: A group_id is required. Groups are 1:1 with destinations, so you should typically create a new group first using create_group, then use that group's ID here. Only ask the user about existing groups if they specifically mention having one.",
        "schema_file": "open-api-definitions/destinations/create_destination.json",
        "method": "POST",
        "endpoint": "/v1/destinations",
        "params": ["request_body"],
    },
    "get_destination_details": {
        "description": "Get detailed information about a specific destination.",
        "schema_file": "open-api-definitions/destinations/destination_details.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id"],
    },
    "modify_destination": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update an existing destination.",
        "schema_file": "open-api-definitions/destinations/modify_destination.json",
        "method": "PATCH",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id", "request_body"],
    },
    "delete_destination": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a destination permanently.",
        "schema_file": "open-api-definitions/destinations/delete_destination.json",
        "method": "DELETE",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id"],
    },
    "run_destination_setup_tests": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Run setup tests for a destination.",
        "schema_file": "open-api-definitions/destinations/run_destination_setup_tests.json",
        "method": "POST",
        "endpoint": "/v1/destinations/{destination_id}/test",
        "params": ["destination_id"],
    },
    "list_destination_certificates": {
        "description": "List certificates approved for a destination.",
        "schema_file": "open-api-definitions/destinations/get_destination_certificates_list.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}/certificates",
        "params": ["destination_id"],
    },
    "approve_destination_certificate": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approve a certificate for a destination.",
        "schema_file": "open-api-definitions/destinations/approve_destination_certificate.json",
        "method": "POST",
        "endpoint": "/v1/destinations/{destination_id}/certificates",
        "params": ["destination_id", "request_body"],
    },
    "get_destination_certificate_details": {
        "description": "Get details of a specific certificate for a destination.",
        "schema_file": "open-api-definitions/destinations/get_destination_certificate_details.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}/certificates/{hash}",
        "params": ["destination_id", "hash"],
    },
    "revoke_destination_certificate": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revoke a certificate for a destination.",
        "schema_file": "open-api-definitions/destinations/revoke_destination_certificate.json",
        "method": "DELETE",
        "endpoint": "/v1/destinations/{destination_id}/certificates/{hash}",
        "params": ["destination_id", "hash"],
    },
    "list_destination_fingerprints": {
        "description": "List fingerprints approved for a destination.",
        "schema_file": "open-api-definitions/destinations/get_destination_fingerprints_list.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}/fingerprints",
        "params": ["destination_id"],
    },
    "approve_destination_fingerprint": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approve a fingerprint for a destination.",
        "schema_file": "open-api-definitions/destinations/approve_destination_fingerprint.json",
        "method": "POST",
        "endpoint": "/v1/destinations/{destination_id}/fingerprints",
        "params": ["destination_id", "request_body"],
    },
    "get_destination_fingerprint_details": {
        "description": "Get details of a specific fingerprint for a destination.",
        "schema_file": "open-api-definitions/destinations/get_destination_fingerprint_details.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}/fingerprints/{hash}",
        "params": ["destination_id", "hash"],
    },
    "revoke_destination_fingerprint": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revoke a fingerprint for a destination.",
        "schema_file": "open-api-definitions/destinations/revoke_destination_fingerprint.json",
        "method": "DELETE",
        "endpoint": "/v1/destinations/{destination_id}/fingerprints/{hash}",
        "params": ["destination_id", "hash"],
    },

    # ============================================================================
    # EXTERNAL LOGGING
    # ============================================================================
    "list_log_services": {
        "description": "List all log services in your account.",
        "schema_file": "open-api-definitions/external-logging/list_log_services.json",
        "method": "GET",
        "endpoint": "/v1/external-logging",
        "auto_paginate": True,
    },
    "create_log_service": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new log service.",
        "schema_file": "open-api-definitions/external-logging/add_log_service.json",
        "method": "POST",
        "endpoint": "/v1/external-logging",
        "params": ["request_body"],
    },
    "get_log_service_details": {
        "description": "Get details of a specific log service.",
        "schema_file": "open-api-definitions/external-logging/get_log_service_details.json",
        "method": "GET",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id"],
    },
    "update_log_service": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a log service.",
        "schema_file": "open-api-definitions/external-logging/update_log_service.json",
        "method": "PATCH",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id", "request_body"],
    },
    "delete_log_service": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a log service permanently.",
        "schema_file": "open-api-definitions/external-logging/delete_log_service.json",
        "method": "DELETE",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id"],
    },
    "run_log_service_setup_tests": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Run setup tests for a log service.",
        "schema_file": "open-api-definitions/external-logging/run_setup_tests_log_service.json",
        "method": "POST",
        "endpoint": "/v1/external-logging/{log_id}/test",
        "params": ["log_id"],
    },

    # ============================================================================
    # GROUPS
    # ============================================================================
    "list_groups": {
        "description": "List ALL groups in your Fivetran account. Groups organize connections and destinations together. Automatically fetches all pages.",
        "schema_file": "open-api-definitions/groups/list_all_groups.json",
        "method": "GET",
        "endpoint": "/v1/groups",
        "auto_paginate": True,
    },
    "create_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new group. Groups are containers that hold a destination and its connections. When creating a new destination, you should create a group first, then create the destination in that group.",
        "schema_file": "open-api-definitions/groups/create_group.json",
        "method": "POST",
        "endpoint": "/v1/groups",
        "params": ["request_body"],
    },
    "get_group_details": {
        "description": "Get detailed information about a specific group.",
        "schema_file": "open-api-definitions/groups/group_details.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id"],
    },
    "modify_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a group.",
        "schema_file": "open-api-definitions/groups/modify_group.json",
        "method": "PATCH",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id", "request_body"],
    },
    "delete_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a group permanently.",
        "schema_file": "open-api-definitions/groups/delete_group.json",
        "method": "DELETE",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id"],
    },
    "list_connections_in_group": {
        "description": "List ALL connections within a specific group. Automatically fetches all pages.",
        "schema_file": "open-api-definitions/groups/list_all_connections_in_group.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/connections",
        "params": ["group_id"],
        "auto_paginate": True,
    },
    "list_users_in_group": {
        "description": "List all users in a group.",
        "schema_file": "open-api-definitions/groups/list_all_users_in_group.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/users",
        "params": ["group_id"],
        "auto_paginate": True,
    },
    "add_user_to_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a user to a group.",
        "schema_file": "open-api-definitions/groups/add_user_to_group.json",
        "method": "POST",
        "endpoint": "/v1/groups/{group_id}/users",
        "params": ["group_id", "request_body"],
    },
    "delete_user_from_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a user from a group.",
        "schema_file": "open-api-definitions/groups/delete_user_from_group.json",
        "method": "DELETE",
        "endpoint": "/v1/groups/{group_id}/users/{user_id}",
        "params": ["group_id", "user_id"],
    },
    "get_group_ssh_public_key": {
        "description": "Get the SSH public key for a group.",
        "schema_file": "open-api-definitions/groups/group_ssh_public_key.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/public-key",
        "params": ["group_id"],
    },
    "get_group_service_account": {
        "description": "Get the service account for a group.",
        "schema_file": "open-api-definitions/groups/group_service_account.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/service-account",
        "params": ["group_id"],
    },

    # ============================================================================
    # HVR
    # ============================================================================
    "hvr_register_hub": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Register an HVR hub.",
        "schema_file": "open-api-definitions/hvr/hvr_register_hub.json",
        "method": "POST",
        "endpoint": "/v1/hvr/register-hub",
        "params": ["request_body"],
    },

    # ============================================================================
    # HYBRID DEPLOYMENT AGENTS
    # ============================================================================
    "list_hybrid_deployment_agents": {
        "description": "List all hybrid deployment agents.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/get_hybrid_deployment_agent_list.json",
        "method": "GET",
        "endpoint": "/v1/hybrid-deployment-agents",
        "auto_paginate": True,
    },
    "create_hybrid_deployment_agent": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new hybrid deployment agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/create_hybrid_deployment_agent.json",
        "method": "POST",
        "endpoint": "/v1/hybrid-deployment-agents",
        "params": ["request_body"],
    },
    "get_hybrid_deployment_agent": {
        "description": "Get details of a hybrid deployment agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/get_hybrid_deployment_agent.json",
        "method": "GET",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}",
        "params": ["agent_id"],
    },
    "re_auth_hybrid_deployment_agent": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Regenerate authentication keys for a hybrid deployment agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/re_auth_hybrid_deployment_agent.json",
        "method": "PATCH",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}/re-auth",
        "params": ["agent_id"],
    },
    "reset_hybrid_deployment_agent_credentials": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Reset credentials for a hybrid deployment agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/reset_hybrid_deployment_agent_credentials.json",
        "method": "POST",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}/reset-credentials",
        "params": ["agent_id"],
    },
    "delete_hybrid_deployment_agent": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a hybrid deployment agent permanently.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/delete_hybrid_deployment_agent.json",
        "method": "DELETE",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}",
        "params": ["agent_id"],
    },

    # ============================================================================
    # METADATA
    # ============================================================================
    "list_metadata_connectors": {
        "description": "List all available connector types and their metadata.",
        "schema_file": "open-api-definitions/metadata/metadata_connectors.json",
        "method": "GET",
        "endpoint": "/v1/metadata/connector-types",
    },
    "get_metadata_connector_config": {
        "description": "Get configuration metadata for a specific connector type.",
        "schema_file": "open-api-definitions/metadata/metadata_connector_config.json",
        "method": "GET",
        "endpoint": "/v1/metadata/connector-types/{service}",
        "params": ["service"],
    },

    # ============================================================================
    # PRIVATE LINKS
    # ============================================================================
    "list_private_links": {
        "description": "List all private links.",
        "schema_file": "open-api-definitions/private-links/get_private_links.json",
        "method": "GET",
        "endpoint": "/v1/private-links",
        "auto_paginate": True,
    },
    "create_private_link": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new private link.",
        "schema_file": "open-api-definitions/private-links/create_private_link.json",
        "method": "POST",
        "endpoint": "/v1/private-links",
        "params": ["request_body"],
    },
    "get_private_link_details": {
        "description": "Get details of a private link.",
        "schema_file": "open-api-definitions/private-links/get_private_link_details.json",
        "method": "GET",
        "endpoint": "/v1/private-links/{private_link_id}",
        "params": ["private_link_id"],
    },
    "modify_private_link": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a private link.",
        "schema_file": "open-api-definitions/private-links/modify_private_link.json",
        "method": "PATCH",
        "endpoint": "/v1/private-links/{private_link_id}",
        "params": ["private_link_id", "request_body"],
    },
    "delete_private_link": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a private link permanently.",
        "schema_file": "open-api-definitions/private-links/delete_private_link.json",
        "method": "DELETE",
        "endpoint": "/v1/private-links/{private_link_id}",
        "params": ["private_link_id"],
    },

    # ============================================================================
    # PROXY AGENTS
    # ============================================================================
    "list_proxy_agents": {
        "description": "List all proxy agents.",
        "schema_file": "open-api-definitions/proxy/get_proxy_agent.json",
        "method": "GET",
        "endpoint": "/v1/proxy",
        "auto_paginate": True,
    },
    "create_proxy_agent": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new proxy agent.",
        "schema_file": "open-api-definitions/proxy/create_proxy_agent.json",
        "method": "POST",
        "endpoint": "/v1/proxy",
        "params": ["request_body"],
    },
    "get_proxy_agent_details": {
        "description": "Get details of a proxy agent.",
        "schema_file": "open-api-definitions/proxy/get_proxy_agent_details.json",
        "method": "GET",
        "endpoint": "/v1/proxy/{agent_id}",
        "params": ["agent_id"],
    },
    "delete_proxy_agent": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a proxy agent permanently.",
        "schema_file": "open-api-definitions/proxy/delete_proxy_agent.json",
        "method": "DELETE",
        "endpoint": "/v1/proxy/{agent_id}",
        "params": ["agent_id"],
    },
    "list_proxy_agent_connections": {
        "description": "List all connections attached to a proxy agent.",
        "schema_file": "open-api-definitions/proxy/get_proxy_agent_connections.json",
        "method": "GET",
        "endpoint": "/v1/proxy/{agent_id}/connections",
        "params": ["agent_id"],
    },
    "regenerate_proxy_agent_secrets": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Regenerate secrets for a proxy agent.",
        "schema_file": "open-api-definitions/proxy/regenerate_secrets_proxy_agent.json",
        "method": "POST",
        "endpoint": "/v1/proxy/{agent_id}/regenerate-secrets",
        "params": ["agent_id"],
    },

    # ============================================================================
    # PUBLIC METADATA
    # ============================================================================
    "list_public_connectors": {
        "description": "List available connector types (public endpoint, no auth required).",
        "schema_file": "open-api-definitions/public/metadata_public_connectors.json",
        "method": "GET",
        "endpoint": "/public/connector-types",
    },

    # ============================================================================
    # ROLES
    # ============================================================================
    "list_roles": {
        "description": "List all available roles.",
        "schema_file": "open-api-definitions/roles/list_all_roles.json",
        "method": "GET",
        "endpoint": "/v1/roles",
    },

    # ============================================================================
    # SYSTEM KEYS
    # ============================================================================
    "list_system_keys": {
        "description": "List all system keys.",
        "schema_file": "open-api-definitions/system-keys/get_system_keys.json",
        "method": "GET",
        "endpoint": "/v1/system-keys",
        "auto_paginate": True,
    },
    "create_system_key": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new system key.",
        "schema_file": "open-api-definitions/system-keys/create_system_key.json",
        "method": "POST",
        "endpoint": "/v1/system-keys",
        "params": ["request_body"],
    },
    "get_system_key_details": {
        "description": "Get details of a system key.",
        "schema_file": "open-api-definitions/system-keys/get_system_key_details.json",
        "method": "GET",
        "endpoint": "/v1/system-keys/{key_id}",
        "params": ["key_id"],
    },
    "update_system_key": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a system key.",
        "schema_file": "open-api-definitions/system-keys/update_system_key.json",
        "method": "PATCH",
        "endpoint": "/v1/system-keys/{key_id}",
        "params": ["key_id", "request_body"],
    },
    "delete_system_key": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a system key permanently.",
        "schema_file": "open-api-definitions/system-keys/delete_system_key.json",
        "method": "DELETE",
        "endpoint": "/v1/system-keys/{key_id}",
        "params": ["key_id"],
    },
    "rotate_system_key": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Rotate a system key.",
        "schema_file": "open-api-definitions/system-keys/rotate_system_key.json",
        "method": "POST",
        "endpoint": "/v1/system-keys/{key_id}/rotate",
        "params": ["key_id"],
    },

    # ============================================================================
    # TEAMS
    # ============================================================================
    "list_teams": {
        "description": "List all teams.",
        "schema_file": "open-api-definitions/teams/list_all_teams.json",
        "method": "GET",
        "endpoint": "/v1/teams",
        "auto_paginate": True,
    },
    "create_team": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new team.",
        "schema_file": "open-api-definitions/teams/create_team.json",
        "method": "POST",
        "endpoint": "/v1/teams",
        "params": ["request_body"],
    },
    "get_team_details": {
        "description": "Get details of a team.",
        "schema_file": "open-api-definitions/teams/team_details.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}",
        "params": ["team_id"],
    },
    "modify_team": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a team.",
        "schema_file": "open-api-definitions/teams/modify_team.json",
        "method": "PATCH",
        "endpoint": "/v1/teams/{team_id}",
        "params": ["team_id", "request_body"],
    },
    "delete_team": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a team permanently.",
        "schema_file": "open-api-definitions/teams/delete_team.json",
        "method": "DELETE",
        "endpoint": "/v1/teams/{team_id}",
        "params": ["team_id"],
    },
    "delete_team_membership_in_account": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a team's account-level role.",
        "schema_file": "open-api-definitions/teams/delete_team_membership_in_account.json",
        "method": "DELETE",
        "endpoint": "/v1/teams/{team_id}/role",
        "params": ["team_id"],
    },
    "list_users_in_team": {
        "description": "List all users in a team.",
        "schema_file": "open-api-definitions/teams/list_users_in_team.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/users",
        "params": ["team_id"],
        "auto_paginate": True,
    },
    "add_user_to_team": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a user to a team.",
        "schema_file": "open-api-definitions/teams/add_user_to_team.json",
        "method": "POST",
        "endpoint": "/v1/teams/{team_id}/users",
        "params": ["team_id", "request_body"],
    },
    "get_user_in_team": {
        "description": "Get a user's membership in a team.",
        "schema_file": "open-api-definitions/teams/get_user_in_team.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/users/{user_id}",
        "params": ["team_id", "user_id"],
    },
    "update_user_membership_in_team": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a user's membership in a team.",
        "schema_file": "open-api-definitions/teams/update_user_membership.json",
        "method": "PATCH",
        "endpoint": "/v1/teams/{team_id}/users/{user_id}",
        "params": ["team_id", "user_id", "request_body"],
    },
    "delete_user_from_team": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a user from a team.",
        "schema_file": "open-api-definitions/teams/delete_user_from_team.json",
        "method": "DELETE",
        "endpoint": "/v1/teams/{team_id}/users/{user_id}",
        "params": ["team_id", "user_id"],
    },
    "list_team_memberships_in_groups": {
        "description": "List a team's group memberships.",
        "schema_file": "open-api-definitions/teams/get_team_memberships_in_groups.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/groups",
        "params": ["team_id"],
        "auto_paginate": True,
    },
    "add_team_membership_in_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a team to a group.",
        "schema_file": "open-api-definitions/teams/add_team_membership_in_group.json",
        "method": "POST",
        "endpoint": "/v1/teams/{team_id}/groups",
        "params": ["team_id", "request_body"],
    },
    "get_team_membership_in_group": {
        "description": "Get a team's membership in a group.",
        "schema_file": "open-api-definitions/teams/get_team_membership_in_group.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
        "params": ["team_id", "group_id"],
    },
    "update_team_membership_in_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a team's membership in a group.",
        "schema_file": "open-api-definitions/teams/update_team_membership_in_group.json",
        "method": "PATCH",
        "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
        "params": ["team_id", "group_id", "request_body"],
    },
    "delete_team_membership_in_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a team from a group.",
        "schema_file": "open-api-definitions/teams/delete_team_membership_in_group.json",
        "method": "DELETE",
        "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
        "params": ["team_id", "group_id"],
    },
    "list_team_memberships_in_connections": {
        "description": "List a team's connection memberships.",
        "schema_file": "open-api-definitions/teams/get_team_memberships_in_connections.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/connections",
        "params": ["team_id"],
        "auto_paginate": True,
    },
    "add_team_membership_in_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a team to a connection.",
        "schema_file": "open-api-definitions/teams/add_team_membership_in_connection.json",
        "method": "POST",
        "endpoint": "/v1/teams/{team_id}/connections",
        "params": ["team_id", "request_body"],
    },
    "get_team_membership_in_connection": {
        "description": "Get a team's membership in a connection.",
        "schema_file": "open-api-definitions/teams/get_team_membership_in_connection.json",
        "method": "GET",
        "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
        "params": ["team_id", "connection_id"],
    },
    "update_team_membership_in_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a team's membership in a connection.",
        "schema_file": "open-api-definitions/teams/update_team_membership_in_connection.json",
        "method": "PATCH",
        "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
        "params": ["team_id", "connection_id", "request_body"],
    },
    "delete_team_membership_in_connection": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a team from a connection.",
        "schema_file": "open-api-definitions/teams/delete_team_membership_in_connection.json",
        "method": "DELETE",
        "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
        "params": ["team_id", "connection_id"],
    },

    # ============================================================================
    # TRANSFORMATION PROJECTS
    # ============================================================================
    "list_transformation_projects": {
        "description": "List all transformation projects.",
        "schema_file": "open-api-definitions/transformation-projects/list_all_transformation_projects.json",
        "method": "GET",
        "endpoint": "/v1/transformation-projects",
        "auto_paginate": True,
    },
    "create_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/create_transformation_project.json",
        "method": "POST",
        "endpoint": "/v1/transformation-projects",
        "params": ["request_body"],
    },
    "get_transformation_project_details": {
        "description": "Get details of a transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/transformation_project_details.json",
        "method": "GET",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id"],
    },
    "modify_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/modify_transformation_project.json",
        "method": "PATCH",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id", "request_body"],
    },
    "delete_transformation_project": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a transformation project permanently.",
        "schema_file": "open-api-definitions/transformation-projects/delete_transformation_project.json",
        "method": "DELETE",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id"],
    },
    "test_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Test a transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/test_transformation_project.json",
        "method": "POST",
        "endpoint": "/v1/transformation-projects/{project_id}/test",
        "params": ["project_id"],
    },

    # ============================================================================
    # TRANSFORMATIONS
    # ============================================================================
    "list_transformations": {
        "description": "List all transformations.",
        "schema_file": "open-api-definitions/transformations/transformations_list.json",
        "method": "GET",
        "endpoint": "/v1/transformations",
        "auto_paginate": True,
    },
    "create_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a new transformation.",
        "schema_file": "open-api-definitions/transformations/create_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations",
        "params": ["request_body"],
    },
    "get_transformation_details": {
        "description": "Get details of a transformation.",
        "schema_file": "open-api-definitions/transformations/transformation_details.json",
        "method": "GET",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id"],
    },
    "update_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a transformation.",
        "schema_file": "open-api-definitions/transformations/update_transformation.json",
        "method": "PATCH",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id", "request_body"],
    },
    "delete_transformation": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a transformation permanently.",
        "schema_file": "open-api-definitions/transformations/delete_transformation.json",
        "method": "DELETE",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id"],
    },
    "run_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Run a transformation.",
        "schema_file": "open-api-definitions/transformations/run_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/run",
        "params": ["transformation_id"],
    },
    "cancel_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Cancel a running transformation.",
        "schema_file": "open-api-definitions/transformations/cancel_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/cancel",
        "params": ["transformation_id"],
    },
    "upgrade_transformation_package": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Upgrade a transformation's package version.",
        "schema_file": "open-api-definitions/transformations/upgrade_transformation_package.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/upgrade",
        "params": ["transformation_id", "request_body"],
    },
    "list_transformation_package_metadata": {
        "description": "List all quickstart package metadata.",
        "schema_file": "open-api-definitions/transformations/transformation_package_metadata_list.json",
        "method": "GET",
        "endpoint": "/v1/transformations/package-metadata",
        "auto_paginate": True,
    },
    "get_transformation_package_metadata_details": {
        "description": "Get details of a quickstart package.",
        "schema_file": "open-api-definitions/transformations/transformation_package_metadata_details.json",
        "method": "GET",
        "endpoint": "/v1/transformations/package-metadata/{package_definition_id}",
        "params": ["package_definition_id"],
    },

    # ============================================================================
    # USERS
    # ============================================================================
    "list_users": {
        "description": "List all users in your account.",
        "schema_file": "open-api-definitions/users/list_all_users.json",
        "method": "GET",
        "endpoint": "/v1/users",
        "auto_paginate": True,
    },
    "create_user": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Invite a new user to the account.",
        "schema_file": "open-api-definitions/users/create_user.json",
        "method": "POST",
        "endpoint": "/v1/users",
        "params": ["request_body"],
    },
    "get_user_details": {
        "description": "Get details of a user.",
        "schema_file": "open-api-definitions/users/user_details.json",
        "method": "GET",
        "endpoint": "/v1/users/{user_id}",
        "params": ["user_id"],
    },
    "modify_user": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a user.",
        "schema_file": "open-api-definitions/users/modify_user.json",
        "method": "PATCH",
        "endpoint": "/v1/users/{user_id}",
        "params": ["user_id", "request_body"],
    },
    "delete_user": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a user permanently.",
        "schema_file": "open-api-definitions/users/delete_user.json",
        "method": "DELETE",
        "endpoint": "/v1/users/{user_id}",
        "params": ["user_id"],
    },
    "delete_user_membership_in_account": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a user's account-level role.",
        "schema_file": "open-api-definitions/users/delete_user_membership_in_account.json",
        "method": "DELETE",
        "endpoint": "/v1/users/{user_id}/role",
        "params": ["user_id"],
    },
    "list_user_memberships_in_groups": {
        "description": "List a user's group memberships.",
        "schema_file": "open-api-definitions/users/get_user_memberships_in_groups.json",
        "method": "GET",
        "endpoint": "/v1/users/{user_id}/groups",
        "params": ["user_id"],
        "auto_paginate": True,
    },
    "add_user_membership_in_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a user to a group.",
        "schema_file": "open-api-definitions/users/add_user_membership_in_group.json",
        "method": "POST",
        "endpoint": "/v1/users/{user_id}/groups",
        "params": ["user_id", "request_body"],
    },
    "get_user_membership_in_group": {
        "description": "Get a user's membership in a group.",
        "schema_file": "open-api-definitions/users/get_user_membership_in_group.json",
        "method": "GET",
        "endpoint": "/v1/users/{user_id}/groups/{group_id}",
        "params": ["user_id", "group_id"],
    },
    "update_user_membership_in_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a user's membership in a group.",
        "schema_file": "open-api-definitions/users/update_user_membership_in_group.json",
        "method": "PATCH",
        "endpoint": "/v1/users/{user_id}/groups/{group_id}",
        "params": ["user_id", "group_id", "request_body"],
    },
    "delete_user_membership_in_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a user from a group.",
        "schema_file": "open-api-definitions/users/delete_user_membership_in_group.json",
        "method": "DELETE",
        "endpoint": "/v1/users/{user_id}/groups/{group_id}",
        "params": ["user_id", "group_id"],
    },
    "list_user_memberships_in_connections": {
        "description": "List a user's connection memberships.",
        "schema_file": "open-api-definitions/users/get_user_memberships_in_connections.json",
        "method": "GET",
        "endpoint": "/v1/users/{user_id}/connections",
        "params": ["user_id"],
        "auto_paginate": True,
    },
    "add_user_membership_in_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Add a user to a connection.",
        "schema_file": "open-api-definitions/users/add_user_membership_in_connection.json",
        "method": "POST",
        "endpoint": "/v1/users/{user_id}/connections",
        "params": ["user_id", "request_body"],
    },
    "get_user_membership_in_connection": {
        "description": "Get a user's membership in a connection.",
        "schema_file": "open-api-definitions/users/get_user_membership_in_connections.json",
        "method": "GET",
        "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
        "params": ["user_id", "connection_id"],
    },
    "update_user_membership_in_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a user's membership in a connection.",
        "schema_file": "open-api-definitions/users/update_user_membership_in_connection.json",
        "method": "PATCH",
        "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
        "params": ["user_id", "connection_id", "request_body"],
    },
    "delete_user_membership_in_connection": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Remove a user from a connection.",
        "schema_file": "open-api-definitions/users/delete_user_membership_in_connection.json",
        "method": "DELETE",
        "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
        "params": ["user_id", "connection_id"],
    },

    # ============================================================================
    # WEBHOOKS
    # ============================================================================
    "list_webhooks": {
        "description": "List all webhooks.",
        "schema_file": "open-api-definitions/webhooks/list_all_webhooks.json",
        "method": "GET",
        "endpoint": "/v1/webhooks",
        "auto_paginate": True,
    },
    "create_account_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create an account-level webhook.",
        "schema_file": "open-api-definitions/webhooks/create_account_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/account",
        "params": ["request_body"],
    },
    "create_group_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Create a group-level webhook.",
        "schema_file": "open-api-definitions/webhooks/create_group_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/group/{group_id}",
        "params": ["group_id", "request_body"],
    },
    "get_webhook_details": {
        "description": "Get details of a webhook.",
        "schema_file": "open-api-definitions/webhooks/webhook_details.json",
        "method": "GET",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id"],
    },
    "modify_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Update a webhook.",
        "schema_file": "open-api-definitions/webhooks/modify_webhook.json",
        "method": "PATCH",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id", "request_body"],
    },
    "delete_webhook": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a webhook permanently.",
        "schema_file": "open-api-definitions/webhooks/delete_webhook.json",
        "method": "DELETE",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id"],
    },
    "test_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Send a test event to a webhook.",
        "schema_file": "open-api-definitions/webhooks/test_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/{webhook_id}/test",
        "params": ["webhook_id", "request_body"],
    },
}


PARAM_DEFINITIONS = {
    "connection_id": {"type": "string", "description": "The unique identifier for the connection"},
    "destination_id": {"type": "string", "description": "The unique identifier for the destination"},
    "group_id": {"type": "string", "description": "The unique identifier for the group"},
    "user_id": {"type": "string", "description": "The unique identifier for the user"},
    "team_id": {"type": "string", "description": "The unique identifier for the team"},
    "webhook_id": {"type": "string", "description": "The unique identifier for the webhook"},
    "agent_id": {"type": "string", "description": "The unique identifier for the agent"},
    "log_id": {"type": "string", "description": "The unique identifier for the log service"},
    "private_link_id": {"type": "string", "description": "The unique identifier for the private link"},
    "project_id": {"type": "string", "description": "The unique identifier for the transformation project"},
    "transformation_id": {"type": "string", "description": "The unique identifier for the transformation"},
    "key_id": {"type": "string", "description": "The unique identifier for the system key"},
    "hash": {"type": "string", "description": "The hash of the certificate or fingerprint"},
    "service": {"type": "string", "description": "The connector service type (e.g., 'google_sheets', 'salesforce')"},
    "schema_name": {"type": "string", "description": "The name of the database schema"},
    "table_name": {"type": "string", "description": "The name of the table"},
    "column_name": {"type": "string", "description": "The name of the column"},
    "package_definition_id": {"type": "string", "description": "The unique identifier for the quickstart package"},
    "request_body": {"type": "string", "description": "JSON string containing the request body. Refer to the schema file for the expected structure."},
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
    for param in tool_config.get("params", []):
        if param in PARAM_DEFINITIONS:
            properties[param] = PARAM_DEFINITIONS[param].copy()
        required.append(param)

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
    """Handle tool calls with mandatory schema validation and write confirmation."""
    try:
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
    tool_config = TOOLS[name]
    method = tool_config["method"]
    endpoint_template = tool_config["endpoint"]

    # Build path parameters dict (exclude schema_file and request_body)
    path_params = {
        k: v for k, v in arguments.items()
        if k not in ("schema_file", "request_body")
    }

    # Format endpoint with path parameters
    endpoint = endpoint_template.format(**path_params)

    # Parse request body if present
    json_body = None
    if "request_body" in arguments:
        try:
            json_body = json.loads(arguments["request_body"])
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in request_body: {e}")

    # Execute request
    if tool_config.get("auto_paginate"):
        return await fivetran_request_all_pages(endpoint)
    else:
        return await fivetran_request(method, endpoint, json_body=json_body)


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
