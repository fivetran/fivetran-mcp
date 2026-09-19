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

### Description overrides

`endpoint_overrides.json` (next to the splitter) is a flat map of
`operationId → prepend text`. On the next split, that text lands in the
endpoint's `description` between the ⚠️ category prefix and the OpenAPI
description:

```
⚠️ WRITE OPERATION - Confirm with user before calling. <override text> <spec description>
```

Use it for agent-facing hints that don't belong in the upstream spec —
"prefer using X together with Y", rate-limit notes, dataset caveats. Keys
starting with `_` are ignored so the file can carry an inline `_comment`.
Malformed entries (non-string or empty values) are skipped with a warning
and the endpoint falls through to the spec description. The manifest's
short `summary` is untouched — overrides only surface when an agent drills
into an endpoint via `get_schema`.

## Runtime stage — `server.py`

`_load_manifest()` reads `endpoints.json` at import; nothing walks
per-endpoint files during startup. The manifest gives:

- `endpoints`: one row per endpoint with `name`, `resource`, `scope`,
  `method`, `path`, `schema_file`
- `tools`: one row per `(resource, action)` pair with ≥1 non-deprecated
  endpoint

### Grant model

`configure(scope_actions, pair_denies, endpoint_denies, mode)` runs from the
entrypoint (`async_main` for stdio) after env parsing, and populates
`ALLOWED_GRANTS`, `ENDPOINT_DENIES`, `MODE`, `GENERATED_TOOLS`, and
`TOOLS_BY_NAME`. Grants are process-static but built after argv is available,
so the future HTTP entrypoint can pass its own scope and denies without touching
env vars.

Effective grants = (`FIVETRAN_SCOPE` × all resources) − pair denies − endpoint denies.

- `FIVETRAN_SCOPE ∈ {read, read/write, read/write/delete}` grants positively
  (`read/write` implies `read`).
- `DISALLOWED_ACTIONS` accepts two token forms:
  - `resource:action` denies that pair and cascades to higher actions on the
    same resource (denying `read` also denies `write` and `delete`).
  - `resource:action:endpoint_name` denies one specific endpoint. No cascade.
    The endpoint must belong to that exact `resource:action` pair or startup
    fails loudly.

**Why a denylist instead of an allowlist?** Scope tiers plus exceptions are
easier to reason about than enumerating every allowed `(resource, action)`
pair. Users pick a broad ceiling and carve holes in it, rather than assembling
permissions from scratch.

**Why does denying a lower action cascade to higher ones?** "You can't
observe it, so you can't touch it either." If an agent can't `read` a
resource, letting it `write` or `delete` blind is asking for trouble —
the write would succeed against something the agent (and often the user)
can't inspect first.

Only `(resource, action)` pairs in `ALLOWED_GRANTS` produce a tool. Endpoint
denies do not drop tools — a tool remains generated even when every endpoint
under it is denied, so the agent can still see the resource:action category and
report the situation. Per-endpoint availability surfaces on `list_endpoints`
rows as `callable: bool` and is enforced at call time by `do_call`.

### Tool surface

Every session exposes:

- `list_endpoints(category?, search?, include_deprecated?)` — discovery. Always
  lists every non-deprecated endpoint regardless of grants or denies. Each row
  carries `callable: bool` (true iff `(resource, scope)` is in `ALLOWED_GRANTS`
  and the name isn't in `ENDPOINT_DENIES`). The no-argument summary returns
  `categories` (total per resource) and `callable_counts` (currently callable
  per resource).
- `get_schema(name, service?)` — full schema for one endpoint, callable or not.
- One `<resource>_<action>` tool per allowed pair — execution.

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
  or is explicitly disallowed. Structured fields (`cause`, `required_grant`,
  `scope`, `disallowed`, `endpoint_disallowed`) let the agent branch
  programmatically; the human-facing "use the Fivetran dashboard or REST API"
  redirect lives once in the server instructions rather than being repeated
  per response.
- `ENDPOINT_TOOL_MISMATCH` — endpoint doesn't belong to the calling tool's
  `(resource, action)` group.

Path-param and body validation still raise `ValueError` today (falls through
to the generic error handler) — see "Room to improve" below.

## Room to improve

- Unify caller-correctable errors under one shape (unknown endpoint name,
  missing path params, invalid JSON body all still raise generic
  `ValueError`s)
- Reuse a single `httpx.AsyncClient` across requests rather than opening one
  per call
