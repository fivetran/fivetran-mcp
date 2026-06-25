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
FIVETRAN_API_KEY = os.getenv("FIVETRAN_API_KEY")
FIVETRAN_API_SECRET = os.getenv("FIVETRAN_API_SECRET")
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
        raise ValueError("FIVETRAN_API_KEY and FIVETRAN_API_SECRET must be set in environment")
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
        "description": "Returns information about current account from API key.",
        "schema_file": "open-api-definitions/account/get_account_info.json",
        "method": "GET",
        "endpoint": "/v1/account/info",
    },
    # ============================================================================
    # CERTIFICATES (Deprecated)
    # ============================================================================
    # ============================================================================
    # CONNECTIONS
    # ============================================================================
    "list_connections": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all accessible connections within your Fivetran account.",
        "schema_file": "open-api-definitions/connections/list_connections.json",
        "method": "GET",
        "endpoint": "/v1/connections",
        "query_params": ["group_id", "schema", "cursor", "limit"],
    },
    "create_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new connection within a specified group in your Fivetran account. Runs setup tests and returns testing results. <br /> > IMPORTANT: The `destination_schema_names` field will soon become a required field. Make sure to include it in your API requests when creating new connections to prevent future disruptions. > IMPORTANT: If you want to get the fingerprint details, do not set `trust_fingerprints` to `true` when you create a connection with our REST API. We can only provide the fingerprint details through the failed SSH Tunnel Connection setup test. For a full walkthrough, see [Get Connection Fingerprint Details](https://fivetran.com/docs/rest-api/tutorials/get-connection-fingerprint-details).",
        "schema_file": "open-api-definitions/connections/create_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections",
        "params": ["request_body"],
    },
    "connection_details": {
        "description": "Returns a connection configuration and status details if a valid identifier was provided.",
        "schema_file": "open-api-definitions/connections/connection_details.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id"],
    },
    "modify_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates connection parameters for an existing connection within your Fivetran account. This endpoint requires at least one persistent configuration parameter to be specified (e.g., `sync_frequency`, `paused`, `config`, `auth`, `daily_sync_time`, `schema_status`). > IMPORTANT: Parameters like `trust_certificates`, `trust_fingerprints`, and `run_setup_tests` are test-control parameters that affect only the behavior of setup tests during the update and do not persist in the connection configuration; they cannot be used on their own. If you want to run setup tests without making configuration changes, use the POST `/v1/connections/{connectionId}/test` endpoint instead.",
        "schema_file": "open-api-definitions/connections/modify_connection.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id", "request_body"],
    },
    "delete_connection": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a connection from your Fivetran account.",
        "schema_file": "open-api-definitions/connections/delete_connection.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}",
        "params": ["connection_id"],
    },
    # "get_connection_certificates_list": {
    #     "description": "Returns the list of approved certificates for the specified connection.",
    #     "schema_file": "open-api-definitions/connections/get_connection_certificates_list.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connections/{connection_id}/certificates",
    #     "params": ["connection_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "approve_connection_certificate": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approves a certificate, so Fivetran trusts this certificate for a source database connection. The connection setup tests will fail if a non-approved certificate is provided. > NOTE: This is only required for source connections based on the following databases: > - [MySQL](https://fivetran.com/docs/connectors/databases/mysql#supportedservices) > - [PostgreSQL](https://fivetran.com/docs/connectors/databases/postgresql#supportedservices) > - [SQLServer](https://fivetran.com/docs/connectors/databases/sql-server#supportedservices)",
    #     "schema_file": "open-api-definitions/connections/approve_connection_certificate.json",
    #     "method": "POST",
    #     "endpoint": "/v1/connections/{connection_id}/certificates",
    #     "params": ["connection_id", "request_body"],
    # },
    # "get_connection_certificate_details": {
    #     "description": "Returns details of the certificate approved for the specified connection with specified certificate hash.",
    #     "schema_file": "open-api-definitions/connections/get_connection_certificate_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connections/{connection_id}/certificates/{hash}",
    #     "params": ["connection_id", "hash"],
    # },
    # "revoke_connection_certificate": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revokes a certificate, so Fivetran no longer trusts it while connecting to the source database.",
    #     "schema_file": "open-api-definitions/connections/revoke_connection_certificate.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/connections/{connection_id}/certificates/{hash}",
    #     "params": ["connection_id", "hash"],
    # },
    "connect_card": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Generates the Connect Card URI for the connection",
        "schema_file": "open-api-definitions/connections/connect_card.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/connect-card",
        "params": ["connection_id", "request_body"],
    },
    # "get_connection_fingerprints_list": {
    #     "description": "Returns the list of approved SSH fingerprints for specified connection",
    #     "schema_file": "open-api-definitions/connections/get_connection_fingerprints_list.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connections/{connection_id}/fingerprints",
    #     "params": ["connection_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "approve_connection_fingerprint": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approves a fingerprint, enabling Fivetran to trust it for a source database and establish connections via an SSH tunnel.",
    #     "schema_file": "open-api-definitions/connections/approve_connection_fingerprint.json",
    #     "method": "POST",
    #     "endpoint": "/v1/connections/{connection_id}/fingerprints",
    #     "params": ["connection_id", "request_body"],
    # },
    # "get_connection_fingerprint_details": {
    #     "description": "Returns SSH fingerprint details approved for specified connection with specified hash",
    #     "schema_file": "open-api-definitions/connections/get_connection_fingerprint_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connections/{connection_id}/fingerprints/{hash}",
    #     "params": ["connection_id", "hash"],
    # },
    # "revoke_connection_fingerprint": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revokes a fingerprint, so Fivetran no longer trusts it while connecting to the source database through an SSH tunnel.",
    #     "schema_file": "open-api-definitions/connections/revoke_connection_fingerprint.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/connections/{connection_id}/fingerprints/{hash}",
    #     "params": ["connection_id", "hash"],
    # },
    "resync_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Triggers a full historical sync of a connection or multiple schema tables within a connection. If the connection is paused, the table sync will be scheduled to be performed when the connection is re-enabled. If there is a data sync already in progress, we will try to complete it. If it fails, the request will be declined and the HTTP 409 Conflict error will be returned.",
        "schema_file": "open-api-definitions/connections/resync_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/resync",
        "params": ["connection_id", "request_body"],
    },
    "connection_schema_config": {
        "description": "Returns the top-level schema configuration for an existing connection within your Fivetran account. The response includes global flags, every schema, each table, and only the columns that were explicitly overridden. Use this endpoint to read the current data-selection tree for a connection, to back up the schema before making edits, or to copy the configuration to another connection. > NOTE: To restore a backed-up schema or copy the configuration to another connection, use the [Update a Connection Schema Config](/docs/rest-api/api-reference/connection-schema/modify-connection-schema-config) endpoint. For more information, see the [Connection Schema config](https://fivetran.com/docs/rest-api/tutorials/connection-schema-configuration-use-cases) tutorial. > NOTE: Unedited columns (those following table defaults) are omitted from the response. For a real-time, exhaustive column list for a specific table, call the [Retrieve Source Table Columns Config](/docs/rest-api/api-reference/connection-schema/connection-column-config) endpoint. For the NetSuite SuiteAnalytics, and Salesforce and Salesforce Sandbox connectors, the 'schemas' map field contains a single entry with the 'netsuite' or 'salesforce' key, respectively. For the 'schema.name_in_destination` name field, these connectors always return the destination schema name you set in the connection setup form. For more information on using this API endpoint with the the Oracle Fusion Cloud Applications connectors, see the [Schema information documentation](https://fivetran.com/docs/connectors/applications/oracle-fusion-cloud-applications#schemainformation). > IMPORTANT: This endpoint does not apply to [Magic Folder](/docs/connectors/files#magicfolder) connectors.",
        "schema_file": "open-api-definitions/connections/connection_schema_config.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/schemas",
        "params": ["connection_id"],
    },
    # "pre_create_connection_schema_config": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Configures a Connection Schema for a new connection before the schema is captured from the source. > NOTE: The response returns the exact settings provided in the request. After the initial sync, when the connection captures the schema from the source, Fivetran attempts to apply the specified settings to the actual schema. If certain tables or columns cannot be excluded, the settings for those entities are ignored.",
    #     "schema_file": "open-api-definitions/connections/pre_create_connection_schema_config.json",
    #     "method": "POST",
    #     "endpoint": "/v1/connections/{connection_id}/schemas",
    #     "params": ["connection_id", "request_body"],
    # },
    "modify_connection_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the schema config for an existing connection within your Fivetran account. > NOTE: For backward compatibility, the response may contain the 'enable_new_by_default' boolean field. It defines whether new schemas and tables discovered in the source are synced. The value is 'true' if you specify 'ALLOW_ALL' as a value of 'schema_change_handling'. In the future API versions, we may remove this field. > > The response contains all known schemas and tables. Also, it contains columns whose state has ever been set by the user. For more information, see also the [Connection Schema config](https://fivetran.com/docs/rest-api/tutorials/connection-schema-configuration-use-cases) tutorial.",
        "schema_file": "open-api-definitions/connections/modify_connection_schema_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas",
        "params": ["connection_id", "request_body"],
    },
    "delete_multiple_columns_connection_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Mark multiple blocked columns for deletion from your destination tables. The columns will be dropped during the next sync.",
        "schema_file": "open-api-definitions/connections/delete_multiple_columns_connection_config.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/drop-columns",
        "params": ["connection_id", "request_body"],
    },
    "reload_connection_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Reloads the connection schema config for an existing connection within your Fivetran account. > NOTE: This method reloads the full schema from the connection's data source. It may take a long time to complete the request. The method execution speed depends on the schema size and the number of databases, tables, and columns. > > The response contains all known schemas and tables. Also, it contains columns whose state has ever been set by the user. For more information, see also the [Connection Schema config](https://fivetran.com/docs/rest-api/tutorials/connection-schema-configuration-use-cases) tutorial.",
        "schema_file": "open-api-definitions/connections/reload_connection_schema_config.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/reload",
        "params": ["connection_id", "request_body"],
    },
    "resync_tables": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Triggers a historical sync of all data for multiple schema tables within a connection. This action does not override the standard sync frequency you defined in the Fivetran dashboard.",
        "schema_file": "open-api-definitions/connections/resync_tables.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/schemas/tables/resync",
        "params": ["connection_id", "request_body"],
    },
    "modify_connection_database_schema_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the database schema config for an existing connection within your Fivetran account (for a single schema within a connection with multiple schemas). > NOTE: The response contains all known schemas and tables. Also, it contains columns whose state has ever been set by the user. For more information, see also the [Connection Schema config](https://fivetran.com/docs/rest-api/tutorials/connection-schema-configuration-use-cases) tutorial. In this API call, the NetSuite SuiteAnalytics, Salesforce and Salesforce Sandbox connectors always return the schema name as 'netsuite' and 'salesforce', respectively. For more information about this API call for the Oracle Fusion Cloud Applications connectors, see our [Schema information](https://fivetran.com/docs/connectors/applications/oracle-fusion-cloud-applications#schemainformation) documentation.",
        "schema_file": "open-api-definitions/connections/modify_connection_database_schema_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}",
        "params": ["connection_id", "schema_name", "request_body"],
    },
    "modify_connection_table_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the table config within your database schema for an existing connection within your Fivetran account. For the NetSuite SuiteAnalytics and Salesforce and Salesforce Sandbox connectors, the 'schemas' map field will always have a single entry with the 'netsuite' or 'salesforce' key, respectively.",
        "schema_file": "open-api-definitions/connections/modify_connection_table_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}",
        "params": ["connection_id", "schema_name", "table_name", "request_body"],
    },
    "modify_connection_column_config": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the column config within your table for an existing connection within your Fivetran account. For the NetSuite SuiteAnalytics and Salesforce and Salesforce Sandbox connectors, the 'schemas' map field will always have a single entry with the 'netsuite' or 'salesforce' key, respectively. > NOTE: The response contains all known schemas and tables. Also, it contains columns whose state has ever been set by the user. For more information, see also the [Connection Schema config](https://fivetran.com/docs/rest-api/tutorials/connection-schema-configuration-use-cases) tutorial.",
        "schema_file": "open-api-definitions/connections/modify_connection_column_config.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}/columns/{column_name}",
        "params": ["connection_id", "schema_name", "table_name", "column_name", "request_body"],
    },
    "delete_column_connection_config": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Marks a blocked column for deletion from your destination table. The column will be dropped during the next sync. For the NetSuite SuiteAnalytics and Salesforce and Salesforce Sandbox connectors, the 'schemas' map field will always have a single entry with the 'netsuite' or 'salesforce' key, respectively.",
        "schema_file": "open-api-definitions/connections/delete_column_connection_config.json",
        "method": "DELETE",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema_name}/tables/{table_name}/columns/{column_name}",
        "params": ["connection_id", "schema_name", "table_name", "column_name"],
    },
    "connection_column_config": {
        "description": "Returns the real-time column list for one source table by querying the source. The response includes the current enabled and hashed flags, and the patchable fields. > NOTE: This endpoint works only for an existing connection that is in a 'Connected' state.",
        "schema_file": "open-api-definitions/connections/connection_column_config.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/schemas/{schema}/tables/{table}/columns",
        "params": ["connection_id", "schema", "table"],
    },
    "connection_state": {
        "description": "Returns the connection state. This endpoint is only supported for Function and Connection SDK connectors. To update the connection state, use [Update Connection State](https://fivetran.com/docs/rest-api/api-reference/connections/modify-connection-state).",
        "schema_file": "open-api-definitions/connections/connection_state.json",
        "method": "GET",
        "endpoint": "/v1/connections/{connection_id}/state",
        "params": ["connection_id"],
    },
    "modify_connection_state": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the connection state. To update the state, you should pause your connection first. To update the connection state, do the following: 1. Pause connection using [Update a Connection](https://fivetran.com/docs/rest-api/api-reference/connections/modify-connection) endpoint (set 'paused' to 'true'). 2. Update the state by using the [Update Connection State](https://fivetran.com/docs/rest-api/api-reference/connections/modify-connection-state) endpoint. 3. Unpause the connection by setting the 'paused' parameter to 'false' in the [Update a Connection](https://fivetran.com/docs/rest-api/api-reference/connections/modify-connection) endpoint request. This endpoint is only supported for [Function](https://fivetran.com/docs/connectors/functions) and [Connection SDK](https://fivetran.com/docs/connectors/connector-sdk) connectors.",
        "schema_file": "open-api-definitions/connections/modify_connection_state.json",
        "method": "PATCH",
        "endpoint": "/v1/connections/{connection_id}/state",
        "params": ["connection_id", "request_body"],
    },
    "sync_connection": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Triggers a data sync for an existing connection within your Fivetran account without waiting for the next scheduled sync. This action does not override the standard sync frequency you defined in the Fivetran dashboard. When `schedule_type` is set to `manual`, this endpoint is the only way syncs occur — including syncs in a `rescheduled` state. For a full walkthrough, see [Trigger Manual Syncs](https://fivetran.com/docs/rest-api/tutorials/trigger-syncs-manually).",
        "schema_file": "open-api-definitions/connections/sync_connection.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/sync",
        "params": ["connection_id", "request_body"],
    },
    "run_setup_tests": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Runs the setup tests for an existing connection within your Fivetran account. Use this parameter to test the connection without making any configuration changes. You can optionally include `trust_certificates` or `trust_fingerprints` parameters to automatically approve certificates or fingerprints during the test run.",
        "schema_file": "open-api-definitions/connections/run_setup_tests.json",
        "method": "POST",
        "endpoint": "/v1/connections/{connection_id}/test",
        "params": ["connection_id", "request_body"],
    },
    # ============================================================================
    # DESTINATIONS
    # ============================================================================
    "list_destinations": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all accessible destinations within your Fivetran account.",
        "schema_file": "open-api-definitions/destinations/list_destinations.json",
        "method": "GET",
        "endpoint": "/v1/destinations",
        "query_params": ["cursor", "limit"],
    },
    "create_destination": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new destination within a specified group in your Fivetran account. > IMPORTANT: Groups and destinations are mapped 1:1 to each other. We do this mapping using the group's `id` value that we automatically generate when you create a group, and the destination's `group_id` value that you specify when you create a destination. This means that you must create a group in your Fivetran account before you can create a destination in it. > IMPORTANT: If you want to get the certificate details, do not set `trust_certificates` to `true` when you create a destination with our REST API. We can only provide the certificate details through the failed Validate Certificate setup test. For a full walkthrough, see [Get Destination Certificate Details](https://fivetran.com/docs/rest-api/tutorials/get-destination-certificate-details).",
        "schema_file": "open-api-definitions/destinations/create_destination.json",
        "method": "POST",
        "endpoint": "/v1/destinations",
        "params": ["request_body"],
    },
    "destination_details": {
        "description": "Returns a destination object if a valid identifier was provided. To find a destination's unique identifier, call the [List All Groups](https://fivetran.com/docs/rest-api/groups#listallgroups) endpoint and search the response `items` for your target destination by its `name` field. The group's `id` value is also the destination's `id`, since groups and destinations are mapped 1:1.",
        "schema_file": "open-api-definitions/destinations/destination_details.json",
        "method": "GET",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id"],
    },
    "modify_destination": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing destination within your Fivetran account.",
        "schema_file": "open-api-definitions/destinations/modify_destination.json",
        "method": "PATCH",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id", "request_body"],
    },
    "delete_destination": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a destination from your Fivetran account.",
        "schema_file": "open-api-definitions/destinations/delete_destination.json",
        "method": "DELETE",
        "endpoint": "/v1/destinations/{destination_id}",
        "params": ["destination_id"],
    },
    # "get_destination_certificates_list": {
    #     "description": "Returns the list of approved certificates for the specified destination.",
    #     "schema_file": "open-api-definitions/destinations/get_destination_certificates_list.json",
    #     "method": "GET",
    #     "endpoint": "/v1/destinations/{destination_id}/certificates",
    #     "params": ["destination_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "approve_destination_certificate": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approves a certificate, so Fivetran trusts this certificate for a destination database connection. The destination connection setup tests will fail if a non-approved certificate is provided. > NOTE: This is only required for destination connections based on the following databases: > - [MySQL](https://fivetran.com/docs/destinations/mysql#supportedimplementations) > - [PostgreSQL](https://fivetran.com/docs/destinations/postgresql#supportedimplementations) > - [SQLServer](https://fivetran.com/docs/destinations/sql-server#supportedimplementations)",
    #     "schema_file": "open-api-definitions/destinations/approve_destination_certificate.json",
    #     "method": "POST",
    #     "endpoint": "/v1/destinations/{destination_id}/certificates",
    #     "params": ["destination_id", "request_body"],
    # },
    # "get_destination_certificate_details": {
    #     "description": "Returns details of the certificate approved for the specified destination with specified certificate hash.",
    #     "schema_file": "open-api-definitions/destinations/get_destination_certificate_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/destinations/{destination_id}/certificates/{hash}",
    #     "params": ["destination_id", "hash"],
    # },
    # "revoke_destination_certificate": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revokes a certificate, so Fivetran no longer trusts it while connecting to the destination database.",
    #     "schema_file": "open-api-definitions/destinations/revoke_destination_certificate.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/destinations/{destination_id}/certificates/{hash}",
    #     "params": ["destination_id", "hash"],
    # },
    # "get_destination_fingerprints_list": {
    #     "description": "Returns the list of approved SSH fingerprints for specified destination",
    #     "schema_file": "open-api-definitions/destinations/get_destination_fingerprints_list.json",
    #     "method": "GET",
    #     "endpoint": "/v1/destinations/{destination_id}/fingerprints",
    #     "params": ["destination_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "approve_destination_fingerprint": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approves a fingerprint, enabling Fivetran to trust it for a destination database and establish connections via an SSH tunnel.",
    #     "schema_file": "open-api-definitions/destinations/approve_destination_fingerprint.json",
    #     "method": "POST",
    #     "endpoint": "/v1/destinations/{destination_id}/fingerprints",
    #     "params": ["destination_id", "request_body"],
    # },
    # "get_destination_fingerprint_details": {
    #     "description": "Returns SSH fingerprint details approved for specified destination with specified hash",
    #     "schema_file": "open-api-definitions/destinations/get_destination_fingerprint_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/destinations/{destination_id}/fingerprints/{hash}",
    #     "params": ["destination_id", "hash"],
    # },
    # "revoke_destination_fingerprint": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Revokes a fingerprint, so Fivetran no longer trusts it while connecting to the destination database through an SSH tunnel.",
    #     "schema_file": "open-api-definitions/destinations/revoke_destination_fingerprint.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/destinations/{destination_id}/fingerprints/{hash}",
    #     "params": ["destination_id", "hash"],
    # },
    "run_destination_setup_tests": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Runs the setup tests for an existing destination within your Fivetran account.",
        "schema_file": "open-api-definitions/destinations/run_destination_setup_tests.json",
        "method": "POST",
        "endpoint": "/v1/destinations/{destination_id}/test",
        "params": ["destination_id", "request_body"],
    },
    # ============================================================================
    # EXTERNAL LOGGING
    # ============================================================================
    "list_log_services": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all accessible [logging services](/docs/logs/external-logs) within your Fivetran account.",
        "schema_file": "open-api-definitions/external-logging/list_log_services.json",
        "method": "GET",
        "endpoint": "/v1/external-logging",
        "query_params": ["cursor", "limit"],
    },
    "add_log_service": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new group-level [logging service](/docs/logs/external-logs) within a specified group in your Fivetran account.",
        "schema_file": "open-api-definitions/external-logging/add_log_service.json",
        "method": "POST",
        "endpoint": "/v1/external-logging",
        "params": ["request_body"],
    },
    # "get_account_log_service_details": {
    #     "description": "Returns the account-level [logging service](/docs/logs/external-logs) if it exists.",
    #     "schema_file": "open-api-definitions/external-logging/get_account_log_service_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/external-logging/account",
    # },
    # "add_account_log_service": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates an account-level [logging service](/docs/logs/external-logs).",
    #     "schema_file": "open-api-definitions/external-logging/add_account_log_service.json",
    #     "method": "POST",
    #     "endpoint": "/v1/external-logging/account",
    #     "params": ["request_body"],
    # },
    # "update_account_log_service": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for the account-level [logging service](/docs/logs/external-logs).",
    #     "schema_file": "open-api-definitions/external-logging/update_account_log_service.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/external-logging/account",
    #     "params": ["request_body"],
    # },
    # "delete_account_log_service": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes the account-level [logging service](/docs/logs/external-logs).",
    #     "schema_file": "open-api-definitions/external-logging/delete_account_log_service.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/external-logging/account",
    # },
    "get_log_service_details": {
        "description": "Returns a group-level [logging service](/docs/logs/external-logs) object if a valid identifier was provided.",
        "schema_file": "open-api-definitions/external-logging/get_log_service_details.json",
        "method": "GET",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id"],
    },
    "update_log_service": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing group-level [logging service](/docs/logs/external-logs) within your Fivetran account.",
        "schema_file": "open-api-definitions/external-logging/update_log_service.json",
        "method": "PATCH",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id", "request_body"],
    },
    "delete_log_service": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a group-level [logging service](/docs/logs/external-logs) from your Fivetran account.",
        "schema_file": "open-api-definitions/external-logging/delete_log_service.json",
        "method": "DELETE",
        "endpoint": "/v1/external-logging/{log_id}",
        "params": ["log_id"],
    },
    "run_setup_tests_log_service": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Runs the setup tests for an existing group-level [logging service](/docs/logs/external-logs) within your Fivetran account.",
        "schema_file": "open-api-definitions/external-logging/run_setup_tests_log_service.json",
        "method": "POST",
        "endpoint": "/v1/external-logging/{log_id}/test",
        "params": ["log_id", "request_body"],
    },
    # ============================================================================
    # GROUPS
    # ============================================================================
    "list_all_groups": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all groups within your Fivetran account.",
        "schema_file": "open-api-definitions/groups/list_all_groups.json",
        "method": "GET",
        "endpoint": "/v1/groups",
        "query_params": ["cursor", "limit"],
    },
    "create_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new group in your Fivetran account. > IMPORTANT: Groups and destinations are mapped 1:1 to each other. We do this mapping using the group's `id` value that we automatically generate when you create a group, and the destination's `group_id` value that you specify when you create a destination. This means that you must create a group in your Fivetran account before you can create a destination in it.",
        "schema_file": "open-api-definitions/groups/create_group.json",
        "method": "POST",
        "endpoint": "/v1/groups",
        "params": ["request_body"],
    },
    "group_details": {
        "description": "Returns a group object if a valid identifier was provided.",
        "schema_file": "open-api-definitions/groups/group_details.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id"],
    },
    "modify_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing group within your Fivetran account.",
        "schema_file": "open-api-definitions/groups/modify_group.json",
        "method": "PATCH",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id", "request_body"],
    },
    "delete_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a group from your Fivetran account.",
        "schema_file": "open-api-definitions/groups/delete_group.json",
        "method": "DELETE",
        "endpoint": "/v1/groups/{group_id}",
        "params": ["group_id"],
    },
    "list_all_connections_in_group": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of information about all connections within a group in your Fivetran account.",
        "schema_file": "open-api-definitions/groups/list_all_connections_in_group.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/connections",
        "params": ["group_id"],
        "query_params": ["schema", "cursor", "limit"],
    },
    "group_ssh_public_key": {
        "description": "Returns public key from SSH key pair associated with the group.",
        "schema_file": "open-api-definitions/groups/group_ssh_public_key.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/public-key",
        "params": ["group_id"],
    },
    "group_service_account": {
        "description": "Returns Fivetran service account associated with the group.",
        "schema_file": "open-api-definitions/groups/group_service_account.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/service-account",
        "params": ["group_id"],
    },
    "list_all_users_in_group": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of information about all users within a group in your Fivetran account.",
        "schema_file": "open-api-definitions/groups/list_all_users_in_group.json",
        "method": "GET",
        "endpoint": "/v1/groups/{group_id}/users",
        "params": ["group_id"],
        "query_params": ["cursor", "limit", "active"],
    },
    "add_user_to_group": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Adds an existing user to a group in your Fivetran account.",
        "schema_file": "open-api-definitions/groups/add_user_to_group.json",
        "method": "POST",
        "endpoint": "/v1/groups/{group_id}/users",
        "params": ["group_id", "request_body"],
    },
    "delete_user_from_group": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes an existing user from a group in your Fivetran account.",
        "schema_file": "open-api-definitions/groups/delete_user_from_group.json",
        "method": "DELETE",
        "endpoint": "/v1/groups/{group_id}/users/{user_id}",
        "params": ["group_id", "user_id"],
    },
    # ============================================================================
    # HVR
    # ============================================================================
    # "hvr_register_hub": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Register a new hub within your Fivetran account.",
    #     "schema_file": "open-api-definitions/hvr/hvr_register_hub.json",
    #     "method": "POST",
    #     "endpoint": "/v1/hvr/register-hub",
    #     "params": ["request_body"],
    # },
    # ============================================================================
    # HYBRID DEPLOYMENT AGENTS
    # ============================================================================
    "get_hybrid_deployment_agent_list": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns list of all Hybrid Deployment Agents within your Fivetran account, along with usage. Optionally filtered to a single group.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/get_hybrid_deployment_agent_list.json",
        "method": "GET",
        "endpoint": "/v1/hybrid-deployment-agents",
        "query_params": ["groupId", "cursor", "limit"],
    },
    "create_hybrid_deployment_agent": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new Hybrid Deployment Agent in a group.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/create_hybrid_deployment_agent.json",
        "method": "POST",
        "endpoint": "/v1/hybrid-deployment-agents",
        "params": ["request_body"],
    },
    "get_hybrid_deployment_agent": {
        "description": "Returns Hybrid Deployment Agent Details.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/get_hybrid_deployment_agent.json",
        "method": "GET",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}",
        "params": ["agent_id"],
    },
    "delete_hybrid_deployment_agent": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Delete a Hybrid Deployment Agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/delete_hybrid_deployment_agent.json",
        "method": "DELETE",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}",
        "params": ["agent_id"],
    },
    "re_auth_hybrid_deployment_agent": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Regenerate authentication for a Hybrid Deployment Agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/re_auth_hybrid_deployment_agent.json",
        "method": "PATCH",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}/re-auth",
        "params": ["agent_id", "request_body"],
    },
    "reset_hybrid_deployment_agent_credentials": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Reset credentials for a Hybrid Deployment Agent.",
        "schema_file": "open-api-definitions/hybrid-deployment-agents/reset_hybrid_deployment_agent_credentials.json",
        "method": "POST",
        "endpoint": "/v1/hybrid-deployment-agents/{agent_id}/reset-credentials",
        "params": ["agent_id", "request_body"],
    },
    # ============================================================================
    # METADATA
    # ============================================================================
    "metadata_connectors": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns all available source types within your Fivetran account. This endpoint makes it easier to display Fivetran connectors within your application because it provides metadata including the proper source name ('Facebook Ads' instead of 'facebook_ads'), the source icon, information about the Hybrid deployment support, and links to Fivetran resources. As we update source names and icons, that metadata will automatically update within this endpoint",
        "schema_file": "open-api-definitions/metadata/metadata_connectors.json",
        "method": "GET",
        "endpoint": "/v1/metadata/connector-types",
        "query_params": ["cursor", "limit"],
    },
    "metadata_connector_config": {
        "description": "Returns metadata of configuration parameters and authorization parameters for a specified connector type.",
        "schema_file": "open-api-definitions/metadata/metadata_connector_config.json",
        "method": "GET",
        "endpoint": "/v1/metadata/connector-types/{service}",
        "params": ["service"],
    },
    # ============================================================================
    # PRIVATE LINKS
    # ============================================================================
    # "get_private_links": {
    #     "description": "Returns a list of all private links.",
    #     "schema_file": "open-api-definitions/private-links/get_private_links.json",
    #     "method": "GET",
    #     "endpoint": "/v1/private-links",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_private_link": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new private link in your Fivetran account. > NOTE: See the [Set Up a Connection With Private Links tutorial](/docs/rest-api/tutorials/set-up-connection-with-private-links) to learn how to use this endpoint to set up a [database connection](/docs/connectors/databases) with [private networking](/docs/using-fivetran/features#privatenetworking).",
    #     "schema_file": "open-api-definitions/private-links/create_private_link.json",
    #     "method": "POST",
    #     "endpoint": "/v1/private-links",
    #     "params": ["request_body"],
    # },
    # "get_private_link_details": {
    #     "description": "Returns a private link object if a valid identifier was provided.",
    #     "schema_file": "open-api-definitions/private-links/get_private_link_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/private-links/{private_link_id}",
    #     "params": ["private_link_id"],
    # },
    # "modify_private_link": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing private link within your Fivetran account.",
    #     "schema_file": "open-api-definitions/private-links/modify_private_link.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/private-links/{private_link_id}",
    #     "params": ["private_link_id", "request_body"],
    # },
    # "delete_private_link": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a private link from your Fivetran account.",
    #     "schema_file": "open-api-definitions/private-links/delete_private_link.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/private-links/{private_link_id}",
    #     "params": ["private_link_id"],
    # },
    # ============================================================================
    # PROXY AGENTS
    # ============================================================================
    # ============================================================================
    # PUBLIC METADATA
    # ============================================================================
    # ============================================================================
    # ROLES
    # ============================================================================
    # # ============================================================================
    # # SYSTEM KEYS
    # # ============================================================================
    # "list_all_roles": {
    #     "description": "Returns a list of all predefined and custom roles within your Fivetran account.",
    #     "schema_file": "open-api-definitions/roles/list_all_roles.json",
    #     "method": "GET",
    #     "endpoint": "/v1/roles",
    #     "query_params": ["cursor", "limit"],
    # },
    # ============================================================================
    # TEAMS
    # ============================================================================
    # "list_all_teams": {
    #     "description": "Returns a list of all teams within your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/list_all_teams.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_team": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new team in your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/create_team.json",
    #     "method": "POST",
    #     "endpoint": "/v1/teams",
    #     "params": ["request_body"],
    # },
    # "team_details": {
    #     "description": "Returns information for a given team within your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/team_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}",
    #     "params": ["team_id"],
    # },
    # "modify_team": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing team within your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/modify_team.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/teams/{team_id}",
    #     "params": ["team_id", "request_body"],
    # },
    # "delete_team": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a team from your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/delete_team.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/teams/{team_id}",
    #     "params": ["team_id"],
    # },
    # "get_team_memberships_in_connections": {
    #     "description": "Returns all connections a team has membership in.",
    #     "schema_file": "open-api-definitions/teams/get_team_memberships_in_connections.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/connections",
    #     "params": ["team_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "add_team_membership_in_connection": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Adds a team as a member of a connection.",
    #     "schema_file": "open-api-definitions/teams/add_team_membership_in_connection.json",
    #     "method": "POST",
    #     "endpoint": "/v1/teams/{team_id}/connections",
    #     "params": ["team_id", "request_body"],
    # },
    # "get_team_membership_in_connection": {
    #     "description": "Returns details of a team membership in a connection.",
    #     "schema_file": "open-api-definitions/teams/get_team_membership_in_connection.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
    #     "params": ["team_id", "connection_id"],
    # },
    # "update_team_membership_in_connection": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates team membership in a connection",
    #     "schema_file": "open-api-definitions/teams/update_team_membership_in_connection.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
    #     "params": ["team_id", "connection_id", "request_body"],
    # },
    # "delete_team_membership_in_connection": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes team membership in a connection.",
    #     "schema_file": "open-api-definitions/teams/delete_team_membership_in_connection.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/teams/{team_id}/connections/{connection_id}",
    #     "params": ["team_id", "connection_id"],
    # },
    # "get_team_memberships_in_groups": {
    #     "description": "Returns all groups in which a team has membership.",
    #     "schema_file": "open-api-definitions/teams/get_team_memberships_in_groups.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/groups",
    #     "params": ["team_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "add_team_membership_in_group": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Adds a team as a member of a group.",
    #     "schema_file": "open-api-definitions/teams/add_team_membership_in_group.json",
    #     "method": "POST",
    #     "endpoint": "/v1/teams/{team_id}/groups",
    #     "params": ["team_id", "request_body"],
    # },
    # "get_team_membership_in_group": {
    #     "description": "Returns details of a team membership in a group.",
    #     "schema_file": "open-api-definitions/teams/get_team_membership_in_group.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
    #     "params": ["team_id", "group_id"],
    # },
    # "update_team_membership_in_group": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates team membership in a group.",
    #     "schema_file": "open-api-definitions/teams/update_team_membership_in_group.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
    #     "params": ["team_id", "group_id", "request_body"],
    # },
    # "delete_team_membership_in_group": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes a team's membership in a group.",
    #     "schema_file": "open-api-definitions/teams/delete_team_membership_in_group.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/teams/{team_id}/groups/{group_id}",
    #     "params": ["team_id", "group_id"],
    # },
    # "delete_team_membership_in_account": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes a team from your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/delete_team_membership_in_account.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/teams/{team_id}/role",
    #     "params": ["team_id"],
    # },
    # "list_users_in_team": {
    #     "description": "Returns a list of users and their roles within a team in your Fivetran account",
    #     "schema_file": "open-api-definitions/teams/list_users_in_team.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/users",
    #     "params": ["team_id"],
    #     "query_params": ["cursor", "limit", "active"],
    # },
    # "add_user_to_team": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Assigns a role for a user in a team.",
    #     "schema_file": "open-api-definitions/teams/add_user_to_team.json",
    #     "method": "POST",
    #     "endpoint": "/v1/teams/{team_id}/users",
    #     "params": ["team_id", "request_body"],
    # },
    # "get_user_in_team": {
    #     "description": "Returns the membership details for a user in a team.",
    #     "schema_file": "open-api-definitions/teams/get_user_in_team.json",
    #     "method": "GET",
    #     "endpoint": "/v1/teams/{team_id}/users/{user_id}",
    #     "params": ["team_id", "user_id"],
    # },
    # "update_user_membership": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates a user membership in a team.",
    #     "schema_file": "open-api-definitions/teams/update_user_membership.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/teams/{team_id}/users/{user_id}",
    #     "params": ["team_id", "user_id", "request_body"],
    # },
    # "delete_user_from_team": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes a user from a team.",
    #     "schema_file": "open-api-definitions/teams/delete_user_from_team.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/teams/{team_id}/users/{user_id}",
    #     "params": ["team_id", "user_id"],
    # },
    # ============================================================================
    # TRANSFORMATION PROJECTS
    # ============================================================================
    "list_all_transformation_projects": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all transformation projects available via API within your Fivetran account.",
        "schema_file": "open-api-definitions/transformation-projects/list_all_transformation_projects.json",
        "method": "GET",
        "endpoint": "/v1/transformation-projects",
        "query_params": ["cursor", "limit"],
    },
    "create_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/create_transformation_project.json",
        "method": "POST",
        "endpoint": "/v1/transformation-projects",
        "params": ["request_body"],
    },
    "transformation_project_details": {
        "description": "Returns transformation project details if a valid identifier was provided.",
        "schema_file": "open-api-definitions/transformation-projects/transformation_project_details.json",
        "method": "GET",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id"],
    },
    "modify_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates transformation project if a valid identifier was provided.",
        "schema_file": "open-api-definitions/transformation-projects/modify_transformation_project.json",
        "method": "PATCH",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id", "request_body"],
    },
    "delete_transformation_project": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes transformation project if a valid identifier was provided.",
        "schema_file": "open-api-definitions/transformation-projects/delete_transformation_project.json",
        "method": "DELETE",
        "endpoint": "/v1/transformation-projects/{project_id}",
        "params": ["project_id"],
    },
    "test_transformation_project": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Triggers tests for an existing transformation project.",
        "schema_file": "open-api-definitions/transformation-projects/test_transformation_project.json",
        "method": "POST",
        "endpoint": "/v1/transformation-projects/{project_id}/test",
        "params": ["project_id", "request_body"],
    },
    # ============================================================================
    # TRANSFORMATIONS
    # ============================================================================
    "transformations_list": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of all transformations within your Fivetran account.",
        "schema_file": "open-api-definitions/transformations/transformations_list.json",
        "method": "GET",
        "endpoint": "/v1/transformations",
        "query_params": ["cursor", "limit", "group_id", "project_id", "type"],
    },
    "create_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new transformation.",
        "schema_file": "open-api-definitions/transformations/create_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations",
        "params": ["request_body"],
    },
    "transformation_package_metadata_list": {
        "description": "⚠️ RESULTS ARE PAGINATED. Returns a list of available Quickstart transformation package metadata details.",
        "schema_file": "open-api-definitions/transformations/transformation_package_metadata_list.json",
        "method": "GET",
        "endpoint": "/v1/transformations/package-metadata",
        "query_params": ["service", "name", "cursor", "limit"],
    },
    "transformation_package_metadata_details": {
        "description": "Returns the metadata details of the Quickstart transformation package if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/transformation_package_metadata_details.json",
        "method": "GET",
        "endpoint": "/v1/transformations/package-metadata/{package_definition_id}",
        "params": ["package_definition_id"],
    },
    "transformation_details": {
        "description": "Returns a transformation details if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/transformation_details.json",
        "method": "GET",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id"],
    },
    "update_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the transformation if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/update_transformation.json",
        "method": "PATCH",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id", "request_body"],
    },
    "delete_transformation": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a transformation if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/delete_transformation.json",
        "method": "DELETE",
        "endpoint": "/v1/transformations/{transformation_id}",
        "params": ["transformation_id"],
    },
    "cancel_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Cancels the execution of the transformation if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/cancel_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/cancel",
        "params": ["transformation_id", "request_body"],
    },
    "run_transformation": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Runs the transformation if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/run_transformation.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/run",
        "params": ["transformation_id", "request_body"],
    },
    "upgrade_transformation_package": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Upgrades the Quickstart transformation package to latest version if a valid identifier is provided.",
        "schema_file": "open-api-definitions/transformations/upgrade_transformation_package.json",
        "method": "POST",
        "endpoint": "/v1/transformations/{transformation_id}/upgrade",
        "params": ["transformation_id", "request_body"],
    },
    # ============================================================================
    # USERS
    # ============================================================================

    # "list_all_users": {
    #     "description": "Returns a list of all users within your Fivetran account.",
    #     "schema_file": "open-api-definitions/users/list_all_users.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users",
    #     "query_params": ["cursor", "limit", "active"],
    # },
    # "create_user": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Invites a new user to your Fivetran account. The invited user will have access to the account only after accepting the invitation. Invited user details are still accessible through the API.",
    #     "schema_file": "open-api-definitions/users/create_user.json",
    #     "method": "POST",
    #     "endpoint": "/v1/users",
    #     "params": ["request_body"],
    # },
    # "delete_user": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a user from your Fivetran account. You will be unable to delete an account owner user if there is only one remaining.",
    #     "schema_file": "open-api-definitions/users/delete_user.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/users/{id}",
    #     "params": ["id"],
    # },
    # "user_details": {
    #     "description": "Returns a user object if a valid identifier was provided.",
    #     "schema_file": "open-api-definitions/users/user_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users/{user_id}",
    #     "params": ["user_id"],
    # },
    # "modify_user": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates information for an existing user within your Fivetran account.",
    #     "schema_file": "open-api-definitions/users/modify_user.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/users/{user_id}",
    #     "params": ["user_id", "request_body"],
    # },
    # "get_user_memberships_in_connections": {
    #     "description": "Returns all connection membership for a user within your Fivetran account.",
    #     "schema_file": "open-api-definitions/users/get_user_memberships_in_connections.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users/{user_id}/connections",
    #     "params": ["user_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "add_user_membership_in_connection": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Adds a connection membership",
    #     "schema_file": "open-api-definitions/users/add_user_membership_in_connection.json",
    #     "method": "POST",
    #     "endpoint": "/v1/users/{user_id}/connections",
    #     "params": ["user_id", "request_body"],
    # },
    # "get_user_membership_in_connections": {
    #     "description": "Returns the details of a user's membership in a connection.",
    #     "schema_file": "open-api-definitions/users/get_user_membership_in_connections.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
    #     "params": ["user_id", "connection_id"],
    # },
    # "update_user_membership_in_connection": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates user membership in a connection.",
    #     "schema_file": "open-api-definitions/users/update_user_membership_in_connection.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
    #     "params": ["user_id", "connection_id", "request_body"],
    # },
    # "delete_user_membership_in_connection": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes user membership in a connection.",
    #     "schema_file": "open-api-definitions/users/delete_user_membership_in_connection.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/users/{user_id}/connections/{connection_id}",
    #     "params": ["user_id", "connection_id"],
    # },
    # "get_user_memberships_in_groups": {
    #     "description": "Returns the membership details for all groups a user belongs to.",
    #     "schema_file": "open-api-definitions/users/get_user_memberships_in_groups.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users/{user_id}/groups",
    #     "params": ["user_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "add_user_membership_in_group": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Adds a user membership in a group.",
    #     "schema_file": "open-api-definitions/users/add_user_membership_in_group.json",
    #     "method": "POST",
    #     "endpoint": "/v1/users/{user_id}/groups",
    #     "params": ["user_id", "request_body"],
    # },
    # "get_user_membership_in_group": {
    #     "description": "Returns details of a user membership in group.",
    #     "schema_file": "open-api-definitions/users/get_user_membership_in_group.json",
    #     "method": "GET",
    #     "endpoint": "/v1/users/{user_id}/groups/{group_id}",
    #     "params": ["user_id", "group_id"],
    # },
    # "update_user_membership_in_group": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates user group membership.",
    #     "schema_file": "open-api-definitions/users/update_user_membership_in_group.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/users/{user_id}/groups/{group_id}",
    #     "params": ["user_id", "group_id", "request_body"],
    # },
    # "delete_user_membership_in_group": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes user from a group.",
    #     "schema_file": "open-api-definitions/users/delete_user_membership_in_group.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/users/{user_id}/groups/{group_id}",
    #     "params": ["user_id", "group_id"],
    # },
    # "delete_user_membership_in_account": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Removes a user's role from an account, but the user remains a member of the account.",
    #     "schema_file": "open-api-definitions/users/delete_user_membership_in_account.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/users/{user_id}/role",
    #     "params": ["user_id"],
    # },
    # ============================================================================
    # WEBHOOKS
    # ============================================================================
    "list_all_webhooks": {
        "description": "⚠️ RESULTS ARE PAGINATED. The endpoint allows you to retrieve the list of existing webhooks available for the current account",
        "schema_file": "open-api-definitions/webhooks/list_all_webhooks.json",
        "method": "GET",
        "endpoint": "/v1/webhooks",
        "query_params": ["cursor", "limit"],
    },
    "create_account_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. This endpoint allows you to create a new webhook for the current account.",
        "schema_file": "open-api-definitions/webhooks/create_account_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/account",
        "params": ["request_body"],
    },
    "create_group_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. This endpoint allows you to create a new webhook for a given group",
        "schema_file": "open-api-definitions/webhooks/create_group_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/group/{group_id}",
        "params": ["group_id", "request_body"],
    },
    "webhook_details": {
        "description": "This endpoint allows you to retrieve details of the existing webhook for a given identifier",
        "schema_file": "open-api-definitions/webhooks/webhook_details.json",
        "method": "GET",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id"],
    },
    "modify_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. The endpoint allows you to update the existing webhook with a given identifier",
        "schema_file": "open-api-definitions/webhooks/modify_webhook.json",
        "method": "PATCH",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id", "request_body"],
    },
    "delete_webhook": {
        "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. This endpoint allows you to delete an existing webhook with a given identifier",
        "schema_file": "open-api-definitions/webhooks/delete_webhook.json",
        "method": "DELETE",
        "endpoint": "/v1/webhooks/{webhook_id}",
        "params": ["webhook_id"],
    },
    "test_webhook": {
        "description": "⚠️ WRITE OPERATION - Confirm with user before calling. The endpoint allows you to test an existing webhook. It sends a webhook with a given identifier for a dummy connection with identifier _connection_1",
        "schema_file": "open-api-definitions/webhooks/test_webhook.json",
        "method": "POST",
        "endpoint": "/v1/webhooks/{webhook_id}/test",
        "params": ["webhook_id", "request_body"],
    },
    # ============================================================================
    # CONNECTOR SDK
    # ============================================================================
    # "list_connector_sdk_packages": {
    #     "description": "Returns a list of all Connector SDK packages in your Fivetran account.",
    #     "schema_file": "open-api-definitions/connector-sdk/list_connector_sdk_packages.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connector-sdk/packages",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_connector_sdk_package": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Uploads a new Connector SDK package to your Fivetran account. The package must be a ZIP file containing your custom connector code. You can create the package ZIP file using the [`fivetran package` command](/docs/connector-sdk/connector-development-and-configuration/connector-sdk-commands#fivetranpackage). After creating a package, use the standard [Create a Connection endpoint](/docs/rest-api/api-reference/connections/create-connection) with the returned `id` as `package_id` in the config. > NOTE: Each package can only be associated with one connection at a time.",
    #     "schema_file": "open-api-definitions/connector-sdk/create_connector_sdk_package.json",
    #     "method": "POST",
    #     "endpoint": "/v1/connector-sdk/packages",
    #     "params": ["request_body"],
    # },
    # "get_connector_sdk_package": {
    #     "description": "Returns details for a specific Connector SDK package.",
    #     "schema_file": "open-api-definitions/connector-sdk/get_connector_sdk_package.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connector-sdk/packages/{package_id}",
    #     "params": ["package_id"],
    # },
    # "update_connector_sdk_package": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates an existing Connector SDK package by uploading a new version of the connector code. All connections using this package will automatically use the updated code on their next sync.",
    #     "schema_file": "open-api-definitions/connector-sdk/update_connector_sdk_package.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/connector-sdk/packages/{package_id}",
    #     "params": ["package_id", "request_body"],
    # },
    # "delete_connector_sdk_package": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Permanently deletes a Connector SDK package from your Fivetran account. > **Warning:** Packages that are associated with a connection cannot be deleted. You must first delete the connection before deleting the package.",
    #     "schema_file": "open-api-definitions/connector-sdk/delete_connector_sdk_package.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/connector-sdk/packages/{package_id}",
    #     "params": ["package_id"],
    # },
    # "download_connector_sdk_package": {
    #     "description": "Downloads the connector code package file (code.zip) for a specific Connector SDK package. This endpoint returns the raw ZIP file as an octet-stream.",
    #     "schema_file": "open-api-definitions/connector-sdk/download_connector_sdk_package.json",
    #     "method": "GET",
    #     "endpoint": "/v1/connector-sdk/packages/{package_id}/download",
    #     "params": ["package_id"],
    # },
    # ============================================================================
    # EXTERNAL SECRETS MANAGERS
    # ============================================================================
    # "list_esms": {
    #     "description": "Returns a list of all External Secrets Manager instances within your Fivetran account.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/list_esms.json",
    #     "method": "GET",
    #     "endpoint": "/v1/external-secrets-managers",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_esm": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new External Secrets Manager instance within your Fivetran account.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/create_esm.json",
    #     "method": "POST",
    #     "endpoint": "/v1/external-secrets-managers",
    #     "params": ["request_body"],
    # },
    # "get_esm_details": {
    #     "description": "Returns the details of an existing External Secrets Manager instance.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/get_esm_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/external-secrets-managers/{esm_id}",
    #     "params": ["esm_id"],
    # },
    # "modify_esm": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the configuration of an existing External Secrets Manager instance.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/modify_esm.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/external-secrets-managers/{esm_id}",
    #     "params": ["esm_id", "request_body"],
    # },
    # "delete_esm": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes an External Secrets Manager instance from your Fivetran account. The instance must not be in use by any source connections or destinations.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/delete_esm.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/external-secrets-managers/{esm_id}",
    #     "params": ["esm_id"],
    # },
    # "get_esm_entities": {
    #     "description": "Returns a list of source connections and destinations that are using a specific External Secrets Manager.",
    #     "schema_file": "open-api-definitions/external-secrets-managers/get_esm_entities.json",
    #     "method": "GET",
    #     "endpoint": "/v1/external-secrets-managers/{esm_id}/entities",
    #     "params": ["esm_id"],
    #     "query_params": ["type"],
    # },
    # ============================================================================
    # EXTERNAL SECRETS MANAGERS ENTITIES
    # ============================================================================

    # "list_esm_entities": {
    #     "description": "Returns a list of all source connections and destinations that are using any External Secrets Manager within your Fivetran account.",
    #     "schema_file": "open-api-definitions/external-secrets-managers-entities/list_esm_entities.json",
    #     "method": "GET",
    #     "endpoint": "/v1/external-secrets-managers-entities",
    #     "query_params": ["esm_id", "type"],
    # },
    # ============================================================================
    # CERTIFICATES
    # ============================================================================
    # "approve_certificate": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Approves a certificate for a connection/destination, so Fivetran trusts this certificate for a source/destination database. The connection/destination setup tests will fail if a non-approved certificate is provided.",
    #     "schema_file": "open-api-definitions/certificates/approve_certificate.json",
    #     "method": "POST",
    #     "endpoint": "/v1/certificates",
    #     "params": ["request_body"],
    # },
    # ============================================================================
    # PROXY
    # ============================================================================
    # "get_proxy_agent": {
    #     "description": "Returns a list of all proxy agents within your Fivetran account.",
    #     "schema_file": "open-api-definitions/proxy/get_proxy_agent.json",
    #     "method": "GET",
    #     "endpoint": "/v1/proxy",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_proxy_agent": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new proxy agent within your Fivetran account.",
    #     "schema_file": "open-api-definitions/proxy/create_proxy_agent.json",
    #     "method": "POST",
    #     "endpoint": "/v1/proxy",
    #     "params": ["request_body"],
    # },
    # "get_proxy_agent_details": {
    #     "description": "Retrieves the details of the specified proxy agent.",
    #     "schema_file": "open-api-definitions/proxy/get_proxy_agent_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/proxy/{agent_id}",
    #     "params": ["agent_id"],
    # },
    # "delete_proxy_agent": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes the specified proxy agent from your Fivetran account.",
    #     "schema_file": "open-api-definitions/proxy/delete_proxy_agent.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/proxy/{agent_id}",
    #     "params": ["agent_id"],
    # },
    # "get_proxy_agent_connections": {
    #     "description": "Returns all connections attached to the specified proxy agent within your Fivetran account.",
    #     "schema_file": "open-api-definitions/proxy/get_proxy_agent_connections.json",
    #     "method": "GET",
    #     "endpoint": "/v1/proxy/{agent_id}/connections",
    #     "params": ["agent_id"],
    #     "query_params": ["cursor", "limit"],
    # },
    # "regenerate_secrets_proxy_agent": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Regenerate secrets for proxy agent within your Fivetran account.",
    #     "schema_file": "open-api-definitions/proxy/regenerate_secrets_proxy_agent.json",
    #     "method": "POST",
    #     "endpoint": "/v1/proxy/{agent_id}/regenerate-secrets",
    #     "params": ["agent_id", "request_body"],
    # },
    # ============================================================================
    # PUBLIC
    # ============================================================================
    "metadata_public_connectors": {
        "description": "Returns all available source types. This endpoint provides metadata including the proper source name (‘Facebook Ads’ instead of facebook_ads), the source icon, feature tables, information about the Hybrid deployment support, information about the Authorization via API support, and links to Fivetran resources. As we update source names and icons, that metadata will automatically update within this endpoint.",
        "schema_file": "open-api-definitions/public/metadata_public_connectors.json",
        "method": "GET",
        "endpoint": "/public/connector-types",
    },
    # ============================================================================
    # SYSTEM KEYS
    # ============================================================================
    # "get_system_keys": {
    #     "description": "Returns a list of system keys within your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/get_system_keys.json",
    #     "method": "GET",
    #     "endpoint": "/v1/system-keys",
    #     "query_params": ["cursor", "limit"],
    # },
    # "create_system_key": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Creates a new system key with your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/create_system_key.json",
    #     "method": "POST",
    #     "endpoint": "/v1/system-keys",
    #     "params": ["request_body"],
    # },
    # "get_system_key_details": {
    #     "description": "Retrieves a system key object within your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/get_system_key_details.json",
    #     "method": "GET",
    #     "endpoint": "/v1/system-keys/{key_id}",
    #     "params": ["key_id"],
    # },
    # "update_system_key": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates an existing system key within your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/update_system_key.json",
    #     "method": "PATCH",
    #     "endpoint": "/v1/system-keys/{key_id}",
    #     "params": ["key_id", "request_body"],
    # },
    # "delete_system_key": {
    #     "description": "⚠️ DESTRUCTIVE - Confirm with user before calling. Deletes a system key from your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/delete_system_key.json",
    #     "method": "DELETE",
    #     "endpoint": "/v1/system-keys/{key_id}",
    #     "params": ["key_id"],
    # },
    # "rotate_system_key": {
    #     "description": "⚠️ WRITE OPERATION - Confirm with user before calling. Updates the secret value and expired_at date for an existing system key within your Fivetran account.",
    #     "schema_file": "open-api-definitions/system-keys/rotate_system_key.json",
    #     "method": "POST",
    #     "endpoint": "/v1/system-keys/{key_id}/rotate",
    #     "params": ["key_id", "request_body"],
    # },
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
    
    "cursor": {"type": "string", "description": "Paging cursor id."},
    "limit": {"type": "integer", "description": "Number of records to return"},
    "schema": {"type": "string", "description": "Filter on schema."},

    "active": {"type": "boolean", "description": "Filter on active."},
    "esm_id": {"type": "string", "description": "The unique identifier for the External Secrets Manager."},
    "groupId": {"type": "string", "description": "The unique identifier for the group."},
    "name": {"type": "string", "description": "The package name."},
    "type": {"type": "string", "description": "Filter on type."},

    "table": {"type": "string", "description": "The table name from the connection schema."},  # needs audit
    "package_id": {"type": "string", "description": "The unique identifier for the Connector SDK package."},  # needs audit
    "id": {"type": "string", "description": "The unique identifier for the user within the account."},  # needs audit
}

