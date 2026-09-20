# Architecture

fivetran-mcp is a two-stage pipeline: a build-time splitter that converts
Fivetran's OpenAPI spec into a manifest, and a runtime server that generates
MCP tools from that manifest and serves them over stdio or streamable-http.

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
        ┌─────┴─────┐
        ▼           ▼
   MCP tools    MCP tools
   over stdio   over streamable-http
```

## Repo map

- `server.py` — the runtime MCP server: manifest loading, grant/scope model,
  tool generation, request handling, both transport entrypoints.
- `auth.py` — OAuth 2.1 resource-server plumbing for streamable-http mode.
  Imported only when `FIVETRAN_AUTH_ISSUER` is set.
- `split_openapi_by_endpoint.py` — build-time script that turns the full
  OpenAPI spec into `open-api-definitions/`.
- `endpoint_overrides.json` — flat `operationId → prepend text` map consumed
  by the splitter.
- `fivetran-open-api-definition.json` — the full upstream OpenAPI spec, input
  to the splitter.
- `open-api-definitions/` — generated output: one schema file per endpoint,
  `_service-configs/` per-service connector/destination configs, the
  `endpoints.json` manifest, and `AVAILABLE_ACTIONS.md`.
- `tests/` — pytest suite covering the grant model, credential resolvers,
  the error contract, HTTP transport, OAuth, logging, and the splitter.
- `Dockerfile` — single-stage image for the streamable-http deployment.
- `pyproject.toml` — package metadata, dependencies, and the wheel's file
  list (`server.py`, `auth.py`, `open-api-definitions`).

## Getting started

See the README's [Setup](./README.md#setup) section for running the server
locally against a real Fivetran account.

To run the test suite:

```bash
pip install -e ".[dev]"
pytest tests/
```

## Configuration reference

Every environment variable the code reads, and what it does.

| Variable | Mode | Default | Effect |
|---|---|---|---|
| `FIVETRAN_API_KEY` | stdio | — | Paired with `FIVETRAN_API_SECRET` to build the `Basic` auth header `env_resolver` sends upstream. Must be unset in streamable-http mode; startup fails if it's set. |
| `FIVETRAN_API_SECRET` | stdio | — | See `FIVETRAN_API_KEY`. |
| `FIVETRAN_SCOPE` | stdio | `read` | Selects the `SCOPE_TIERS` ceiling (`read` / `read/write` / `read/write/delete`) `configure()` grants. Ignored (with a startup warning) in streamable-http mode. |
| `FIVETRAN_ALLOW_WRITES` | stdio | `false` | Legacy alias: `true` behaves like `FIVETRAN_SCOPE=read/write`. Ignored if `FIVETRAN_SCOPE` is also set. Ignored (with a startup warning) in streamable-http mode. |
| `DISALLOWED_ACTIONS` | stdio | — | Comma-separated `resource:action` or `resource:action:endpoint_name` denials carved out of the scope ceiling. Ignored (with a startup warning) in streamable-http mode. |
| `MCP_TRANSPORT` | both | `stdio` | Selects `stdio` or `streamable-http`. Same as `--transport`. |
| `MCP_HOST` | streamable-http | `127.0.0.1` | uvicorn bind host. Same as `--host`. |
| `MCP_PORT` | streamable-http | `8000` | uvicorn bind port. Same as `--port`. |
| `MCP_ALLOWED_ORIGINS` | streamable-http | — | Comma-separated allowed `Origin` header values for DNS-rebinding protection. Unset (with neither this nor `MCP_ALLOWED_HOSTS` set) disables the check and prints a startup warning. |
| `MCP_ALLOWED_HOSTS` | streamable-http | — | Comma-separated allowed `Host` header values; same protection as `MCP_ALLOWED_ORIGINS`. |
| `FIVETRAN_AUTH_ISSUER` | streamable-http | — | Fivetran's OAuth 2.1 authorization server URL. Setting it switches streamable-http from header-forwarding (`header_resolver`) to OAuth resource-server mode (`oauth_resolver` plus `auth.py`'s middleware). Ignored (with a startup warning) in stdio mode. |
| `MCP_RESOURCE_URL` | streamable-http | `https://mcp.fivetran.com/mcp` | The resource identifier advertised in the RFC 9728 well-known route. The SDK's `BearerAuthBackend` will check it against a verified token's audience, but that check only runs once `FivetranOAuthTokenVerifier.verify_token` actually returns a token — today it always raises first (see "Open decisions"). Only meaningful with `FIVETRAN_AUTH_ISSUER` set. Ignored (with a startup warning) in stdio mode. |

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

