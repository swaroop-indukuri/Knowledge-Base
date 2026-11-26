"""Shared auth dependency: verifies the Supabase access token and returns the user id.

Supabase projects sign JWTs in one of two ways, and this handles both by reading
the token's `alg` header:
  - HS256: legacy shared secret  -> verified with SUPABASE_JWT_SECRET
  - ES256 / RS256: asymmetric signing keys -> verified with the project's JWKS
    at <SUPABASE_URL>/auth/v1/.well-known/jwks.json
The allowed algorithms are fixed per branch (never taken from the token) to
prevent algorithm-confusion attacks.
"""

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)

ASYMMETRIC_ALGS = ("ES256", "RS256")


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # PyJWKClient caches fetched keys, so the network is hit rarely.
    return PyJWKClient(jwks_url, cache_keys=True)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Return the authenticated user's id (the JWT `sub` claim) or raise 401."""
    if credentials is None:
        raise _unauthorized()

    token = credentials.credentials
    settings = get_settings()
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else None
    decode_kwargs = {
        "audience": "authenticated",
        "options": {"require": ["exp", "sub"]},
    }
    if issuer:
        decode_kwargs["issuer"] = issuer

    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise HTTPException(500, "Server auth is not configured (SUPABASE_JWT_SECRET)")
            claims = jwt.decode(
                token, settings.supabase_jwt_secret, algorithms=["HS256"], **decode_kwargs
            )
        elif alg in ASYMMETRIC_ALGS:
            if not settings.supabase_url:
                raise HTTPException(500, "Server auth is not configured (SUPABASE_URL)")
            jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
            key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=[alg], **decode_kwargs)
        else:
            raise _unauthorized()
    except jwt.PyJWTError:
        raise _unauthorized() from None

    user_id = claims.get("sub")
    if not user_id:
        raise _unauthorized()
    return str(user_id)
