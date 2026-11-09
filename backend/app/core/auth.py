"""Authentication: who is calling, and in which workspace.

Three modes (``PHEME_AUTH_MODE``) behind one function, ``authenticate``:

- ``none``: a fixed dev user and workspace (local development only; refused in production);
- ``dev``: email login (``POST /auth/login``) issuing HS256 tokens signed with ``PHEME_SECRET_KEY``;
- ``oidc``: bearer JWTs from an OIDC provider, verified against the issuer's JWKS. This is
  the route to SSO: the provider handles sign-in, Pheme only verifies tokens.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import scope
from app.core.principal import Principal
from app.db.tables import User, Workspace

COOKIE = "pheme_session"


class SigningKeys(Protocol):
    """What verify_oidc needs from a JWKS client (PyJWKClient, or a fake in tests)."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class AuthError(Exception):
    """Missing or invalid credentials (HTTP 401)."""


def workspace_id_for(key: str) -> UUID:
    """Stable workspace id for a workspace key (an email domain or an OIDC claim value)."""
    return uuid5(NAMESPACE_URL, f"pheme:workspace:{key.lower()}")


def issue_token(principal: Principal, email: str) -> str:
    s = get_settings()
    now = int(time.time())
    claims = {"sub": str(principal.user_id), "ws": str(principal.workspace_id), "email": email,
              "iat": now, "exp": now + s.session_ttl_s, "iss": "pheme-dev"}  # fmt: skip
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def _verify_dev(token: str) -> Principal:
    try:
        claims = jwt.decode(
            token, get_settings().secret_key, algorithms=["HS256"], issuer="pheme-dev"
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Your session is invalid or has expired. Sign in again.") from exc
    return Principal(user_id=UUID(claims["sub"]), workspace_id=UUID(claims["ws"]))


@lru_cache
def _jwks(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json", cache_keys=True)


def verify_oidc(token: str, jwks: SigningKeys | None = None) -> dict[str, Any]:
    s = get_settings()
    if not s.oidc_issuer or not s.oidc_audience:
        raise AuthError("OIDC isn't configured.")
    try:
        key = (jwks or _jwks(s.oidc_issuer)).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token, key.key, algorithms=["RS256", "ES256"], audience=s.oidc_audience, issuer=s.oidc_issuer,
            options={"require": ["exp", "iss", "aud"]},
        )  # fmt: skip
    except jwt.PyJWTError as exc:
        raise AuthError("The sign-in token isn't valid.") from exc
    if not claims.get("email"):
        raise AuthError("The sign-in token has no email.")
    return claims


def provision(db: Session, email: str, workspace_key: str | None = None) -> Principal:
    """The user for ``email`` (created with their workspace on first sign-in)."""
    email = email.strip().lower()
    key = workspace_key or email.rsplit("@", 1)[-1]
    scope(db, None, bypass=True)  # users are looked up by email across workspaces
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None:
        ws_id = workspace_id_for(key)
        if db.get(Workspace, ws_id) is None:
            db.add(Workspace(id=ws_id, name=key))
            db.flush()
        user = User(id=uuid4(), workspace_id=ws_id, email=email, display_name=email.split("@")[0])
        db.add(user)
        db.flush()
    who = Principal(user_id=user.id, workspace_id=user.workspace_id)
    scope(db, None)  # commits; the caller sets the workspace next
    return who


def _dev_stub(db: Session) -> Principal:
    s = get_settings()
    who = Principal(user_id=s.dev_user_id, workspace_id=s.dev_workspace_id)
    scope(db, None, bypass=True)
    if db.get(Workspace, who.workspace_id) is None:
        db.add(Workspace(id=who.workspace_id, name="Dev workspace"))
        db.flush()
    if db.get(User, who.user_id) is None:
        db.add(
            User(
                id=who.user_id,
                workspace_id=who.workspace_id,
                email=f"dev+{who.user_id}@pheme.local",
            )
        )
    scope(db, None)
    return who


def token_from(authorization: str | None, cookie: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return cookie or None


def authenticate(db: Session, token: str | None) -> Principal:
    mode = get_settings().auth_mode
    if mode == "none":
        return _dev_stub(db)
    if not token:
        raise AuthError("Sign in to continue.")
    if mode == "dev":
        return _verify_dev(token)
    claims = verify_oidc(token)
    ws_claim = get_settings().oidc_workspace_claim
    return provision(
        db,
        claims["email"],
        str(claims.get(ws_claim)) if ws_claim and claims.get(ws_claim) else None,
    )


def check_startup() -> None:
    s = get_settings()
    if s.env == "production" and s.auth_mode == "none":
        raise RuntimeError(
            "PHEME_AUTH_MODE=none is for local development; set dev or oidc in production."
        )
    if s.env == "production" and (
        s.secret_key.startswith("dev-only-") or len(s.secret_key.encode()) < 32
    ):
        raise RuntimeError("Set PHEME_SECRET_KEY in production (at least 32 random bytes).")