### Transport

`main()` parses `--transport {stdio,streamable-http}` (default `stdio`, env
`MCP_TRANSPORT`) plus `--host`/`--port` (env `MCP_HOST`/`MCP_PORT`, HTTP
only) via `_build_arg_parser()`. `TRANSPORT_MODES = ("stdio", "streamable-http")`
is the single source of truth both the arg parser's `choices` and
`select_credentials_resolver` validate against.

- **stdio** (default): `asyncio.run(async_main())`.
- **streamable-http**: `uvicorn.run(build_http_app(), host=..., port=...)`.
  `build_http_app()` calls `configure(SCOPE_TIERS["read/write"], ...,
  mode="streamable-http")` — hosted mode is always read/write, never delete —
  and `select_credentials_resolver("streamable-http")`, then wraps the
  existing low-level `mcp_server` in
  `mcp.server.streamable_http_manager.StreamableHTTPSessionManager(stateless=True)`
  and mounts it at `/mcp` in a Starlette app. `stateless=True` means every
  HTTP request gets a fresh MCP session with no server-side state carried
  between requests — required so instances are interchangeable behind a load
  balancer. The Starlette app's `lifespan` enters `http_client_lifespan()`
  (see "Outbound HTTP client" below) and `session_manager.run()` together, so
  each uvicorn worker owns its own HTTP client and session manager and closes
  both on shutdown. `/mcp` and `/health` (see "Operational surface" below)
  are always mounted; the OAuth well-known route is added alongside them
  when `FIVETRAN_AUTH_ISSUER` is set (see "OAuth resource-server mode"
  below).
- **Origin/Host validation**: `_build_security_settings(mode)` reads
  `MCP_ALLOWED_ORIGINS` / `MCP_ALLOWED_HOSTS` (comma-separated) and builds a
  `mcp.server.transport_security.TransportSecuritySettings` passed to the
  session manager, which enforces it per HTTP request before MCP dispatch.
  If neither env var is set, DNS-rebinding protection stays off (the SDK's
  own backwards-compatible default — an enabled check with empty allow-lists
  would reject every request) and a startup warning is printed instead of a
  hard failure, so a quick local `--transport streamable-http` test isn't
  blocked on configuring allow-lists.
- **Hosted denies**: `HOSTED_DISALLOWED_ACTIONS` is a code constant (not env),
  parsed by the same `_parse_disallowed_actions` function stdio uses — one
  mechanism for both modes. It ships empty (see "Open decisions" below).
  `FIVETRAN_SCOPE`, `DISALLOWED_ACTIONS`, and `FIVETRAN_ALLOW_WRITES` are
  stdio-only; if any is set when `build_http_app()` runs, it's ignored and a
  startup warning is printed rather than silently doing nothing.

### Grant model

`configure(scope_actions, pair_denies, endpoint_denies, mode)` runs from the
entrypoint (`async_main` for stdio, `build_http_app` for streamable-http)
after mode is known, and populates `ALLOWED_GRANTS`, `ENDPOINT_DENIES`,
`MODE`, `GENERATED_TOOLS`, and `TOOLS_BY_NAME`. Grants are process-static but
built after argv is available, so each transport passes its own scope and
denies without either touching the other's env vars.

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

**Why does denying a lower action cascade to higher ones?** If an agent
can't `read` a resource, letting it `write` or `delete` blind means the
write would succeed against something the agent — and often the user —
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
  active transport. `async_main` (stdio) registers whatever
  `select_credentials_resolver("stdio")` returns.

Three resolvers ship in `server.py`:

- `env_resolver` — reads `FIVETRAN_API_KEY` / `FIVETRAN_API_SECRET` and builds
  `Basic <b64(key:secret)>`. Used by the stdio entrypoint.
- `header_resolver` — forwards the incoming HTTP `Authorization` header as-is,
  reading from `mcp_server.request_context.request.headers`. This is the
  interim path for streamable-http: it lets an existing GitHub-clone user
  point an AI client at a hosted URL with their own Basic-auth credentials
  in the `Authorization` header, before OAuth exists. Selected whenever
  `FIVETRAN_AUTH_ISSUER` is unset.
- `oauth_resolver` — forwards the incoming `Authorization` header the same
  way `header_resolver` does, but only ever runs after the OAuth middleware
  (see "OAuth resource-server mode" below) has already verified the bearer
  token and rejected anything invalid with a 401 before dispatch. Selected
  whenever `FIVETRAN_AUTH_ISSUER` is set.

