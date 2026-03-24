# Fivetran MCP Server

An MCP server that you can use to interact with your Fivetran environment.  It allows you to ask read-only questions like "when was the last time my postgres connection completed a sync?" and "are any of my connection's broken?"  Additionally, if you set FIVETRAN_ALLOW_WRITES to "true" you can complete write operations like "update the sync frequency of my Redshift connections to every 3 hours".  The MCP will confirm with you before performing a write operation.

## Enabling Additional Tools

Not all tools are enabled by default. To keep context usage manageable for AI clients with smaller context windows, tools for managing users, teams, roles, system keys, private links, proxy agents, HVR, and certificates/fingerprints are commented out in `server.py`. The tables in the [Available Tools](#available-tools) section below indicate which tools are enabled by default.

To enable additional tools, open `server.py` and uncomment the relevant tool definitions in the `TOOLS` dictionary. You can enable as many or as few as you need.

## Regenerating API Schema Files

The `open-api-definitions/` directory contains lightweight per-endpoint schema files used by the server. To regenerate them from an updated OpenAPI spec:

```bash
python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
```

This will replace the existing schema files with freshly generated ones.

## Setup

### 1. Install dependencies

Requires Python 3.10+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

### 2. Get Fivetran API credentials

You can generate credentials within https://fivetran.com/dashboard/user/api-config

### 3. Configure your MCP client

See `.mcp.example.json` for an example configuration. Update the paths and credentials in that file to match your setup and save it as .mcp.json in the primary folder.

### 4. Connect to your AI client

Choose your preferred AI client below and follow the configuration instructions.

#### Claude Desktop

1. Open Claude Desktop and go to **Settings** → **Developer** → **Edit Config**
2. This opens `claude_desktop_config.json`. Add the Fivetran MCP server:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "python",
      "args": ["/path/to/fivetran-mcp-server/server.py"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_ALLOW_WRITES": "false"
      }
    }
  }
}
```

3. Save the file and restart Claude Desktop
4. Look for the MCP server indicator in the bottom-right corner of the chat input

---

#### Claude Code (CLI)

Use the `claude mcp add` command to register the server:

```bash
claude mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_ALLOW_WRITES=false \
  -- python /path/to/fivetran-mcp-server/server.py
```

Or add it directly to your `~/.claude.json` configuration:

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "python",
      "args": ["/path/to/fivetran-mcp-server/server.py"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_ALLOW_WRITES": "false"
      }
    }
  }
}
```

Verify the server is configured:

```bash
claude mcp list
```

---

#### OpenAI Codex

Codex stores MCP configuration in `~/.codex/config.toml`. You can configure via CLI or by editing the file directly.

**Option 1: CLI**

```bash
codex mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_ALLOW_WRITES=false \
  -- python /path/to/fivetran-mcp-server/server.py
```

**Option 2: Edit config.toml**

Add the following to `~/.codex/config.toml`:

```toml
[mcp_servers.fivetran]
command = "python"
args = ["/path/to/fivetran-mcp-server/server.py"]

[mcp_servers.fivetran.env]
FIVETRAN_API_KEY = "your-api-key"
FIVETRAN_API_SECRET = "your-api-secret"
FIVETRAN_ALLOW_WRITES = "false"
```

Verify configuration:

```bash
codex mcp list
```

---

#### Cursor

Cursor supports both global and project-level MCP configurations.

**Global Configuration:** `~/.cursor/mcp.json`  
**Project Configuration:** `.cursor/mcp.json` (in your project root)

Add the following to your chosen configuration file:

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "python",
      "args": ["/path/to/fivetran-mcp-server/server.py"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_ALLOW_WRITES": "false"
      }
    }
  }
}
```

**Alternative:** Use Cursor's UI
1. Open Cursor and press `Cmd/Ctrl + Shift + P`
2. Search for "MCP" and select **View: Open MCP Settings**
3. Click **Tools & Integrations** → **MCP Tools** → **Add Custom MCP**
4. Add the configuration above

Restart Cursor to load the new MCP server configuration.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FIVETRAN_API_KEY` | Yes | - | Your Fivetran API key |
| `FIVETRAN_API_SECRET` | Yes | - | Your Fivetran API secret |
| `FIVETRAN_ALLOW_WRITES` | No | `false` | Set to `true` to enable POST, PATCH, and DELETE operations |

