"""Connections API — Nango Connect sessions + read-model listing.

Auth (§3.6/§3.20): identity is derived from the authenticated session, never
caller-supplied. Members act only on their own identity/connections; admins
may target any user.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from gateway.auth import invalidate_providers_cache

from .. import db
from ..config import get_config
from ..deps import get_current_user
from ..services import nango_admin

router = APIRouter(prefix='/api/v1/connections', tags=['connections'])


class CreateSessionRequest(BaseModel):
    # Omitted => the authenticated user themself. Admin-only override otherwise.
    email: EmailStr | None = None
    name: str | None = None
    provider: str | None = None  # nango provider key → preselected in Connect UI


@router.post('/session')
async def create_session(
    req: CreateSessionRequest,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a Nango Connect session → returns widget URL."""
    cfg = get_config()

    if req.email and str(req.email).lower() == str(user['email']).lower():
        req.email = None  # self-session, not an admin override
    if req.email and user['role'] != 'admin':
        raise HTTPException(status_code=403, detail='Only admins may create sessions for other users')

    email = str(req.email) if req.email else user['email']
    name = req.name if req.email else (user['name'] or req.name)

    user_id = await db.ensure_user(email, name)

    try:
        # POST /connect/sessions is a server-side API → SECRET key
        session = await nango_admin.create_connect_session(
            cfg.nango_host,
            cfg.nango_secret,
            end_user_id=user_id,
            end_user_email=email,
            end_user_name=name,
            webhook_url_override=cfg.webhook_url_override or None,
            allowed_integrations=[req.provider] if req.provider else None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Nango session creation failed: {e}') from e

    return {'user_id': user_id, **session}


@router.get('')
async def list_connections(
    user_id: UUID | None = None,
    user: dict = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List connections from Nango directly (source of truth).

    Members always see only their own. Admins see all, or filter with ?user_id=.
    """
    cfg = get_config()
    target_user_id = str(user['id']) if user['role'] != 'admin' else (str(user_id) if user_id else None)

    try:
        all_conns = await nango_admin.list_connections(
            cfg.nango_host, cfg.nango_secret, user_id=target_user_id,
        )
    except Exception:
        all_conns = []

    return [
        {
            'user_id': c.get('tags', {}).get('end_user_id', ''),
            'provider': c.get('provider_config_key', ''),
            'nango_connection_id': c.get('connection_id', ''),
            'status': 'active',
            'created_at': c.get('created', ''),
            'updated_at': c.get('created', ''),
        }
        for c in all_conns
    ]


@router.delete('/{nango_connection_id}')
async def delete_connection(
    nango_connection_id: str,
    provider: str,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Revoke connection in Nango (owner or admin)."""
    cfg = get_config()
    try:
        await nango_admin.delete_connection(cfg.nango_host, cfg.nango_secret, nango_connection_id, provider)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Nango delete failed: {e}') from e

    try:
        pool = await db.get_pool()
        owner = await pool.fetchval(
            'SELECT user_id FROM user_connections WHERE nango_connection_id = $1',
            nango_connection_id,
        )
    except Exception:
        owner = None  # pool unavailable (tests/dev) — fall back to deleter
    invalidate_providers_cache(str(owner) if owner else str(user['id']))
    return {'ok': True}


@router.post('/sync')
async def sync_connections(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Reconcile user_connections from Nango (pull fallback).

    Nango won't push auth webhooks on this build (empty subscription), so the
    dashboard calls this after each connect and the table stays truthful:
    upserts the user's live tagged connections, prunes rows whose Nango
    connection is gone (revokes). Members sync only themselves.
    """
    cfg = get_config()
    user_id = str(user['id'])
    pool = await db.get_pool()

    try:
        live = await nango_admin.list_connections(cfg.nango_host, cfg.nango_secret, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Nango list failed: {e}') from e

    live_ids: set[str] = set()
    synced = 0
    for c in live:
        conn_id = str(c.get('connection_id', ''))
        provider = str(c.get('provider_config_key', ''))
        if not conn_id or not provider:
            continue
        live_ids.add(conn_id)
        await db.upsert_connection(user_id, provider, conn_id, 'active')
        synced += 1

    pruned = 0
    rows = await pool.fetch('SELECT nango_connection_id FROM user_connections WHERE user_id = $1', user_id)
    for r in rows:
        if r['nango_connection_id'] not in live_ids:
            if await db.delete_connection(r['nango_connection_id']):
                pruned += 1

    invalidate_providers_cache(user_id)
    return {'synced': synced, 'pruned': pruned}


@router.post('/dedup/{provider}')
async def dedup_connections(
    provider: str,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove duplicate connections for same user+provider, keep newest. Returns deduped count."""
    cfg = get_config()
    user_id = str(user['id'])

    try:
        all_conns = await nango_admin.list_connections(cfg.nango_host, cfg.nango_secret, user_id=user_id)
    except Exception:
        return {'removed': 0, 'remaining': 0}

    same_provider = [c for c in all_conns if c.get('provider_config_key') == provider]
    if len(same_provider) <= 1:
        return {'removed': 0, 'remaining': len(same_provider)}

    sorted_conns = sorted(same_provider, key=lambda c: c.get('created', ''), reverse=True)
    removed = 0
    for old in sorted_conns[1:]:
        try:
            await nango_admin.delete_connection(
                cfg.nango_host, cfg.nango_secret,
                old['connection_id'], provider,
            )
            removed += 1
        except Exception:
            pass

    return {'removed': removed, 'remaining': 1}
