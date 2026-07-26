# Fivetran MCP Server

An MCP server that you can use to interact with your Fivetran environment. It allows you to ask read-only questions like "when was the last time my postgres connection completed a sync?" and "are any of my connections broken?" Set `FIVETRAN_SCOPE` to `read/write` or `read/write/delete` to unlock write and delete operations, and use `DISALLOWED_ACTIONS` to carve exceptions out of that tier (for example, `system-keys:write,system-keys:delete` to keep credential minting off-limits). The MCP will confirm with you before performing a write or delete operation.

## Plugins

We have plugins that use this MCP server to make complicated tasks easier, compatible with Claude Code and Codex. Each plugin lives in its own repository with its own README.

- **[copy-connections](https://github.com/fivetran/copy-connections)** — Copy existing Fivetran connections to a new destination.  Keep their configs and schemas intact or modify them as you like.

## Regenerating API Schema Files

The `open-api-definitions/` directory contains lightweight per-endpoint schema files used by the server. To regenerate them from an updated OpenAPI spec:

```bash
python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
```

This will replace the existing schema files with freshly generated ones.

## Setup

### 1. Choose how to run the server

You have two options. Most users should use **uvx** — no clone required.

#### Option A: Run with uvx (recommended)

Requires [uv](https://docs.astral.sh/uv/) (which provides `uvx`) and Python 3.10+. uvx fetches and runs the server directly from this repository, so there is nothing to install or update manually.

The command your MCP client will run is:

```bash
uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

> Note: bare `uvx fivetran-mcp` (without `--from`) does not work — the `fivetran-mcp` and `mcp-fivetran` names on PyPI are owned by unrelated projects, so you must install from the git URL.

#### Option B: Run from a local clone (for development)

Use this if you want to modify `server.py` or regenerate schema files.

```bash
git clone https://github.com/fivetran/fivetran-mcp
cd fivetran-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

You can then point your MCP client at `python /path/to/fivetran-mcp/server.py`.

### 2. Get Fivetran API credentials

You can generate credentials within https://fivetran.com/dashboard/user/api-config

### 3. Connect to your AI client

Choose your preferred AI client below and follow the configuration instructions.

#### Claude Desktop

1. Open Claude Desktop and go to **Settings** → **Developer** → **Edit Config**
2. This opens `claude_desktop_config.json`. Add the Fivetran MCP server:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Using uvx (Option A):

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/fivetran/fivetran-mcp", "fivetran-mcp"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_SCOPE": "read",
        "DISALLOWED_ACTIONS": "system-keys:write,system-keys:delete"
      }
    }
  }
}
```

Using a local clone (Option B):

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "python",
      "args": ["/path/to/fivetran-mcp/server.py"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_SCOPE": "read",
        "DISALLOWED_ACTIONS": "system-keys:write,system-keys:delete"
      }
    }
  }
}
```

3. Save the file and restart Claude Desktop
4. Look for the MCP server indicator in the bottom-right corner of the chat input

---

#### Claude Code (CLI)

Use the `claude mcp add` command to register the server.

Using uvx (Option A):

```bash
claude mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:write,system-keys:delete \
  -- uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

Using a local clone (Option B):

```bash
claude mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:write,system-keys:delete \
  -- python /path/to/fivetran-mcp/server.py
```

Or add it directly to your `~/.claude.json` configuration:

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/fivetran/fivetran-mcp", "fivetran-mcp"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_SCOPE": "read",
        "DISALLOWED_ACTIONS": "system-keys:write,system-keys:delete"
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

Using uvx (Option A):

```bash
codex mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:write,system-keys:delete \
  -- uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

Using a local clone (Option B):

```bash
codex mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:write,system-keys:delete \
  -- python /path/to/fivetran-mcp/server.py
```

**Option 2: Edit config.toml**

Add the following to `~/.codex/config.toml`. Using uvx (Option A):

```toml
[mcp_servers.fivetran]
command = "uvx"
args = ["--from", "git+https://github.com/fivetran/fivetran-mcp", "fivetran-mcp"]

[mcp_servers.fivetran.env]
FIVETRAN_API_KEY = "your-api-key"
FIVETRAN_API_SECRET = "your-api-secret"
FIVETRAN_SCOPE = "read"
DISALLOWED_ACTIONS = "system-keys:write,system-keys:delete"
```

Using a local clone (Option B):

```toml
[mcp_servers.fivetran]
command = "python"
args = ["/path/to/fivetran-mcp/server.py"]

[mcp_servers.fivetran.env]
FIVETRAN_API_KEY = "your-api-key"
FIVETRAN_API_SECRET = "your-api-secret"
FIVETRAN_SCOPE = "read"
DISALLOWED_ACTIONS = "system-keys:write,system-keys:delete"
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

Add the following to your chosen configuration file.

Using uvx (Option A):

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/fivetran/fivetran-mcp", "fivetran-mcp"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_SCOPE": "read",
        "DISALLOWED_ACTIONS": "system-keys:write,system-keys:delete"
      }
    }
  }
}
```

Using a local clone (Option B):

```json
{
  "mcpServers": {
    "fivetran": {
      "command": "python",
      "args": ["/path/to/fivetran-mcp/server.py"],
      "env": {
        "FIVETRAN_API_KEY": "your-api-key",
        "FIVETRAN_API_SECRET": "your-api-secret",
        "FIVETRAN_SCOPE": "read",
        "DISALLOWED_ACTIONS": "system-keys:write,system-keys:delete"
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
| `FIVETRAN_SCOPE` | No | `read` | One of `read`, `read/write`, `read/write/delete`. Case-insensitive. Sets the ceiling of what the server can do. |
| `DISALLOWED_ACTIONS` | No | (empty) | Comma-separated list of `resource:action` tokens (e.g. `system-keys:write,connections:delete`) to deny inside the current scope. Case-insensitive. Each token cascades to higher actions on the same resource — denying `read` also denies `write` and `delete`; denying `write` also denies `delete`. |

## Example Questions

- "What connections are failing?"
- "When did the Salesforce connection last sync?"
- "Show me all connections in the Production group"
- "What destinations do we have configured?"
