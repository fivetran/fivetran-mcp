# Fivetran MCP Server

A read-only MCP server for querying Fivetran connections, destinations, and groups.

## Setup

### 1. Install dependencies

Requires Python 3.10+

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Get Fivetran API credentials

1. Go to https://fivetran.com/account/settings
2. Navigate to **API Config**
3. Copy your API Key and API Secret

### 3. Configure your MCP client

#### Claude Code

```bash
claude mcp add fivetran \
  -e FIVETRAN_APIKEY=your-api-key \
  -e FIVETRAN_APISECRET=your-api-secret \
  -- /path/to/.venv/bin/python3 /path/to/server.py
```

#### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["/path/to/server.py"],
      "env": {
        "FIVETRAN_APIKEY": "your-api-key",
        "FIVETRAN_APISECRET": "your-api-secret"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `list_connections` | List all connections in your account |
| `get_connection_details` | Get connection status, last sync time, and config |
| `get_connection_state` | Get detailed sync state |
| `get_connection_schema_config` | Get schema/table sync configuration |
| `list_destinations` | List all data warehouse destinations |
| `get_destination_details` | Get destination configuration |
| `list_groups` | List all groups |
| `get_group_details` | Get group information |
| `list_connections_in_group` | List connections within a specific group |

## Example Questions

- "What connections are failing?"
- "When did the Salesforce connection last sync?"
- "Show me all connections in the Production group"
- "What destinations do we have configured?"