def build_tool_schema(tool_name: str, tool_config: dict) -> Tool:
    """Build the MCP Tool definition (the input schema the agent sees) for one tool.

    Three kinds of input are advertised:
      - schema_file : always required (the mandatory read-then-confirm gate)
      - params      : required path params + request_body (from tool_config["params"])
      - query_params: OPTIONAL query-string params (from tool_config["query_params"])
    """
    properties = {
        "schema_file": {
            "type": "string",
            "description": f"REQUIRED: You must first read the schema file at '{tool_config['schema_file']}', then provide this exact path here to confirm.",
        },
    }

    required = ["schema_file"]

    # Required params: path params (connection_id, schema_name, ...) and request_body.
    # All mandatory. The .get() fallback means a param missing from PARAM_DEFINITIONS
    # still gets a valid definition instead of becoming a required-but-undefined field.
    for param in tool_config.get("params", []):
        properties[param] = PARAM_DEFINITIONS.get(
            param, {"type": "string", "description": param}
        ).copy()
        required.append(param)

    # Optional query params: added to properties so the agent MAY send them,
    # but deliberately NOT added to `required`.
    for param in tool_config.get("query_params", []):
        properties[param] = PARAM_DEFINITIONS.get(
            param, {"type": "string", "description": param}
        ).copy()

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
    """Execute the API call after schema validation.

    Splits the incoming arguments into three kinds of input:
      - path params : declared in tool_config["params"] (minus request_body);
                      fill the {connection_id}, {schema_name}, ... placeholders in the URL
      - query params: declared in tool_config["query_params"]; sent as the URL
                      query string, but ONLY when the agent actually supplied them
      - request body: the "request_body" argument, a JSON string, for POST/PATCH calls
    """
    tool_config = TOOLS[name]
    method = tool_config["method"]
    endpoint_template = tool_config["endpoint"]

    # --- Path parameters -----------------------------------------------------
    # Everything in "params" except request_body is a path param. Pull only the
    # declared ones out of arguments, then substitute them into the endpoint URL.
    path_param_names = [p for p in tool_config.get("params", []) if p != "request_body"]
    path_params = {k: arguments[k] for k in path_param_names if k in arguments}
    endpoint = endpoint_template.format(**path_params)

    # --- Query parameters ----------------------------------------------------
    # Optional. Include only the ones the agent provided (and that aren't None),
    # so omitted params never get appended to the query string.
    query_param_names = tool_config.get("query_params", [])
    query_params = {
        k: arguments[k]
        for k in query_param_names
        if k in arguments and arguments[k] is not None
    }

    # --- Request body --------------------------------------------------------
    # Write operations send the body as a JSON string; parse it into a dict.
    json_body = None
    if "request_body" in arguments:
        try:
            json_body = json.loads(arguments["request_body"])
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in request_body: {e}")

    # --- Fire the request ----------------------------------------------------
    # Pass params only when non-empty so omitted query params leave the URL clean.
    return await fivetran_request(
        method,
        endpoint,
        params=query_params or None,
        json_body=json_body,
    )


async def async_main():
    """Run the MCP server."""
    if not FIVETRAN_API_KEY or not FIVETRAN_API_SECRET:
        raise ValueError(
            "FIVETRAN_API_KEY and FIVETRAN_API_SECRET environment variables must be set. "
            "Configure them in your .mcp.json or .env file."
        )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    import asyncio
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
