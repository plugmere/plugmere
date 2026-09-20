"""Gateway OAuth (Phase 8) — authorize/token/DCR/metadata + grants + invites.

Flow: ChatGPT registers (DCR) → redirects user to dashboard /oauth/approve
(login + consent in one page) → frontend POSTs here with Supabase JWT →
we check invite list, mint a code, return the redirect URL → ChatGPT
exchanges code for tokens → gateway accepts our JWT as a second auth branch.

Access tokens are HS256 JWTs signed with OAUTH_JWT_SECRET, carrying
user_id + grant_id. The gateway verifies with the same secret and emits
the same (user_id, api_key_id, allowed_tools) tuple as API keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import get_config
from ..db import get_pool
from ..deps import get_current_user, require_admin

log = logging.getLogger(__name__)

router = APIRouter(tags=['oauth'])

ACCESS_TTL = 3600  # 1 hour
REFRESH_TTL_DAYS = 30
CODE_TTL = 600  # 10 minutes


# ── Schemas ────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    client_name: str = ''
    redirect_uris: list[str]


class ApproveRequest(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str = ''
    code_challenge: str | None = None
    state: str = ''


class TokenRequest(BaseModel):
    grant_type: str
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    refresh_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


class InviteAdd(BaseModel):
    email: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _oauth_secret() -> str:
    import os

    secret = os.getenv('OAUTH_JWT_SECRET', '')
    if not secret:
        raise HTTPException(status_code=500, detail='OAUTH_JWT_SECRET not configured')
    return secret


def _mint_access_token(user_id: str, grant_id: str, scopes: list[str]) -> str:
    cfg = get_config()
    now = int(time.time())
    return jwt.encode(
        {
            'iss': cfg.app_url,
            'sub': user_id,
            'grant_id': grant_id,
            'scope': ' '.join(scopes),
            'iat': now,
            'exp': now + ACCESS_TTL,
        },
        _oauth_secret(),
        algorithm='HS256',
    )


def _new_refresh_token() -> tuple[str, str]:
    raw = 'rt-' + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


async def _audit(actor_id: str, action: str, target: str, metadata: dict | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        'INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) '
        "VALUES ('user', $1, $2, $3, $4::jsonb)",
        actor_id, action, target, json.dumps(metadata or {}),
    )


async def _invite_check(email: str) -> None:
    """Invite-only gate: if the list is non-empty, email must be on it."""
    pool = await get_pool()
    total = await pool.fetchval('SELECT count(*) FROM invite_list')
    if total == 0:
        return  # open mode — no list, anyone in
    row = await pool.fetchrow('SELECT 1 FROM invite_list WHERE email = $1', email.lower())
    if row is None:
        raise HTTPException(status_code=403, detail='Not invited — ask the admin for access')


# ── Discovery metadata (ChatGPT reads this) ────────────────────────────────

@router.get('/.well-known/oauth-authorization-server')
async def oauth_metadata() -> dict:
    cfg = get_config()
    base = cfg.app_url.rstrip('/')
    return {
        'issuer': base,
        'authorization_endpoint': f'{base}/api/v1/oauth/authorize',
        'token_endpoint': f'{base}/api/v1/oauth/token',
        'registration_endpoint': f'{base}/api/v1/oauth/register',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'none'],
    }


# ── Dynamic client registration (public, rate-limited by gateway abuse layer later) ──

@router.post('/api/v1/oauth/register', status_code=201)
async def register_client(req: RegisterRequest) -> dict:
    if not req.redirect_uris:
        raise HTTPException(status_code=422, detail='redirect_uris required')
    for uri in req.redirect_uris:
        if not (uri.startswith('https://') or uri.startswith('http://localhost')):
            raise HTTPException(status_code=422, detail=f'Insecure redirect_uri rejected: {uri}')

    client_id = 'cli-' + secrets.token_urlsafe(16)
    client_secret = 'cls-' + secrets.token_urlsafe(32)
    pool = await get_pool()
    await pool.execute(
        'INSERT INTO oauth_clients (client_id, client_secret, name, redirect_uris) '
        'VALUES ($1, $2, $3, $4)',
        client_id,
        hashlib.sha256(client_secret.encode()).hexdigest(),
        req.client_name[:100],
        req.redirect_uris,
    )
    log.info('OAuth client registered: %s', req.client_name[:50])
    return {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uris': req.redirect_uris,
    }


# ── Client info (approve page looks up name) ────────────────────────────────

@router.get('/api/v1/oauth/client/{client_id}/info')
async def client_info(client_id: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT client_id, name FROM oauth_clients WHERE client_id = $1', client_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Unknown client')
    return {'client_id': row['client_id'], 'name': row['name']}


# ── Approve (logged-in user + invite check + consent → auth code) ───────────

@router.get('/api/v1/oauth/authorize')
async def authorize_redirect(
    client_id: str = '',
    redirect_uri: str = '',
    scope: str = '',
    state: str = '',
    code_challenge: str = '',
    response_type: str = '',
) -> RedirectResponse:
    """OAuth authorization endpoint — redirects to dashboard approve page."""
    cfg = get_config()
    ui = cfg.ui_url.rstrip('/')
    params = '&'.join(f'{k}={v}' for k, v in [
        ('client_id', client_id),
        ('redirect_uri', redirect_uri),
        ('scope', scope),
        ('state', state),
        ('code_challenge', code_challenge),
    ] if v)
    return RedirectResponse(f'{ui}/oauth/approve?{params}')


@router.post('/api/v1/oauth/approve')
async def approve(req: ApproveRequest, user: dict = Depends(get_current_user)) -> dict:
    pool = await get_pool()

    # Invite gate
    await _invite_check(user['email'])

    # Client + redirect validation
    client = await pool.fetchrow('SELECT * FROM oauth_clients WHERE client_id = $1', req.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail='Unknown client')
    redirect_uris: list = list(client['redirect_uris'] or [])
    if req.redirect_uri not in redirect_uris:
        raise HTTPException(status_code=422, detail='redirect_uri not registered for this client')

    scopes = req.scope.split() if req.scope else []
    code = 'ac-' + secrets.token_urlsafe(32)
    await pool.execute(
        'INSERT INTO oauth_codes (code, client_id, user_id, redirect_uri, code_challenge, scopes, expires_at) '
        'VALUES ($1, $2, $3, $4, $5, $6, now() + make_interval(secs => $7))',
        code, req.client_id, user['id'], req.redirect_uri, req.code_challenge, scopes, CODE_TTL,
    )

    # Upsert grant (one per user+client; revoked grants stay dead)
    grant = await pool.fetchrow(
        "SELECT id FROM oauth_grants WHERE user_id = $1 AND client_id = $2 AND revoked_at IS NULL",
        user['id'], req.client_id,
    )
    if grant is None:
        grant = await pool.fetchrow(
            'INSERT INTO oauth_grants (user_id, client_id, scopes) VALUES ($1, $2, $3) RETURNING id',
            user['id'], req.client_id, scopes,
        )
    grant_id = str(grant['id'])

    await _audit(str(user['id']), 'oauth.grant', req.client_id, {'scopes': scopes})
    sep = '&' if '?' in req.redirect_uri else '?'
    redirect = f'{req.redirect_uri}{sep}code={code}'
    if req.state:
        redirect += f'&state={req.state}'
    return {'redirect_to': redirect, 'grant_id': grant_id}


# ── Token endpoint (code exchange + refresh) ───────────────────────────────

@router.post('/api/v1/oauth/token')
async def token(req: TokenRequest) -> dict:
    pool = await get_pool()

    if req.grant_type == 'authorization_code':
        if not req.code or not req.redirect_uri or not req.client_id:
            raise HTTPException(status_code=422, detail='code, redirect_uri, client_id required')
        row = await pool.fetchrow(
            "SELECT * FROM oauth_codes WHERE code = $1 AND used_at IS NULL AND expires_at > now()",
            req.code,
        )
        if row is None:
            raise HTTPException(status_code=400, detail='Invalid or expired code')
        if row['client_id'] != req.client_id or row['redirect_uri'] != req.redirect_uri:
            raise HTTPException(status_code=400, detail='Code/client mismatch')

        client = await pool.fetchrow('SELECT * FROM oauth_clients WHERE client_id = $1', req.client_id)
        if client is None:
            raise HTTPException(status_code=400, detail='Unknown client')
        if client['client_secret'] and req.client_secret:
            if hashlib.sha256(req.client_secret.encode()).hexdigest() != client['client_secret']:
                raise HTTPException(status_code=401, detail='Bad client_secret')

        # PKCE check (required when a challenge was sent)
        if row['code_challenge']:
            if not req.code_verifier:
                raise HTTPException(status_code=400, detail='code_verifier required')
            digest = hashlib.sha256(req.code_verifier.encode()).digest()
            import base64

            expected = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
            if expected != row['code_challenge']:
                raise HTTPException(status_code=400, detail='PKCE mismatch')

        await pool.execute('UPDATE oauth_codes SET used_at = now() WHERE code = $1', req.code)

        grant = await pool.fetchrow(
            "SELECT * FROM oauth_grants WHERE user_id = $1 AND client_id = $2 AND revoked_at IS NULL",
            row['user_id'], req.client_id,
        )
        if grant is None:
            raise HTTPException(status_code=400, detail='Grant revoked')
        grant_id = str(grant['id'])
        user_id = str(row['user_id'])
        scopes = list(grant['scopes'] or [])

        refresh_raw, refresh_hash = _new_refresh_token()
        await pool.execute(
            'UPDATE oauth_grants SET refresh_hash = $1, last_used_at = now() WHERE id = $2',
            refresh_hash, grant['id'],
        )
        access = _mint_access_token(user_id, grant_id, scopes)
        return {
            'access_token': access,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TTL,
            'refresh_token': refresh_raw,
            'scope': ' '.join(scopes),
        }

    if req.grant_type == 'refresh_token':
        if not req.refresh_token or not req.client_id:
            raise HTTPException(status_code=422, detail='refresh_token, client_id required')
        digest = hashlib.sha256(req.refresh_token.encode()).hexdigest()
        grant = await pool.fetchrow(
            "SELECT * FROM oauth_grants WHERE client_id = $1 AND refresh_hash = $2 AND revoked_at IS NULL",
            req.client_id, digest,
        )
        if grant is None:
            raise HTTPException(status_code=400, detail='Invalid refresh token')
        user = await pool.fetchrow("SELECT * FROM users WHERE id = $1 AND status = 'active'", grant['user_id'])
        if user is None:
            raise HTTPException(status_code=403, detail='Account disabled')

        # Rotation: new refresh each use
        refresh_raw, refresh_hash = _new_refresh_token()
        await pool.execute(
            'UPDATE oauth_grants SET refresh_hash = $1, last_used_at = now() WHERE id = $2',
            refresh_hash, grant['id'],
        )
        access = _mint_access_token(str(grant['user_id']), str(grant['id']), list(grant['scopes'] or []))
        return {
            'access_token': access,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TTL,
            'refresh_token': refresh_raw,
            'scope': ' '.join(grant['scopes'] or []),
        }

    raise HTTPException(status_code=400, detail=f"Unsupported grant_type '{req.grant_type}'")


# ── Connected apps (self-service list + revoke) ────────────────────────────

@router.get('/api/v1/oauth/grants')
async def list_grants(user: dict = Depends(get_current_user)) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        'SELECT g.id, g.client_id, c.name, g.scopes, g.created_at, g.last_used_at '
        'FROM oauth_grants g JOIN oauth_clients c ON c.client_id = g.client_id '
        "WHERE g.user_id = $1 AND g.revoked_at IS NULL ORDER BY g.created_at DESC",
        user['id'],
    )
    return [
        {
            'id': str(r['id']),
            'client_id': r['client_id'],
            'name': r['name'],
            'scopes': list(r['scopes'] or []),
            'created_at': r['created_at'].isoformat() if isinstance(r['created_at'], datetime) else r['created_at'],
            'last_used_at': r['last_used_at'].isoformat() if isinstance(r['last_used_at'], datetime) else r['last_used_at'],
        }
        for r in rows
    ]


@router.delete('/api/v1/oauth/grants/{grant_id}')
async def revoke_grant(grant_id: UUID, user: dict = Depends(get_current_user)) -> dict:
    pool = await get_pool()
    tag = await pool.execute(
        'UPDATE oauth_grants SET revoked_at = now(), refresh_hash = NULL '
        'WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL',
        grant_id, user['id'],
    )
    if tag != 'UPDATE 1':
        raise HTTPException(status_code=404, detail='Grant not found')
    await _audit(str(user['id']), 'oauth.revoke', str(grant_id), {})
    return {'ok': True}


# ── Invite list (admin) ────────────────────────────────────────────────────

@router.get('/api/v1/invites')
async def list_invites(_: dict = Depends(require_admin)) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch('SELECT email, created_at FROM invite_list ORDER BY created_at DESC')
    return [{'email': r['email'], 'created_at': r['created_at'].isoformat()} for r in rows]


@router.post('/api/v1/invites', status_code=201)
async def add_invite(req: InviteAdd, actor: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    await pool.execute(
        'INSERT INTO invite_list (email, added_by) VALUES ($1, $2) ON CONFLICT DO NOTHING',
        req.email.lower().strip(), actor['id'],
    )
    await _audit(str(actor['id']), 'invite.added', req.email.lower().strip(), {})
    return {'ok': True, 'email': req.email.lower().strip()}


@router.delete('/api/v1/invites/{email}')
async def remove_invite(email: str, actor: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    await pool.execute('DELETE FROM invite_list WHERE email = $1', email.lower())
    await _audit(str(actor['id']), 'invite.removed', email.lower(), {})
    return {'ok': True}