## Available Tools

### Account

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `get_account_info` | GET | Get account information associated with the API key | Yes |

### Certificates (Deprecated)

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `approve_certificate` | POST | (Deprecated) Approve a certificate for the account | No |

### Connections

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_connections` | GET | List all connections in your account | Yes |
| `create_connection` | POST | Create a new connection | Yes |
| `get_connection_details` | GET | Get connection status, last sync time, and config | Yes |
| `modify_connection` | PATCH | Update an existing connection | Yes |
| `delete_connection` | DELETE | Delete a connection | Yes |
| `get_connection_state` | GET | Get detailed sync state | Yes |
| `modify_connection_state` | PATCH | Update the sync state of a connection | Yes |
| `sync_connection` | POST | Trigger a data sync for a connection | Yes |
| `resync_connection` | POST | Trigger a historical re-sync for a connection | Yes |
| `resync_tables` | POST | Re-sync specific tables in a connection | Yes |
| `run_connection_setup_tests` | POST | Run setup tests for a connection | Yes |
| `create_connect_card` | POST | Create a connect card token for a connection | Yes |
| `get_connection_schema_config` | GET | Get schema/table sync configuration | Yes |
| `reload_connection_schema_config` | POST | Reload schema configuration from the source | Yes |
| `modify_connection_schema_config` | PATCH | Update schema configuration for a connection | Yes |
| `modify_connection_database_schema_config` | PATCH | Update configuration for a specific database schema | Yes |
| `get_connection_column_config` | GET | Get column configuration for a specific table | Yes |
| `modify_connection_table_config` | PATCH | Update configuration for a specific table | Yes |
| `modify_connection_column_config` | PATCH | Update configuration for a specific column | Yes |
| `delete_connection_column_config` | DELETE | Drop a blocked column from the destination | Yes |
| `delete_multiple_columns_connection_config` | POST | Drop multiple blocked columns from the destination | Yes |
| `list_connection_certificates` | GET | List certificates approved for a connection | No |
| `approve_connection_certificate` | POST | Approve a certificate for a connection | No |
| `get_connection_certificate_details` | GET | Get details of a specific certificate | No |
| `revoke_connection_certificate` | DELETE | Revoke a certificate for a connection | No |
| `list_connection_fingerprints` | GET | List fingerprints approved for a connection | No |
| `approve_connection_fingerprint` | POST | Approve a fingerprint for a connection | No |
| `get_connection_fingerprint_details` | GET | Get details of a specific fingerprint | No |
| `revoke_connection_fingerprint` | DELETE | Revoke a fingerprint for a connection | No |

### Destinations

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_destinations` | GET | List all data warehouse destinations | Yes |
| `create_destination` | POST | Create a new destination | Yes |
| `get_destination_details` | GET | Get destination configuration | Yes |
| `modify_destination` | PATCH | Update an existing destination | Yes |
| `delete_destination` | DELETE | Delete a destination | Yes |
| `run_destination_setup_tests` | POST | Run setup tests for a destination | Yes |
| `list_destination_certificates` | GET | List certificates approved for a destination | No |
| `approve_destination_certificate` | POST | Approve a certificate for a destination | No |
| `get_destination_certificate_details` | GET | Get details of a specific certificate | No |
| `revoke_destination_certificate` | DELETE | Revoke a certificate for a destination | No |
| `list_destination_fingerprints` | GET | List fingerprints approved for a destination | No |
| `approve_destination_fingerprint` | POST | Approve a fingerprint for a destination | No |
| `get_destination_fingerprint_details` | GET | Get details of a specific fingerprint | No |
| `revoke_destination_fingerprint` | DELETE | Revoke a fingerprint for a destination | No |