`select_credentials_resolver(mode)` maps a transport mode to the
resolver `set_credentials_resolver` should register: `stdio` →
`env_resolver`; `streamable-http` → `oauth_resolver` if
`FIVETRAN_AUTH_ISSUER` is set, else `header_resolver`. `async_main` (stdio)
calls it with `mode="stdio"`; `build_http_app()` (streamable-http) calls it
with `mode="streamable-http"`. Selecting `streamable-http` fails startup
(`ValueError`) if `FIVETRAN_API_KEY` or `FIVETRAN_API_SECRET` is set in the
environment — a shared key baked into a multi-tenant HTTP process would
apply one operator's credentials to every caller, defeating per-request
auth. This check runs eagerly at selection time, unlike the resolvers' own
lazy, per-call checks, because it's a startup-time misconfiguration rather
than a per-request condition.

`Authorization` header values must never be logged. The structured per-call
logging in "Operational surface" below enforces this: it emits a fixed
field whitelist rather than redacting, so headers are never in scope to
begin with. `User-Agent` is the one header value that is logged, as the
`client` field. See "Open decisions" below for the one remaining
documentation-only invariant in this area (`load_dotenv()`).

### OAuth resource-server mode

Set `FIVETRAN_AUTH_ISSUER` (the URL of Fivetran's OAuth 2.1 authorization
server) to switch streamable-http mode from the interim header-forwarding
path into an OAuth resource server. Fivetran's MCP server is a resource
server only — it never implements an authorization server, login, or
dynamic client registration; those belong to the auth server. Unset, this
mode does not exist and streamable-http behaves exactly as it does without
any of what follows.

`auth.py` holds everything OAuth-specific, imported only from
`build_http_app()` when `FIVETRAN_AUTH_ISSUER` is set — stdio never touches
it and needs no auth env vars. It builds on the `mcp` SDK's own
resource-server primitives (`TokenVerifier`, `BearerAuthBackend`,
`RequireAuthMiddleware`, `create_protected_resource_routes`) rather than
reimplementing RFC 9728 or bearer-token gating by hand:

- `oauth_authentication_middleware(resource_url, token_verifier)` — a
  Starlette-level `Middleware` entry (installed on every request, before
  routing) that authenticates the bearer token and populates
  `scope["user"]`/`scope["auth"]`. It does not reject anything by itself —
  a route that isn't wrapped by `require_oauth` stays open even behind it.
- `require_oauth(app, resource_url)` — wraps just the `/mcp` app. Rejects
  any request without a token `oauth_authentication_middleware` already
  validated, returning 401 with `WWW-Authenticate: Bearer ...
  resource_metadata="..."` pointing at the well-known route below.
- `oauth_protected_resource_routes(issuer, resource_url)` — the RFC 9728
  `GET /.well-known/oauth-protected-resource/mcp` route, listing `resource`
  (`MCP_RESOURCE_URL`, default `https://mcp.fivetran.com/mcp`) and
  `authorization_servers` (`[FIVETRAN_AUTH_ISSUER]`). Building this route
  calls the SDK's `validate_issuer_url`, which requires `https://` unless
  the issuer's host is `localhost` or starts with `127.0.0.1` (`http://`
  allowed there for local testing), and rejects a fragment or query string
  in the issuer URL. Startup fails loudly on a rejected issuer.
- `FivetranOAuthTokenVerifier` — implements `TokenVerifier.verify_token`.
  Its body is `NotImplementedError`: the auth server hasn't confirmed
  token format (JWT+JWKS vs. opaque+introspection) yet, and every other
  piece above is independent of that decision. `build_http_app()` accepts
  an optional `token_verifier` argument so tests can substitute a fake
  verifier and exercise the gate without real crypto; production always
  gets the real (currently stub) verifier.

`MCP_RESOURCE_URL` and `FIVETRAN_AUTH_ISSUER` are hosted-deployment operator
configuration — nobody self-hosting runs their own OAuth issuer against
Fivetran's API — so they're documented here, not in the README.

An upstream 401 mid-call (a bearer token valid at request time that the
Fivetran API now rejects, e.g. account-level MCP disable) is not turned
into a raw HTTP 401 — it stays a shaped `UPSTREAM_UNAUTHORIZED` tool result
(HTTP 200 at the transport level), since there's no supported way to
rewrite an in-flight JSON-RPC response into a transport-level status from
inside `do_call`. The real entry-point gate — missing, invalid, or expired
token — is already enforced fresh on every stateless request by
`oauth_authentication_middleware`/`require_oauth`; account disablement is
expected to rely on a short access-token TTL so the next request's
verification naturally 401s.

