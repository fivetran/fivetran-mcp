# Fivetran MCP Server

> **Upgrading from version 0.3?** Four things worth knowing:
> - **Credential endpoints are governed by `DISALLOWED_ACTIONS`.** The manifest carries every endpoint in the Fivetran API, so system-key and user-API-key operations are reachable within whatever `FIVETRAN_SCOPE` you grant. Set the [recommended denylist](#recommended-denylist-for-credential-endpoints) to keep them out of reach; the server warns at startup while any remain callable. `system-keys:write` and `system-keys:delete` are valid `DISALLOWED_ACTIONS` tokens.
> - **`get_schema(service=X)` used to drop required destination-schema fields.** Fixed in 0.3.2 — per-service connector configs were silently missing the shared `schema_format_*` refs, which carry the only unconditional `required` field in the whole config. If connector creation through `get_schema`/`connections_write` ever failed or came back incomplete, that's now fixed.
> - **The server can now run as a hosted HTTP server**, via `--transport streamable-http` (or `MCP_TRANSPORT`), instead of only stdio. See [Running over HTTP (advanced)](#running-over-http-advanced).
> - **OAuth resource-server support is infrastructure scaffolding only, not yet functional.** Real token verification isn't implemented yet, so streamable-http mode today only works in its interim header-forwarding form (leave `FIVETRAN_AUTH_ISSUER` unset).

An MCP server that you can use to interact with your Fivetran environment. It allows you to ask read-only questions like "when was the last time my postgres connection completed a sync?" and "are any of my connections broken?" Set `FIVETRAN_SCOPE` to `read/write` or `read/write/delete` to unlock write and delete operations, and use `DISALLOWED_ACTIONS` to carve exceptions out of that tier (for example, `system-keys:write` to deny both write and delete operations on system keys). Write and delete operations are marked with advisory instructions telling the client model to confirm with you before execution; the server does not enforce confirmation.

## Using the tools

The server exposes two discovery tools plus one execution tool for each allowed resource/action pair:

- `list_endpoints` discovers API endpoints. Call it without arguments for category counts, with `category` to list a resource such as `connections`, or with `search` to find endpoints by name, summary, or path.
- `get_schema` returns the parameters, request body, and response schema for an endpoint. For connection and destination create/modify endpoints, pass `service` (for example, `postgres`) to include the service-specific configuration fields.
- `<resource>_<action>` tools execute endpoints in that group. Examples include `connections_read`, `connections_write`, and `destinations_delete`. Pass the endpoint `name` plus any `path_params`, `query`, or `body` values required by its schema.

The generated execution tools are filtered by `FIVETRAN_SCOPE` and `DISALLOWED_ACTIONS`, so clients only see operations allowed by the server configuration. A typical workflow is:

1. Discover an endpoint with `list_endpoints(category="connections")` or `list_endpoints(search="sync")`.
2. Inspect it with `get_schema(name="sync_connection")`.
3. Execute it with the matching tool, for example `connections_write(name="sync_connection", path_params={"connectionId": "..."})`.

Write and delete tool descriptions and endpoint summaries contain advisory confirmation warnings. Whether confirmation occurs depends on the client model following those instructions; the server does not enforce it.

## Plugins

We have plugins that use this MCP server to make complicated tasks easier, compatible with Claude Code and Codex. Each plugin lives in its own repository with its own README.

- **[copy-connections](https://github.com/fivetran/copy-connections)**. Copy existing Fivetran connections to a new destination. Keep their configs and schemas intact or modify them as you like.

## Regenerating API Schema Files

The `open-api-definitions/` directory contains lightweight per-endpoint schema files used by the server. To regenerate them from an updated OpenAPI spec:

```bash
python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
```

This will replace the existing schema files with freshly generated ones.

## Setup

### 1. Choose how to run the server

You have two options. Most users should use **uvx**. No clone required.

#### Option A: Run with uvx (recommended)

Requires [uv](https://docs.astral.sh/uv/) (which provides `uvx`) and Python 3.10+. uvx fetches and runs the server directly from this repository, so there is nothing to install or update manually.

The command your MCP client will run is:

```bash
uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

> Note: bare `uvx fivetran-mcp` (without `--from`) does not work. The `fivetran-mcp` and `mcp-fivetran` names on PyPI are owned by unrelated projects, so you must install from the git URL.

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

### 3. Prepare your environment variables

Before configuring any client, decide on the values you will pass to the server. Every client config below expects the same set of variables, so figure them out once here and reuse them.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FIVETRAN_API_KEY` | Yes | - | Your Fivetran API key (from step 2) |
| `FIVETRAN_API_SECRET` | Yes | - | Your Fivetran API secret (from step 2) |
| `FIVETRAN_SCOPE` | No | `read` | One of `read`, `read/write`, `read/write/delete`. Case-insensitive. Sets the ceiling of what the server can do. |
| `DISALLOWED_ACTIONS` | No | (empty) | Comma-separated list of tokens to deny inside the current scope. Case-insensitive. Two forms:<br>• `resource:action` (e.g. `system-keys:write`) denies that pair and cascades to higher actions on the same resource — denying `read` also denies `write` and `delete`; denying `write` also denies `delete`.<br>• `resource:action:endpoint_name` (e.g. `connections:write:sync_connection`) denies one specific endpoint. No cascade. The endpoint must belong to that exact `resource:action` pair or startup fails.<br>See [`open-api-definitions/AVAILABLE_ACTIONS.md`](./open-api-definitions/AVAILABLE_ACTIONS.md) for the full list of valid `resource:action` tokens. |
| `FIVETRAN_ALLOW_WRITES` | No | `false` | Backwards-compatibility flag from earlier releases. `true` is equivalent to `FIVETRAN_SCOPE=read/write`. Prefer `FIVETRAN_SCOPE` for new configs. If both are set, `FIVETRAN_SCOPE` wins and this is ignored. |

The server marks write and delete operations with advisory confirmation warnings, but does not enforce confirmation.

#### Recommended denylist for credential endpoints

Some endpoints read, mint, or rotate API keys that outlive the session, and `DISALLOWED_ACTIONS` is the only thing keeping them from an agent:

```
system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys
```

### 4. Connect to your AI client

Choose your preferred AI client below and follow the configuration instructions. Each snippet uses the environment variables you prepared in step 3. Plug in the values you settled on.

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
        "DISALLOWED_ACTIONS": "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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
        "DISALLOWED_ACTIONS": "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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
  --env DISALLOWED_ACTIONS=system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys \
  -- uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

Using a local clone (Option B):

```bash
claude mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys \
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
        "DISALLOWED_ACTIONS": "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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

Codex stores MCP configuration in `~/.codex/config.toml` (global) or `.codex/config.toml` inside a project (project-scoped). You can configure via CLI, edit the global file directly, or — if you cloned this repo — start from the shipped example.

**Option 0: Use the shipped example (fastest, for a local clone)**

```bash
cp .codex/config.example.toml .codex/config.toml
# then open .codex/config.toml and fill in your API key/secret
```

Codex only loads project-local config for **trusted** projects. On first use, run `codex` inside the repo directory and accept the trust prompt (or run `codex trust`). Without this step, `.codex/config.toml` is silently ignored.

**Option 1: CLI**

Using uvx (Option A):

```bash
codex mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys \
  -- uvx --from git+https://github.com/fivetran/fivetran-mcp fivetran-mcp
```

Using a local clone (Option B):

```bash
codex mcp add fivetran \
  --env FIVETRAN_API_KEY=your-api-key \
  --env FIVETRAN_API_SECRET=your-api-secret \
  --env FIVETRAN_SCOPE=read \
  --env DISALLOWED_ACTIONS=system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys \
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
DISALLOWED_ACTIONS = "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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
DISALLOWED_ACTIONS = "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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
        "DISALLOWED_ACTIONS": "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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
        "DISALLOWED_ACTIONS": "system-keys:read,users:read:get_user_api_key,users:read:list_api_keys,users:write:create_user_api_key,users:write:rotate_user_api_key,users:delete:delete_user_api_keys"
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

---

### Running over HTTP (advanced)

Most users should stick with stdio (above). If you'd rather point your AI
client at a URL than run the server per-client over stdio — for example so
several clients on a network can reach one running instance — run it with
`--transport streamable-http` (or `MCP_TRANSPORT=streamable-http`):

```bash
fivetran-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

(Works the same way for both install options in step 1 — `fivetran-mcp` is the console script both `uvx` and `pip install .` provide.)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_TRANSPORT` | No | `stdio` | `stdio` or `streamable-http`. Same as `--transport`. |
| `MCP_HOST` / `--host` | No | `127.0.0.1` | Bind host, streamable-http only. |
| `MCP_PORT` / `--port` | No | `8000` | Bind port, streamable-http only. |
| `MCP_ALLOWED_ORIGINS` | No | (empty) | Comma-separated list of allowed `Origin` header values. |
| `MCP_ALLOWED_HOSTS` | No | (empty) | Comma-separated list of allowed `Host` header values. |

In streamable-http mode, credentials come from each request's incoming
`Authorization` header rather than `FIVETRAN_API_KEY`/`FIVETRAN_API_SECRET` —
your AI client sends the same API key/secret pair from step 3 as a Basic
auth header instead of the server reading it from the environment. Do not
set `FIVETRAN_API_KEY`/`FIVETRAN_API_SECRET` for an HTTP deployment; the
server refuses to start if it finds them, since a shared key baked into a
multi-tenant process would apply one operator's credentials to every
caller. If neither `MCP_ALLOWED_ORIGINS` nor `MCP_ALLOWED_HOSTS` is set, the
server runs without DNS-rebinding protection and logs a startup warning.

`FIVETRAN_SCOPE`, `DISALLOWED_ACTIONS`, and `FIVETRAN_ALLOW_WRITES` from
step 3 work identically in streamable-http mode: the same defaults apply
(read-only unless you raise `FIVETRAN_SCOPE`), and the same
`DISALLOWED_ACTIONS` grammar carves exceptions out of whatever scope you
choose. Set them the same way you would for stdio. See `ARCHITECTURE.md`
for how the HTTP transport is wired, including OAuth resource-server mode.

---

## Example Questions

- "What connections are failing?"
- "When did the Salesforce connection last sync?"
- "Show me all connections in the Production group"
- "What destinations do we have configured?"