### External Logging

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_log_services` | GET | List all log services in your account | Yes |
| `create_log_service` | POST | Create a new log service | Yes |
| `get_log_service_details` | GET | Get details of a specific log service | Yes |
| `update_log_service` | PATCH | Update a log service | Yes |
| `delete_log_service` | DELETE | Delete a log service | Yes |
| `run_log_service_setup_tests` | POST | Run setup tests for a log service | Yes |

### Groups

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_groups` | GET | List all groups | Yes |
| `create_group` | POST | Create a new group | Yes |
| `get_group_details` | GET | Get group information | Yes |
| `modify_group` | PATCH | Update a group | Yes |
| `delete_group` | DELETE | Delete a group | Yes |
| `list_connections_in_group` | GET | List connections within a specific group | Yes |
| `list_users_in_group` | GET | List all users in a group | Yes |
| `add_user_to_group` | POST | Add a user to a group | Yes |
| `delete_user_from_group` | DELETE | Remove a user from a group | Yes |
| `get_group_ssh_public_key` | GET | Get the SSH public key for a group | Yes |
| `get_group_service_account` | GET | Get the service account for a group | Yes |

### HVR

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `hvr_register_hub` | POST | Register an HVR hub | No |

### Hybrid Deployment Agents

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_hybrid_deployment_agents` | GET | List all hybrid deployment agents | Yes |
| `create_hybrid_deployment_agent` | POST | Create a new hybrid deployment agent | Yes |
| `get_hybrid_deployment_agent` | GET | Get details of a hybrid deployment agent | Yes |
| `re_auth_hybrid_deployment_agent` | PATCH | Regenerate authentication keys | Yes |
| `reset_hybrid_deployment_agent_credentials` | POST | Reset credentials for an agent | Yes |
| `delete_hybrid_deployment_agent` | DELETE | Delete a hybrid deployment agent | Yes |

### Metadata

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_metadata_connectors` | GET | List all available connector types | Yes |
| `get_metadata_connector_config` | GET | Get configuration metadata for a connector type | Yes |

### Private Links

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_private_links` | GET | List all private links | No |
| `create_private_link` | POST | Create a new private link | No |
| `get_private_link_details` | GET | Get details of a private link | No |
| `modify_private_link` | PATCH | Update a private link | No |
| `delete_private_link` | DELETE | Delete a private link | No |

### Proxy Agents

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_proxy_agents` | GET | List all proxy agents | No |
| `create_proxy_agent` | POST | Create a new proxy agent | No |
| `get_proxy_agent_details` | GET | Get details of a proxy agent | No |
| `delete_proxy_agent` | DELETE | Delete a proxy agent | No |
| `list_proxy_agent_connections` | GET | List connections attached to a proxy agent | No |
| `regenerate_proxy_agent_secrets` | POST | Regenerate secrets for a proxy agent | No |

### Public Metadata

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_public_connectors` | GET | List available connector types (no auth required) | Yes |

### Roles

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_roles` | GET | List all available roles | No |

### System Keys

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_system_keys` | GET | List all system keys | No |
| `create_system_key` | POST | Create a new system key | No |
| `get_system_key_details` | GET | Get details of a system key | No |
| `update_system_key` | PATCH | Update a system key | No |
| `delete_system_key` | DELETE | Delete a system key | No |
| `rotate_system_key` | POST | Rotate a system key | No |

### Teams

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_teams` | GET | List all teams | No |
| `create_team` | POST | Create a new team | No |
| `get_team_details` | GET | Get details of a team | No |
| `modify_team` | PATCH | Update a team | No |
| `delete_team` | DELETE | Delete a team | No |
| `delete_team_membership_in_account` | DELETE | Delete a team's account-level role | No |
| `list_users_in_team` | GET | List all users in a team | No |
| `add_user_to_team` | POST | Add a user to a team | No |
| `get_user_in_team` | GET | Get a user's membership in a team | No |
| `update_user_membership_in_team` | PATCH | Update a user's membership in a team | No |
| `delete_user_from_team` | DELETE | Remove a user from a team | No |
| `list_team_memberships_in_groups` | GET | List a team's group memberships | No |
| `add_team_membership_in_group` | POST | Add a team to a group | No |
| `get_team_membership_in_group` | GET | Get a team's membership in a group | No |
| `update_team_membership_in_group` | PATCH | Update a team's membership in a group | No |
| `delete_team_membership_in_group` | DELETE | Remove a team from a group | No |
| `list_team_memberships_in_connections` | GET | List a team's connection memberships | No |
| `add_team_membership_in_connection` | POST | Add a team to a connection | No |
| `get_team_membership_in_connection` | GET | Get a team's membership in a connection | No |
| `update_team_membership_in_connection` | PATCH | Update a team's membership in a connection | No |
| `delete_team_membership_in_connection` | DELETE | Remove a team from a connection | No |