`list_endpoints` and `get_schema` read the local manifest and don't require
credentials. `CredentialsError` only surfaces when an API-hitting tool is
invoked, so clients can browse the tool surface before auth is wired.

### Operational surface

Hosted-deployment concerns only — nobody self-hosting stdio needs any of
this, so (matching how `MCP_RESOURCE_URL`/`FIVETRAN_AUTH_ISSUER` are handled
above) it's documented here, not in the README.

- **`GET /health`** — unauthenticated (never passed through
  `auth.require_oauth`, same treatment as the OAuth well-known route).
  Returns `{"status": "ok", "version": __version__, "manifest_checksum":
  MANIFEST_CHECKSUM}`. `MANIFEST_CHECKSUM` is computed once at import from
  `endpoints.json`'s raw bytes and cached — the process never reloads the
  file, so a per-request hash would just describe a manifest the server
  isn't running.
- **Structured per-call JSON logs** — `call_tool` assigns a `uuid.uuid4()`
  request id per invocation (the JSON-RPC `request_id` is client-assigned
  and not globally unique, so it isn't used as the correlation id) and logs
  one JSON line in a `finally` block, so it fires on every path including
  uncaught exceptions. Fields: `request_id`, `mode`, `tool`, `endpoint`
  (`None` for `list_endpoints`), `upstream_status`, `latency_ms`, `client`
  — a fixed whitelist that never includes `arguments` or the `Authorization`
  header.
  - **Destination is transport-dependent**: stdio's stdout is the MCP
    JSON-RPC channel itself, so log lines there would corrupt the
    connection. HTTP mode logs to stdout; stdio logs to stderr.
  - **`client`** comes from `_resolve_client_name()`. stdio's session
    persists for the whole connection, so `clientInfo.name` is reliable.
    HTTP mode is stateless — every request gets a brand-new `ServerSession`,
    so `client_params` is always `None` there — so it logs the raw incoming
    `User-Agent` header instead.
  - **`upstream_status`** is set in `_fivetran_request` from
    `response.status_code`, right before `raise_for_status()`, so it's
    captured for every upstream call that gets a response back, success or
    failure. It's `None` only when no upstream call happened at all:
    `list_endpoints`, `get_schema`, a validation short-circuit inside
    `do_call`, `CREDENTIALS_MISSING`, or a transport failure that never got
    a response.
- **`Dockerfile`** (repo root) — single-stage `python:3.12-slim`, installs
  the package via `pip install .` (matching `[tool.hatch.build.targets.wheel]
  only-include`'s file list), runs as a non-root user, and defaults to
  `fivetran-mcp --transport streamable-http --host 0.0.0.0 --port 8000`.
  TLS terminates upstream (load balancer/ingress) — nothing TLS-related in
  the image itself.

### Error contract

Every caller-correctable failure returns `{"error": <CODE>, "message": <str>, ...}`
as the tool's JSON text result (`isError=False`) rather than raising — an
agent can branch on `error` without re-parsing prose. Codes:

- `GRANT_NOT_ALLOWED` — endpoint requires an action outside the current scope
  or is explicitly disallowed. Structured fields (`cause`, `required_grant`,
  `scope`, `disallowed`, `endpoint_disallowed`) let the agent branch
  programmatically; the human-facing "use the Fivetran dashboard or REST API"
  redirect lives once in the server instructions rather than being repeated
  per response.
- `ENDPOINT_TOOL_MISMATCH` — endpoint doesn't belong to the calling tool's
  `(resource, action)` group.
- `UNKNOWN_ENDPOINT` — `do_call`/`do_get_schema` given a `name` not in the
  manifest.
- `UNKNOWN_TOOL` — `call_tool` dispatched a name outside `list_endpoints`,
  `get_schema`, and `TOOLS_BY_NAME` (a client calling a tool that doesn't
  exist).
- `MISSING_PATH_PARAM` — `do_call`'s `path_params` didn't cover every
  `{placeholder}` in the endpoint's path; `missing` lists the gaps.
- `INVALID_BODY` — `body` was a string that failed `json.loads`.
- `CREDENTIALS_MISSING` — the registered `CredentialsResolver` raised
  `CredentialsError` (no `FIVETRAN_API_KEY`/`SECRET` in stdio; no/empty
  `Authorization` header in streamable-http).
- `UPSTREAM_UNAUTHORIZED` / `UPSTREAM_FORBIDDEN` / `UPSTREAM_RATE_LIMITED` /
  `UPSTREAM_ERROR` — Fivetran API returned 401 / 403 / 429 / another 4xx.
  `_shape_upstream_error` classifies these from the raised
  `httpx.HTTPStatusError`, pulling `code`/`message` out of the upstream JSON
  body when present; 429 adds `retry_after` from the `Retry-After` header
  when the upstream response sent one.

Everything else — Fivetran 5xx responses (`do_call` re-raises rather than
shaping them), transport-level failures (connection errors, timeouts), and
the two `do_get_schema` validation `ValueError`s that aren't in the list
above (bad `service` argument combination; unknown service name) — is not
shaped. It propagates out of `call_tool` uncaught, and the MCP SDK's own
`@mcp_server.call_tool()` decorator turns it into a genuine
`CallToolResult(isError=True, ...)`. This is a deliberate split: shaped
`isError=False` results are for outcomes an agent should read and act on
programmatically; `isError=True` is for failures — the client shouldn't
try to parse the text as structured data.

### Outbound HTTP client

`_fivetran_request` shares one module-level `httpx.AsyncClient` across all
calls rather than opening one per request:

- `http_client_lifespan()` — async context manager that owns the client's
  lifecycle: opens it on enter with a fixed `httpx.Timeout`/`httpx.Limits`,
  closes it on exit. Standalone and transport-agnostic; takes no arguments.
- `get_http_client()` — the only accessor. Raises `RuntimeError` if called
  before the lifespan is entered, so a missing or leaked client fails loudly
  instead of silently reopening a connection pool per call.
- `async_main` (stdio) enters `http_client_lifespan()` around
  `stdio_server()`/`mcp_server.run(...)`. `build_http_app()` (streamable-http)
  enters the same context manager inside the Starlette app's `lifespan=`
  (alongside `session_manager.run()`), so each uvicorn worker owns and closes
  its own client the same way.

**Outbound `User-Agent`** — `_get_auth_header` sends
`fivetran-official-mcp-{mode}-{client}/{__version__}` on every Fivetran API
call, e.g. `fivetran-official-mcp-stdio-claude-code/0.3.2` or
`fivetran-official-mcp-http-cursor/0.3.2`. `{client}` comes from two
different sources depending on transport, both routed through
`_raw_client_identifier()`: stdio uses `clientInfo.name` from the session,
sanitized into a slug; streamable-http uses the incoming `User-Agent`
header, matched by substring against a small table of known AI clients
(claude, chatgpt/openai, cursor, codex, gemini). See "Open decisions" below
for what happens on an HTTP request whose `User-Agent` matches none of
those.

## Open decisions / known gaps

- `FivetranOAuthTokenVerifier.verify_token` (`auth.py`) is a
  `NotImplementedError` stub. Real verification is blocked on the auth
  server team confirming token format (JWT+JWKS vs. opaque+introspection).
- `HOSTED_DISALLOWED_ACTIONS` ships empty. Several read/write endpoints
  return or mint credentials — `get_user_api_key`, `list_api_keys`
  (`users_read`); `connect_card`, `regenerate_secrets_proxy_agent`,
  `reset_hybrid_deployment_agent_credentials`, `re_auth_hybrid_deployment_agent`
  (`*_write`) — and an OAuth session that can read a permanent API key
  defeats the reason customers asked for OAuth. Whether to deny some or all
  of these in hosted mode is undecided.
- `FIVETRAN_AUTH_ISSUER` stays optional at streamable-http startup because
  `verify_token` isn't implemented yet; requiring it would force every
  deployment through a verifier that unconditionally fails. Revisit once
  real token verification lands.
- **Known bug**: in streamable-http mode, `_resolve_ua_client_slug()`
  returns an unrecognized client's raw `User-Agent` header value verbatim,
  unsanitized, for use in the outbound `User-Agent` sent to Fivetran. Two
  problems: the raw header can contain `/`, spaces, or parentheses, which
  breaks the `fivetran-official-mcp-{mode}-{client}/{version}` format for
  any downstream parser expecting one `/`-delimited version suffix; and it
  forwards a client-controlled string upstream unmodified. stdio sanitizes
  its equivalent (`clientInfo.name`) into a slug before use; HTTP mode
  should do the same instead of falling through to the raw value.
- `load_dotenv()` runs once at import (`server.py`). Harmless in a container
  that only ever runs one mode, but nothing stops a `.env` file carrying a
  real key/secret — left over from local dev, or baked into a shared image —
  from reintroducing the exact shared-key misconfiguration
  `select_credentials_resolver` otherwise rejects in streamable-http mode.
  Documented only; not enforced in code.
