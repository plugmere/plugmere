"""API keys — create (plaintext shown once), list (prefix only), revoke."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter

from gateway.auth import invalidate_user_cache

from ..db import get_pool
from ..deps import get_current_user
from ..schemas import ApiKeyCreate, ApiKeyCreated
from ..security import generate_api_key

router = APIRouter(prefix='/api/v1/api-keys', tags=['api-keys'])


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else v


def _out(row) -> dict:
    scopes = TypeAdapter(list[str]).validate_python(row['scopes'])
    return {
        'id': str(row['id']),
        'name': row['name'],
        'key_prefix': row['key_prefix'],
        'scopes': scopes,
        'last_used_at': _iso(row['last_used_at']),
        'expires_at': _iso(row['expires_at']),
        'revoked_at': _iso(row['revoked_at']),
        'created_at': _iso(row['created_at']),
    }


@router.post('', status_code=201)
async def create_key(req: ApiKeyCreate, user: dict = Depends(get_current_user)) -> ApiKeyCreated:
    """Create an API key for yourself (admins may target another user later via /users scope)."""
    raw, key_hash, prefix = generate_api_key()

    expires_at = None
    if req.expires_at:
        try:
            expires_at = datetime.fromisoformat(req.expires_at.replace('Z', '+00:00'))
        except ValueError as e:
            raise HTTPException(status_code=422, detail='Invalid expires_at ISO timestamp') from e

    pool = await get_pool()
    row = await pool.fetchrow(
        'INSERT INTO api_keys (key_hash, key_prefix, user_id, name, scopes, expires_at) '
        'VALUES ($1, $2, $3, $4, $5, $6) RETURNING *',
        key_hash, prefix, user['id'], req.name, req.scopes, expires_at,
    )

    # Audit (self-service action still logged)
    await pool.execute(
        'INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) '
        "VALUES ('user', $1, 'key.created', $2, $3::jsonb)",
        str(user['id']), prefix, json.dumps({'name': req.name}),
    )

    return ApiKeyCreated(plaintext=raw, **_out(row))  # type: ignore[arg-type]


@router.get('')
async def list_keys(user: dict = Depends(get_current_user)) -> list[dict]:
    """Own keys only (prefix + last_used; never hashes/plaintext)."""
    pool = await get_pool()
    rows = await pool.fetch(
        'SELECT * FROM api_keys WHERE user_id = $1 ORDER BY created_at DESC',
        user['id'],
    )
    return [_out(r) for r in rows]


@router.delete('/{key_id}')
async def revoke_key(key_id: UUID, user: dict = Depends(get_current_user)) -> dict:
    """Revoke own key (or any key if admin)."""
    pool = await get_pool()
    row = await pool.fetchrow('SELECT * FROM api_keys WHERE id = $1', key_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Key not found')
    if str(row['user_id']) != str(user['id']) and user['role'] != 'admin':
        raise HTTPException(status_code=403, detail='Not your key')

    tag = await pool.execute(
        'UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL',
        key_id,
    )
    revoked = tag == 'UPDATE 1'
    if revoked:
        invalidate_user_cache(str(row['user_id']))
        await pool.execute(
            'INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) '
            "VALUES ('user', $1, 'key.revoked', $2, '{}')",
            str(user['id']), row['key_prefix'],
        )
    return {'ok': True, 'was_active': revoked}
