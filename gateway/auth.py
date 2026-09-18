"""API-key Bearer authentication.

Extracts the Bearer token from the Authorization header, hashes it with SHA-256,
looks up the key in `api_keys` (joined with `users`), checks revoked/expired/status,
and returns (user_id, api_key_id, allowed_tools).

`user_id` is NEVER accepted from tool arguments — always derived from the key.
"""

from __future__ import annotations

import hashlib
import logging
import time

import asyncpg

log = logging.getLogger(__name__)

# In-memory key cache: key_hash → (expiry, user_id, api_key_id, allowed_tools)
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
            return None  # cached miss — never authenticate an empty user_id
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


async def authenticate_request(
    authorization: str | None,
    pool: asyncpg.Pool,
) -> tuple[str, str, list[str]] | None:
    """Validate bearer token. Returns (user_id, api_key_id, allowed_tools) or None."""
    raw_key = _extract_bearer(authorization)
    if raw_key is None:
        return None
    key_hash = _hash_key(raw_key)
    return await _lookup_key(pool, key_hash)


def invalidate_key_cache(key_hash: str) -> None:
    """Called by control plane on key revoke."""
    _key_cache.pop(key_hash, None)
