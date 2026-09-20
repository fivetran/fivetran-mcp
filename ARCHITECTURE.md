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

### Transport

`main()` parses `--transport {stdio,streamable-http}` (default `stdio`, env
`MCP_TRANSPORT`) plus `--host`/`--port` (env `MCP_HOST`/`MCP_PORT`, HTTP
only) via `_build_arg_parser()`. `TRANSPORT_MODES = ("stdio", "streamable-http")`
is the single source of truth both the arg parser's `choices` and
`select_credentials_resolver` validate against.

- **stdio** (default): `asyncio.run(async_main())`, unchanged from before P1.
- **streamable-http**: `uvicorn.run(build_http_app(), host=..., port=...)`.
  `build_http_app()` calls `configure(SCOPE_TIERS["read/write"], ...,
  mode="streamable-http")` — hosted mode is always read/write, never delete —
  and `select_credentials_resolver("streamable-http")` (fails eagerly per P2
  if a shared API key is set), then wraps the existing low-level `mcp_server`
  in `mcp.server.streamable_http_manager.StreamableHTTPSessionManager(stateless=True)`
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
  parsed by the same `_parse_disallowed_actions` function stdio uses — "one
  mechanism for both modes." It ships empty; which credential-minting
  endpoints (`get_user_api_key`, `connect_card`, etc.) it should exclude is
  an open decision tracked in `local/hosted-mcp-plan.md` ("Flag for future
  review"), unblocked by this constant's existence. `FIVETRAN_SCOPE`,
  `DISALLOWED_ACTIONS`, and `FIVETRAN_ALLOW_WRITES` are stdio-only; if any is
  set when `build_http_app()` runs, it's ignored and a startup warning is
  printed rather than silently doing nothing.

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

Two related invariants, both documentation-level today (no enforcement code
exists yet to point at):

- `load_dotenv()` runs once at import. Harmless in a container that only
  ever runs one mode, but HTTP deployments must not rely on it — a `.env`
  file carrying a real key/secret, left over from local dev or baked into a
  shared image, recreates the exact shared-key misconfiguration
  `select_credentials_resolver` rejects above.
- `Authorization` header values must never be logged. The structured
  per-call logging added in "Operational surface" below excludes header
  values entirely (a fixed field whitelist, not redaction).

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
  `authorization_servers` (`[FIVETRAN_AUTH_ISSUER]`).
- `FivetranOAuthTokenVerifier` — implements `TokenVerifier.verify_token`.
  Its body is `NotImplementedError`: the auth server hasn't confirmed
  token format (JWT+JWKS vs. opaque+introspection) yet, and every other
  piece above is independent of that decision. `build_http_app()` accepts
  an optional `token_verifier` argument so tests can substitute a fake
  verifier and exercise the gate without real crypto; production always
  gets the real (currently stub) verifier.

`FIVETRAN_AUTH_ISSUER` is validated as a well-formed HTTPS URL at startup
(fails loudly otherwise, matching this codebase's other startup checks).
`MCP_RESOURCE_URL` and `FIVETRAN_AUTH_ISSUER` are hosted-deployment operator
configuration — nobody self-hosting runs their own OAuth issuer against
Fivetran's API — so they're documented here, not in the README.

An upstream 401 from the Fivetran API mid-call (a bearer token that was
valid at request time but the API itself now rejects — e.g. account-level
MCP disable) is *not* turned into a raw HTTP 401. It stays exactly what the
error contract below already produces: a shaped `UPSTREAM_UNAUTHORIZED`
tool result, HTTP 200 at the transport level. There's no supported
extension point in the SDK's low-level `Server`/`StreamableHTTPSessionManager`
to rewrite an in-flight JSON-RPC response into a raw HTTP status from
inside `do_call`, several layers below where that status is decided. The
real entry-point gate — missing, invalid, or expired bearer token — is
already enforced by `oauth_authentication_middleware`/`require_oauth` on
every single request (stateless mode re-verifies fresh each time); account
disablement is expected to rely on a short access-token TTL so the next
request's fresh verification naturally 401s, not on this server catching
a mid-call signal from the upstream API.

`list_endpoints` and `get_schema` read the local manifest and don't require
credentials. `CredentialsError` only surfaces when an API-hitting tool is
invoked, so clients can browse the tool surface before auth is wired.

### Operational surface

Hosted-deployment concerns only — nobody self-hosting stdio needs any of
this, so (matching how `MCP_RESOURCE_URL`/`FIVETRAN_AUTH_ISSUER` are handled
above) it's documented here, not in the README.

- **`GET /health`** — added straight to `build_http_app()`'s `routes` list,
  never passed through `auth.require_oauth`, so it's reachable regardless of
  OAuth configuration (same treatment the `/.well-known/oauth-protected-resource/mcp`
  route already gets). No auth. Returns `{"status": "ok", "version":
  __version__, "manifest_checksum": MANIFEST_CHECKSUM}`. `MANIFEST_CHECKSUM`
  is a sha256 hex digest of `endpoints.json`'s raw bytes, computed once at
  import inside `_load_manifest()` and cached as a module constant — the
  process never reloads the file at runtime, so a live-recomputed hash per
  request would just describe a manifest the server isn't running.
- **Structured per-call JSON logs** — `call_tool` generates a fresh
  `uuid.uuid4()` request id and a `time.monotonic()` timer per invocation
  (the JSON-RPC `request_id` on `RequestContext` is client-assigned and not
  globally unique — collisions are expected across different clients or
  stateless HTTP sessions — so it isn't used as the correlation id), then
  emits one JSON line via `_record_tool_call(_build_call_record(...))` in a
  `finally` block, so it fires on every path including uncaught exceptions.
  Fields: `request_id`, `mode`, `tool` (the dispatched MCP tool name),
  `endpoint` (the `name` argument, when the tool takes one — `None` for
  `list_endpoints`), `upstream_status`, `latency_ms`, `client`. Only these
  whitelisted fields are logged — never `arguments` (which can carry a
  request body) and never headers.
  - **Destination is transport-dependent, not uniformly stdout.** stdio's
    stdout is the MCP JSON-RPC protocol channel itself; writing log lines
    there would corrupt every stdio client's connection. HTTP mode logs to
    stdout; stdio mode logs to stderr, matching this module's existing
    `print(..., file=sys.stderr)` warning convention.
  - **`client`** is a stand-in until a future proposal normalizes it: HTTP
    mode uses the raw incoming `User-Agent` header; stdio mode uses
    `mcp_server.request_context.session.client_params.clientInfo.name`.
    These aren't interchangeable by accident — `StreamableHTTPSessionManager`
    runs `stateless=True` here, so every HTTP request gets a brand-new
    `ServerSession`; the `InitializeRequest` that populates `client_params`
    and the later `tools/call` request land on two different sessions, so
    `client_params` is `None` on every HTTP tool call. stdio's session
    persists for the whole connection, so `client_params` is reliable
    there. `_resolve_client_name()` falls back to `"unknown"` outside a
    real request/session (e.g. a direct call in a test).
  - **`upstream_status` scope boundary**: populated only for the server's
    own classified 4xx dicts (`_shape_upstream_error`'s `"status"` field)
    and a caught-then-reraised 5xx; `None` for everything else, including
    every successful call. `_fivetran_request` returns a successful
    response's JSON body directly with no status side-channel, and adding
    one would mean either reshaping `do_call`'s return contract (breaking
    the shapes `tests/test_http_client.py` and `tests/test_error_contract.py`
    pin) or guess-parsing arbitrary Fivetran response bodies for a
    `"status"`-like field — risking misattributing a real business field as
    an HTTP status. Not attempted.
- **Metrics** — `_record_tool_call` is the single hook where future metrics
  attach (calls by tool/status, upstream codes, 401s, init failures). It's
  an instrumentation seam only today: no counters, no exporter, no new
  dependency.
- **`FIVETRAN_AUTH_ISSUER` stays optional.** Unaffected by any of the above
  — P3b (real token verification) is still `NotImplementedError`, so
  requiring the issuer at HTTP startup would force every deployment through
  a verifier that unconditionally fails. Revisit once P3b lands.
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
call, e.g. `fivetran-official-mcp-stdio-claude-code/0.3.1` or
`fivetran-official-mcp-http-cursor/0.3.1`. `{mode}` is a short label from
`_MODE_UA_LABEL` (`stdio` stays `stdio`; `streamable-http` becomes `http`
here only — every other use of `MODE` in this module keeps the full value).
`{client}` comes from `_resolve_ua_client_slug()`, built on
`_raw_client_identifier()` — the same raw signal the "Operational surface"
section's per-call log `client` field uses (stdio: `clientInfo.name`; HTTP:
the incoming `User-Agent` header, since stateless sessions never populate
`clientInfo` — see that section) — but sanitized differently for this use:
stdio's `clientInfo.name` is slugified (`_sanitize_client_slug`, lowercased
with non-alphanumerics collapsed to hyphens); HTTP's raw header is matched
by substring against `_HTTP_CLIENT_MARKERS` (claude, chatgpt/openai,
cursor, codex, gemini), falling through to the **raw header value
verbatim** — not sanitized, not `"unknown"` — when nothing matches.
`"unknown"` is reserved for when there's no `User-Agent` at all to report.
Since an unrecognized client's raw header can itself contain `/`, spaces,
or parentheses, a downstream consumer (e.g. a BigQuery query) parsing this
header can't safely split on the first or last `/` to isolate the version —
it should match by substring instead.
