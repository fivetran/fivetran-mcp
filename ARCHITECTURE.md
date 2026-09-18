# Architecture

fivetran-mcp is a two-stage pipeline: a build-time splitter that converts
Fivetran's OpenAPI spec into a manifest, and a runtime server that generates
MCP tools from that manifest.

```
fivetran-open-api-definition.json
              │
              ▼
   split_openapi_by_endpoint.py         (build)
              │
              ▼
   open-api-definitions/
     ├── <resource>/<operation_id>.json      (one per endpoint)
     ├── _service-configs/{connectors,destinations}/{svc}.json
     ├── endpoints.json                       (manifest: {tools, endpoints})
     └── AVAILABLE_ACTIONS.md                 (human reference)
              │
              ▼
      server.py                          (runtime)
              │
              ▼
      MCP tools over stdio
```

## Build stage — `split_openapi_by_endpoint.py`

**Why split?** The full Fivetran OpenAPI spec is ~3MB. Loading it (or even
listing every endpoint's schema) into an MCP session would eat a large share
of the agent's context window before it does any work. Splitting into
per-endpoint files lets the server load just a tiny manifest at startup and
fetch individual schemas on demand via `get_schema` — the agent only pays
context cost for the endpoints it actually inspects.

Reads the full OpenAPI spec and produces one JSON file per endpoint plus a
manifest. During the split:

- `$ref`s are resolved inline
- Examples, tags, security, and non-JSON metadata are stripped
- Descriptions on write/delete methods get a ⚠️ warning prefix
- Large informational enums on response `service` fields are dropped
- `EXCLUDED_ENDPOINTS` (currently `create_system_key`, `rotate_system_key`)
  are skipped at build time — they never enter the manifest, so no runtime
  scope config can accidentally expose them. Session-outliving credentials
  aren't safe to expose to any agent.

Per-service configs (`_service-configs/connectors/<svc>.json`) are
pre-assembled at build time from `<service>_NewConnectorRequestV1` allOf refs.
Walking those refs at runtime would be complex and slow, and the results
only change when the spec does — so precompute once and splice into
`get_schema` responses on demand.

Rerun after any OpenAPI spec change:

```bash
python split_openapi_by_endpoint.py fivetran-open-api-definition.json open-api-definitions
```

The script snapshots the previous manifest before regenerating and prints a
diff — added, removed, and modified endpoints — so spec drift is visible.

## Runtime stage — `server.py`

`_load_manifest()` reads `endpoints.json` at import; nothing walks
per-endpoint files during startup. The manifest gives:

- `endpoints`: one row per endpoint with `name`, `resource`, `scope`,
  `method`, `path`, `schema_file`
- `tools`: one row per `(resource, action)` pair with ≥1 non-deprecated
  endpoint

### Grant model

Effective grants = (`FIVETRAN_SCOPE` × all resources) − `DISALLOWED_ACTIONS`.

- `FIVETRAN_SCOPE ∈ {read, read/write, read/write/delete}` grants positively
  (`read/write` implies `read`).
- `DISALLOWED_ACTIONS` (comma-separated `resource:action` tokens) denies, and
  each token cascades to higher actions on the same resource (denying `read`
  also denies `write` and `delete`).

**Why a denylist instead of an allowlist?** Scope tiers plus exceptions are
easier to reason about than enumerating every allowed `(resource, action)`
pair. Users pick a broad ceiling and carve holes in it, rather than assembling
permissions from scratch.

**Why does denying a lower action cascade to higher ones?** "You can't
observe it, so you can't touch it either." If an agent can't `read` a
resource, letting it `write` or `delete` blind is asking for trouble —
the write would succeed against something the agent (and often the user)
can't inspect first.

Only `(resource, action)` pairs in `ALLOWED_GRANTS` produce a tool. That's
what the client sees in `list_tools`.

### Tool surface

Every session exposes:

- `list_endpoints(category?, search?, include_deprecated?)` — discovery
- `get_schema(name, service?)` — full schema for one endpoint
- One `<resource>_<action>` tool per allowed pair — execution

**Why grouped tools instead of one per endpoint?** Fivetran has ~167
endpoints. Exposing each as its own MCP tool would balloon the tool list,
slow client startup, and make the surface hard for an agent to scan. Grouping
into ~30 `<resource>_<action>` tools keeps the top-level discoverable while
`list_endpoints` and `get_schema` handle the drill-down.

The tool name is a boundary: `connections_read(name="delete_connection")`
returns an `ENDPOINT_TOOL_MISMATCH` error instead of executing. Without this
check the tool namespace would be decorative.

### Credentials

Fivetran credentials are resolved per tool invocation by a pluggable resolver.
`server.py` defines:

- `Credentials(authorization: str)` — opaque, carries the full `Authorization`
  header value. Basic for API key/secret today; Bearer once Fivetran's OAuth
  lands.
- `CredentialsResolver = Callable[[], Awaitable[Credentials]]` — a zero-arg
  async factory. Transport-specific inputs (env vars, request headers, OAuth
  tokens) are closed over when the resolver is registered.
- `set_credentials_resolver(resolver)` — called once by the entrypoint for the
  active transport. `async_main` (stdio) registers `env_resolver`.

Three resolvers ship in `server.py`:

- `env_resolver` — reads `FIVETRAN_API_KEY` / `FIVETRAN_API_SECRET` and builds
  `Basic <b64(key:secret)>`. Used by the stdio entrypoint.
- `header_resolver` — forwards the incoming HTTP `Authorization` header as-is,
  reading from `mcp_server.request_context.request.headers`. For self-hosted
  HTTP deployments where the caller already has valid Fivetran credentials.
- `oauth_resolver` — reserved for marketplace hosting. Body is
  `NotImplementedError` until the Fivetran OAuth broker is available.

`list_endpoints` and `get_schema` read the local manifest and don't require
credentials. `CredentialsError` only surfaces when an API-hitting tool is
invoked, so clients can browse the tool surface before auth is wired.

### Error contract

Caller-correctable errors return a shaped JSON dict rather than raising:

- `GRANT_NOT_ALLOWED` — endpoint requires an action outside the current scope
  or explicitly disallowed
- `ENDPOINT_TOOL_MISMATCH` — endpoint doesn't belong to the calling tool's
  `(resource, action)` group

Path-param and body validation still raise `ValueError` today (falls through
to the generic error handler) — see "Room to improve" below.

## Room to improve

- Unify caller-correctable errors under one shape (unknown endpoint name,
  missing path params, invalid JSON body all still raise generic
  `ValueError`s)
- Reuse a single `httpx.AsyncClient` across requests rather than opening one
  per call