### Transformation Projects

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_transformation_projects` | GET | List all transformation projects | Yes |
| `create_transformation_project` | POST | Create a new transformation project | Yes |
| `get_transformation_project_details` | GET | Get details of a transformation project | Yes |
| `modify_transformation_project` | PATCH | Update a transformation project | Yes |
| `delete_transformation_project` | DELETE | Delete a transformation project | Yes |
| `test_transformation_project` | POST | Test a transformation project | Yes |

### Transformations

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_transformations` | GET | List all transformations | Yes |
| `create_transformation` | POST | Create a new transformation | Yes |
| `get_transformation_details` | GET | Get details of a transformation | Yes |
| `update_transformation` | PATCH | Update a transformation | Yes |
| `delete_transformation` | DELETE | Delete a transformation | Yes |
| `run_transformation` | POST | Run a transformation | Yes |
| `cancel_transformation` | POST | Cancel a running transformation | Yes |
| `upgrade_transformation_package` | POST | Upgrade a transformation's package version | Yes |
| `list_transformation_package_metadata` | GET | List all quickstart package metadata | Yes |
| `get_transformation_package_metadata_details` | GET | Get details of a quickstart package | Yes |

### Users

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_users` | GET | List all users in the account | No |
| `create_user` | POST | Create a new user | No |
| `get_user_details` | GET | Get details of a user | No |
| `modify_user` | PATCH | Update a user | No |
| `delete_user` | DELETE | Delete a user | No |
| `delete_user_membership_in_account` | DELETE | Delete a user's account-level role | No |
| `list_user_memberships_in_groups` | GET | List a user's group memberships | No |
| `add_user_membership_in_group` | POST | Add a user to a group with a role | No |
| `get_user_membership_in_group` | GET | Get a user's membership in a group | No |
| `update_user_membership_in_group` | PATCH | Update a user's membership in a group | No |
| `delete_user_membership_in_group` | DELETE | Remove a user from a group | No |
| `list_user_memberships_in_connections` | GET | List a user's connection memberships | No |
| `add_user_membership_in_connection` | POST | Add a user to a connection with a role | No |
| `get_user_membership_in_connection` | GET | Get a user's membership in a connection | No |
| `update_user_membership_in_connection` | PATCH | Update a user's membership in a connection | No |
| `delete_user_membership_in_connection` | DELETE | Remove a user from a connection | No |

### Webhooks

| Tool | Method | Description | Default |
|------|--------|-------------|---------|
| `list_webhooks` | GET | List all webhooks in the account | Yes |
| `create_account_webhook` | POST | Create a webhook at the account level | Yes |
| `create_group_webhook` | POST | Create a webhook for a specific group | Yes |
| `get_webhook_details` | GET | Get details of a webhook | Yes |
| `modify_webhook` | PATCH | Update a webhook | Yes |
| `delete_webhook` | DELETE | Delete a webhook | Yes |
| `test_webhook` | POST | Test a webhook by sending a test event | Yes |

## Example Questions

- "What connections are failing?"
- "When did the Salesforce connection last sync?"
- "Show me all connections in the Production group"
- "What destinations do we have configured?"
