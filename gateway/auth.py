"""API-key + OAuth JWT dual authentication.

Tries API-key first (SHA-256 lookup), then OAuth JWT (HS256 verify).
Both paths return (user_id, api_key_id, allowed_tools) where api_key_id
is None for OAuth callers. `user_id` is NEVER accepted from tool arguments.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time

import asyncpg
import jwt as pyjwt

log = logging.getLogger(__name__)

# In-memory key cache: key_hash -> (expiry, user_id, api_key_id, allowed_tools)
_key_cache: dict[str, tuple[float, str, str | None, list[str]]] = {}
_CACHE_TTL = 60  # seconds


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token if token else None
    return None


async def _lookup_key(pool: asyncpg.Pool, key_hash: str) -> tuple[str, str | None, list[str]] | None:
    """Return (user_id, api_key_id, allowed_tools) or None if key is invalid."""
    now = time.time()

    cached = _key_cache.get(key_hash)
    if cached and cached[0] > now:
        if not cached[1]:
            return None  # cached miss
        return cached[1], cached[2], cached[3]

    row = await pool.fetchrow(
        """
        SELECT ak.id AS api_key_id, ak.user_id, ak.revoked_at, ak.expires_at,
               ak.scopes, u.status AS user_status
        FROM api_keys ak
        JOIN users u ON u.id = ak.user_id
        WHERE ak.key_hash = $1
        """,
        key_hash,
    )
    if row is None:
        _key_cache[key_hash] = (now + _CACHE_TTL, "", None, [])
        return None

    if row["revoked_at"] is not None:
        log.warning("Revoked API key used: prefix=%s", key_hash[:8])
        return None

    if row["expires_at"] is not None and row["expires_at"].timestamp() < time.time():
        log.warning("Expired API key used: prefix=%s", key_hash[:8])
        return None

    if row["user_status"] != "active":
        log.warning("Disabled user attempted auth: user_id=%s", row["user_id"])
        return None

    user_id = str(row["user_id"])
    api_key_id = str(row["api_key_id"])
    allowed_tools = list(row["scopes"] or [])

    _key_cache[key_hash] = (now + _CACHE_TTL, user_id, api_key_id, allowed_tools)
    return user_id, api_key_id, allowed_tools


async def _verify_oauth_jwt(token: str, pool: asyncpg.Pool) -> tuple[str, None, list[str]] | None:
    """Verify an OAuth JWT issued by our control plane.

    Checks signature, expiry, required scope, live grant revocation,
    and user status on EVERY call (no positive caching) so a revoked
    grant or disabled account goes blind in <60s.
    Returns (user_id, api_key_id=None, allowed_tools=[]) or None.
    """
    secret = os.getenv("OAUTH_JWT_SECRET", "")
    if not secret:
        return None

    try:
        claims = pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.InvalidTokenError:
        return None

    user_id = claims.get("sub")
    grant_id = claims.get("grant_id")
    if not user_id or not grant_id:
        return None

    # Scope enforcement: consent promises mcp:tools, gateway holds it
    scopes = (claims.get("scope") or "").split()
    if "mcp:tools" not in scopes:
        log.warning("OAuth token without mcp:tools scope: grant=%s", grant_id)
        return None

    # Live revocation + status check (no cache — revoke must propagate fast)
    row = await pool.fetchrow(
        "SELECT g.revoked_at, u.status FROM oauth_grants g "
        "JOIN users u ON u.id = g.user_id "
        "WHERE g.id = $1 AND g.user_id = $2",
        grant_id, user_id,
    )
    if row is None or row["revoked_at"] is not None or row["status"] != "active":
        return None

    return (user_id, None, [])


async def authenticate_request(
    authorization: str | None,
    pool: asyncpg.Pool,
) -> tuple[str, str | None, list[str]] | None:
    """Validate bearer token. Returns (user_id, api_key_id, allowed_tools) or None.

    Tries API-key lookup first. If that fails, tries OAuth JWT verification.
    """
    raw_token = _extract_bearer(authorization)
    if raw_token is None:
        return None

    # Try API key first
    key_hash = _hash_key(raw_token)
    result = await _lookup_key(pool, key_hash)
    if result is not None:
        return result

    # Fall back to OAuth JWT
    return await _verify_oauth_jwt(raw_token, pool)


def invalidate_key_cache(key_hash: str) -> None:
    """Called by control plane on key revoke."""
    _key_cache.pop(key_hash, None)
