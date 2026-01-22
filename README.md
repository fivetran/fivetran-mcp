# Fivetran MCP Server

An MCP server that you can use to interact with your Fivetran environment.  It allows you to ask read-only questions like "when was the last time my postgres connection completed a sync?" and "are any of my connection's broken?"  Additionally, if you set FIVETRAN_ALLOW_WRITES to "true" you can complete write operations like "update the sync frequency of my Redshift connections to every 3 hours"

## Setup

### 1. Install dependencies

Requires Python 3.10+

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Get Fivetran API credentials

You can generate credentials within https://fivetran.com/account/settings

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
        "FIVETRAN_APIKEY": "your-api-key",
        "FIVETRAN_APISECRET": "your-api-secret",
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
  --env FIVETRAN_APIKEY=your-api-key \
  --env FIVETRAN_APISECRET=your-api-secret \
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
        "FIVETRAN_APIKEY": "your-api-key",
        "FIVETRAN_APISECRET": "your-api-secret",
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
  --env FIVETRAN_APIKEY=your-api-key \
  --env FIVETRAN_APISECRET=your-api-secret \
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
FIVETRAN_APIKEY = "your-api-key"
FIVETRAN_APISECRET = "your-api-secret"
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
        "FIVETRAN_APIKEY": "your-api-key",
        "FIVETRAN_APISECRET": "your-api-secret",
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
| `FIVETRAN_APIKEY` | Yes | - | Your Fivetran API key |
| `FIVETRAN_APISECRET` | Yes | - | Your Fivetran API secret |
| `FIVETRAN_ALLOW_WRITES` | No | `false` | Set to `true` to enable POST, PATCH, and DELETE operations |

## Available Tools

### Account

| Tool | Method | Description |
|------|--------|-------------|
| `get_account_info` | GET | Get account information associated with the API key |

### Certificates (Deprecated)

| Tool | Method | Description |
|------|--------|-------------|
| `approve_certificate` | POST | (Deprecated) Approve a certificate for the account |

### Connections

| Tool | Method | Description |
|------|--------|-------------|
| `list_connections` | GET | List all connections in your account |
| `create_connection` | POST | Create a new connection |
| `get_connection_details` | GET | Get connection status, last sync time, and config |
| `modify_connection` | PATCH | Update an existing connection |
| `delete_connection` | DELETE | Delete a connection |
| `get_connection_state` | GET | Get detailed sync state |
| `modify_connection_state` | PATCH | Update the sync state of a connection |
| `sync_connection` | POST | Trigger a data sync for a connection |
| `resync_connection` | POST | Trigger a historical re-sync for a connection |
| `resync_tables` | POST | Re-sync specific tables in a connection |
| `run_connection_setup_tests` | POST | Run setup tests for a connection |
| `create_connect_card` | POST | Create a connect card token for a connection |
| `get_connection_schema_config` | GET | Get schema/table sync configuration |
| `reload_connection_schema_config` | POST | Reload schema configuration from the source |
| `modify_connection_schema_config` | PATCH | Update schema configuration for a connection |
| `modify_connection_database_schema_config` | PATCH | Update configuration for a specific database schema |
| `get_connection_column_config` | GET | Get column configuration for a specific table |
| `modify_connection_table_config` | PATCH | Update configuration for a specific table |
| `modify_connection_column_config` | PATCH | Update configuration for a specific column |
| `delete_connection_column_config` | DELETE | Drop a blocked column from the destination |
| `delete_multiple_columns_connection_config` | POST | Drop multiple blocked columns from the destination |
| `list_connection_certificates` | GET | List certificates approved for a connection |
| `approve_connection_certificate` | POST | Approve a certificate for a connection |
| `get_connection_certificate_details` | GET | Get details of a specific certificate |
| `revoke_connection_certificate` | DELETE | Revoke a certificate for a connection |
| `list_connection_fingerprints` | GET | List fingerprints approved for a connection |
| `approve_connection_fingerprint` | POST | Approve a fingerprint for a connection |
| `get_connection_fingerprint_details` | GET | Get details of a specific fingerprint |
| `revoke_connection_fingerprint` | DELETE | Revoke a fingerprint for a connection |

### Destinations

