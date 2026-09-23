"""Users management (admin-only)."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from gateway.auth import invalidate_user_cache

from ..db import get_pool
from ..deps import require_admin
from ..schemas import UserCreate, UserPatch

router = APIRouter(prefix='/api/v1/users', tags=['users'])


async def _audit(actor: dict, action: str, target: str, metadata: dict | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        'INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) '
        "VALUES ('user', $1, $2, $3, $4::jsonb)",
        str(actor['id']), action, target, json.dumps(metadata or {}),
    )


@router.get('')
async def list_users(
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: dict = Depends(require_admin),
) -> list[dict]:
    pool = await get_pool()
    if q:
        rows = await pool.fetch(
            'SELECT id, email, name, role, status, created_at, updated_at FROM users '
            'WHERE email ILIKE $1 OR name ILIKE $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3',
            f'%{q}%', min(limit, 500), offset,
        )
    else:
        rows = await pool.fetch(
            'SELECT id, email, name, role, status, created_at, updated_at FROM users '
            'ORDER BY created_at DESC LIMIT $1 OFFSET $2',
            min(limit, 500), offset,
        )
    return [{**dict(r), 'id': str(r['id'])} for r in rows]


@router.post('', status_code=201)
async def create_user(req: UserCreate, actor: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    try:
        row = await pool.fetchrow(
            'INSERT INTO users (email, name, role) VALUES ($1, $2, $3) RETURNING *',
            str(req.email), req.name, req.role,
        )
    except asyncpg.UniqueViolationError as e:
        raise HTTPException(status_code=409, detail='Email already exists') from e
    await _audit(actor, 'user.created', str(req.email), {'role': req.role})
    return {**dict(row), 'id': str(row['id'])}


@router.patch('/{user_id}')
async def patch_user(user_id: UUID, req: UserPatch, actor: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()

    # Guard: last active admin cannot demote/disable self into lockout
    row_before = await pool.fetchrow('SELECT role, status FROM users WHERE id = $1', user_id)
    if row_before and row_before['role'] == 'admin' and (
        (req.role and req.role != 'admin') or req.status == 'disabled'
    ):
        admins = await pool.fetchval(
            "SELECT count(*) FROM users WHERE role = 'admin' AND status = 'active' AND id <> $1",
            user_id,
        )
        if admins == 0:
            raise HTTPException(status_code=409, detail='Cannot remove the last active admin')

    row = await pool.fetchrow(
        'UPDATE users SET '
        'name = COALESCE($2, name), '
        "role = COALESCE($3, role), "
        "status = COALESCE($4, status), "
        'updated_at = now() '
        'WHERE id = $1 RETURNING *',
        user_id, req.name, req.role, req.status,
    )
    if row is None:
        raise HTTPException(status_code=404, detail='User not found')

    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    if 'status' in changes and changes['status'] == 'disabled':
        invalidate_user_cache(str(user_id))
        await _audit(actor, 'user.disabled', str(user_id), changes)
    else:
        await _audit(actor, 'user.updated', str(user_id), changes)
    return {**dict(row), 'id': str(row['id'])}


@router.delete('/{user_id}')
async def delete_user(user_id: UUID, actor: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT email FROM users WHERE id = $1', user_id)
    if row is None:
        raise HTTPException(status_code=404, detail='User not found')
    await pool.execute('DELETE FROM users WHERE id = $1', user_id)
    invalidate_user_cache(str(user_id))
    await _audit(actor, 'user.deleted', str(user_id), {'email': row['email']})
    return {'ok': True}
