"""OAuth 2.1 resource-server plumbing for streamable-http mode.

Imported only by server.build_http_app() when FIVETRAN_AUTH_ISSUER is set;
stdio mode never imports it.

This server is a resource server only. Login, token issuance, and client
registration belong to Fivetran's authorization server. Built on the mcp
SDK's resource-server primitives (TokenVerifier, BearerAuthBackend,
RequireAuthMiddleware, create_protected_resource_routes).
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
    """Verifies bearer tokens issued by Fivetran's OAuth authorization server."""

    # TODO: implement once the auth server confirms token format.
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
    """Starlette middleware that authenticates bearer tokens on every request.

    Populates scope["user"]/scope["auth"] but rejects nothing; require_oauth
    does the rejecting. Install via Starlette(middleware=[...]).
    `token_verifier` is a test hook; defaults to FivetranOAuthTokenVerifier().
    """
    verifier = token_verifier or FivetranOAuthTokenVerifier()
    backend = BearerAuthBackend(verifier, resource_server_url=AnyHttpUrl(resource_url))
    return Middleware(AuthenticationMiddleware, backend=backend)


def require_oauth(app: ASGIApp, resource_url: str) -> ASGIApp:
    """Return 401 for requests to `app` without a valid authenticated token.

    The 401 includes WWW-Authenticate pointing at the well-known metadata route.
    Wrap only the /mcp app, not the whole Starlette app, or the well-known
    route itself would return 401.
    """
    metadata_url = build_resource_metadata_url(AnyHttpUrl(resource_url))
    return RequireAuthMiddleware(app, required_scopes=[], resource_metadata_url=metadata_url)


def oauth_protected_resource_routes(issuer: str, resource_url: str) -> list[Route]:
    """RFC 9728 /.well-known/oauth-protected-resource route. Raises on an invalid issuer URL."""
    issuer_url = AnyHttpUrl(issuer)
    validate_issuer_url(issuer_url)
    return create_protected_resource_routes(AnyHttpUrl(resource_url), [issuer_url])