| Tool | Method | Description |
|------|--------|-------------|
| `list_destinations` | GET | List all data warehouse destinations |
| `create_destination` | POST | Create a new destination |
| `get_destination_details` | GET | Get destination configuration |
| `modify_destination` | PATCH | Update an existing destination |
| `delete_destination` | DELETE | Delete a destination |
| `run_destination_setup_tests` | POST | Run setup tests for a destination |
| `list_destination_certificates` | GET | List certificates approved for a destination |
| `approve_destination_certificate` | POST | Approve a certificate for a destination |
| `get_destination_certificate_details` | GET | Get details of a specific certificate |
| `revoke_destination_certificate` | DELETE | Revoke a certificate for a destination |
| `list_destination_fingerprints` | GET | List fingerprints approved for a destination |
| `approve_destination_fingerprint` | POST | Approve a fingerprint for a destination |
| `get_destination_fingerprint_details` | GET | Get details of a specific fingerprint |
| `revoke_destination_fingerprint` | DELETE | Revoke a fingerprint for a destination |

### External Logging

| Tool | Method | Description |
|------|--------|-------------|
| `list_log_services` | GET | List all log services in your account |
| `create_log_service` | POST | Create a new log service |
| `get_log_service_details` | GET | Get details of a specific log service |
| `update_log_service` | PATCH | Update a log service |
| `delete_log_service` | DELETE | Delete a log service |
| `run_log_service_setup_tests` | POST | Run setup tests for a log service |

### Groups

| Tool | Method | Description |
|------|--------|-------------|
| `list_groups` | GET | List all groups |
| `create_group` | POST | Create a new group |
| `get_group_details` | GET | Get group information |
| `modify_group` | PATCH | Update a group |
| `delete_group` | DELETE | Delete a group |
| `list_connections_in_group` | GET | List connections within a specific group |
| `list_users_in_group` | GET | List all users in a group |
| `add_user_to_group` | POST | Add a user to a group |
| `delete_user_from_group` | DELETE | Remove a user from a group |
| `get_group_ssh_public_key` | GET | Get the SSH public key for a group |
| `get_group_service_account` | GET | Get the service account for a group |

### HVR

| Tool | Method | Description |
|------|--------|-------------|
| `hvr_register_hub` | POST | Register an HVR hub |

### Hybrid Deployment Agents

| Tool | Method | Description |
|------|--------|-------------|
| `list_hybrid_deployment_agents` | GET | List all hybrid deployment agents |
| `create_hybrid_deployment_agent` | POST | Create a new hybrid deployment agent |
| `get_hybrid_deployment_agent` | GET | Get details of a hybrid deployment agent |
| `re_auth_hybrid_deployment_agent` | PATCH | Regenerate authentication keys |
| `reset_hybrid_deployment_agent_credentials` | POST | Reset credentials for an agent |
| `delete_hybrid_deployment_agent` | DELETE | Delete a hybrid deployment agent |

### Metadata

| Tool | Method | Description |
|------|--------|-------------|
| `list_metadata_connectors` | GET | List all available connector types |
| `get_metadata_connector_config` | GET | Get configuration metadata for a connector type |

### Private Links

| Tool | Method | Description |
|------|--------|-------------|
| `list_private_links` | GET | List all private links |
| `create_private_link` | POST | Create a new private link |
| `get_private_link_details` | GET | Get details of a private link |
| `modify_private_link` | PATCH | Update a private link |
| `delete_private_link` | DELETE | Delete a private link |

### Proxy Agents

| Tool | Method | Description |
|------|--------|-------------|
| `list_proxy_agents` | GET | List all proxy agents |
| `create_proxy_agent` | POST | Create a new proxy agent |
| `get_proxy_agent_details` | GET | Get details of a proxy agent |
| `delete_proxy_agent` | DELETE | Delete a proxy agent |
| `list_proxy_agent_connections` | GET | List connections attached to a proxy agent |
| `regenerate_proxy_agent_secrets` | POST | Regenerate secrets for a proxy agent |

### Public Metadata

| Tool | Method | Description |
|------|--------|-------------|
| `list_public_connectors` | GET | List available connector types (no auth required) |

### Roles

| Tool | Method | Description |
|------|--------|-------------|
| `list_roles` | GET | List all available roles |

### System Keys

| Tool | Method | Description |
|------|--------|-------------|
| `list_system_keys` | GET | List all system keys |
| `create_system_key` | POST | Create a new system key |
| `get_system_key_details` | GET | Get details of a system key |
| `update_system_key` | PATCH | Update a system key |
| `delete_system_key` | DELETE | Delete a system key |
| `rotate_system_key` | POST | Rotate a system key |

### Teams

