"""OAuth 2.1 resource-server plumbing for streamable-http mode.

Imported only from server.build_http_app() when FIVETRAN_AUTH_ISSUER is
set — stdio mode never imports this module and needs no auth env vars.

Fivetran's MCP server is a resource server only. It does not implement an
authorization server, login, or dynamic client registration — those are
Fivetran's auth server's job. This module wires the installed `mcp` SDK's
own resource-server primitives (TokenVerifier, BearerAuthBackend,
RequireAuthMiddleware, create_protected_resource_routes) rather than
reimplementing RFC 9728 / bearer-token gating by hand.

FivetranOAuthTokenVerifier.verify_token is deliberately still
NotImplementedError: the auth server hasn't confirmed token format (JWT +
JWKS vs. opaque + introspection) yet, and guessing now would mean throwing
the implementation away. Everything else here — the well-known route, the
401 + WWW-Authenticate gate, resource/audience checking — does not depend
on that decision and is safe to build ahead of it.
"""
from pydantic import AnyHttpUrl
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route
from starlette.types import ASGIApp

from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_protected_resource_routes,
    validate_issuer_url,
)


class FivetranOAuthTokenVerifier(TokenVerifier):
    """Verifies a bearer token issued by Fivetran's OAuth authorization server.

    Body raises NotImplementedError until the auth server confirms token
    format — see module docstring. This is the only piece that needs a real
    implementation later; wrap_mcp_app_with_oauth and the resource-metadata
    route around it are already load-bearing.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        raise NotImplementedError(
            "FivetranOAuthTokenVerifier.verify_token is not implemented yet; "
            "wire this up once Fivetran's OAuth broker confirms token format "
            "(JWT+JWKS vs. opaque+introspection)."
        )


def oauth_authentication_middleware(
    resource_url: str,
    token_verifier: TokenVerifier | None = None,
) -> Middleware:
    """Starlette top-level middleware entry that authenticates every request's
    bearer token, populating `scope["user"]`/`scope["auth"]`.

    Must be installed as `Starlette(middleware=[...])` — i.e. it needs to run
    on every request, before routing — not wrapped around a single route's
    app. `require_oauth` (below) is the piece that actually rejects
    unauthenticated requests, and only wraps the routes that need it; this
    middleware alone doesn't reject anything (a Route the app doesn't wrap
    with `require_oauth`, like the well-known route, stays open even though
    this runs in front of it too). `token_verifier` defaults to
    FivetranOAuthTokenVerifier(); tests substitute a fake to exercise the
    gate without real crypto.
    """
    verifier = token_verifier or FivetranOAuthTokenVerifier()
    backend = BearerAuthBackend(verifier, resource_server_url=AnyHttpUrl(resource_url))
    return Middleware(AuthenticationMiddleware, backend=backend)


def require_oauth(app: ASGIApp, resource_url: str) -> ASGIApp:
    """Reject `app`'s requests unless `oauth_authentication_middleware` already
    authenticated them (i.e. populated `scope["user"]` with a valid token).

    Missing, malformed, expired, or wrong-audience tokens get a 401 with
    `WWW-Authenticate: Bearer ... resource_metadata="..."` pointing at the
    RFC 9728 well-known route (see oauth_protected_resource_routes), so the
    client knows where to re-authenticate. Wrap only the app(s) that need
    the gate — e.g. the `/mcp` mount — not the whole Starlette app, or the
    well-known route itself would 401.
    """
    metadata_url = build_resource_metadata_url(AnyHttpUrl(resource_url))
    return RequireAuthMiddleware(app, required_scopes=[], resource_metadata_url=metadata_url)


def oauth_protected_resource_routes(issuer: str, resource_url: str) -> list[Route]:
    """RFC 9728 `/.well-known/oauth-protected-resource` route for `resource_url`.

    Fails loudly if `issuer` isn't a well-formed HTTPS URL — same posture as
    the rest of this codebase's startup validation.
    """
    issuer_url = AnyHttpUrl(issuer)
    validate_issuer_url(issuer_url)
    return create_protected_resource_routes(AnyHttpUrl(resource_url), [issuer_url])