| Tool | Method | Description |
|------|--------|-------------|
| `list_teams` | GET | List all teams |
| `create_team` | POST | Create a new team |
| `get_team_details` | GET | Get details of a team |
| `modify_team` | PATCH | Update a team |
| `delete_team` | DELETE | Delete a team |
| `delete_team_membership_in_account` | DELETE | Delete a team's account-level role |
| `list_users_in_team` | GET | List all users in a team |
| `add_user_to_team` | POST | Add a user to a team |
| `get_user_in_team` | GET | Get a user's membership in a team |
| `update_user_membership_in_team` | PATCH | Update a user's membership in a team |
| `delete_user_from_team` | DELETE | Remove a user from a team |
| `list_team_memberships_in_groups` | GET | List a team's group memberships |
| `add_team_membership_in_group` | POST | Add a team to a group |
| `get_team_membership_in_group` | GET | Get a team's membership in a group |
| `update_team_membership_in_group` | PATCH | Update a team's membership in a group |
| `delete_team_membership_in_group` | DELETE | Remove a team from a group |
| `list_team_memberships_in_connections` | GET | List a team's connection memberships |
| `add_team_membership_in_connection` | POST | Add a team to a connection |
| `get_team_membership_in_connection` | GET | Get a team's membership in a connection |
| `update_team_membership_in_connection` | PATCH | Update a team's membership in a connection |
| `delete_team_membership_in_connection` | DELETE | Remove a team from a connection |

### Transformation Projects

| Tool | Method | Description |
|------|--------|-------------|
| `list_transformation_projects` | GET | List all transformation projects |
| `create_transformation_project` | POST | Create a new transformation project |
| `get_transformation_project_details` | GET | Get details of a transformation project |
| `modify_transformation_project` | PATCH | Update a transformation project |
| `delete_transformation_project` | DELETE | Delete a transformation project |
| `test_transformation_project` | POST | Test a transformation project |

### Transformations

| Tool | Method | Description |
|------|--------|-------------|
| `list_transformations` | GET | List all transformations |
| `create_transformation` | POST | Create a new transformation |
| `get_transformation_details` | GET | Get details of a transformation |
| `update_transformation` | PATCH | Update a transformation |
| `delete_transformation` | DELETE | Delete a transformation |
| `run_transformation` | POST | Run a transformation |
| `cancel_transformation` | POST | Cancel a running transformation |
| `upgrade_transformation_package` | POST | Upgrade a transformation's package version |
| `list_transformation_package_metadata` | GET | List all quickstart package metadata |
| `get_transformation_package_metadata_details` | GET | Get details of a quickstart package |

### Users

| Tool | Method | Description |
|------|--------|-------------|
| `list_users` | GET | List all users in the account |
| `create_user` | POST | Create a new user |
| `get_user_details` | GET | Get details of a user |
| `modify_user` | PATCH | Update a user |
| `delete_user` | DELETE | Delete a user |
| `delete_user_membership_in_account` | DELETE | Delete a user's account-level role |
| `list_user_memberships_in_groups` | GET | List a user's group memberships |
| `add_user_membership_in_group` | POST | Add a user to a group with a role |
| `get_user_membership_in_group` | GET | Get a user's membership in a group |
| `update_user_membership_in_group` | PATCH | Update a user's membership in a group |
| `delete_user_membership_in_group` | DELETE | Remove a user from a group |
| `list_user_memberships_in_connections` | GET | List a user's connection memberships |
| `add_user_membership_in_connection` | POST | Add a user to a connection with a role |
| `get_user_membership_in_connection` | GET | Get a user's membership in a connection |
| `update_user_membership_in_connection` | PATCH | Update a user's membership in a connection |
| `delete_user_membership_in_connection` | DELETE | Remove a user from a connection |

### Webhooks

| Tool | Method | Description |
|------|--------|-------------|
| `list_webhooks` | GET | List all webhooks in the account |
| `create_account_webhook` | POST | Create a webhook at the account level |
| `create_group_webhook` | POST | Create a webhook for a specific group |
| `get_webhook_details` | GET | Get details of a webhook |
| `modify_webhook` | PATCH | Update a webhook |
| `delete_webhook` | DELETE | Delete a webhook |
| `test_webhook` | POST | Test a webhook by sending a test event |

## Example Questions

- "What connections are failing?"
- "When did the Salesforce connection last sync?"
- "Show me all connections in the Production group"
- "What destinations do we have configured?"
