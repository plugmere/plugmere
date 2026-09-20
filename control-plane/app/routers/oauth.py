"""Gateway OAuth (Phase 8) — MCP-spec-compliant authorization server.

Works with ALL MCP clients: Claude, ChatGPT, Cursor, VS Code, opencode, Inspector.
Follows MCP 2026-07-28 spec: DCR, CIMD, PKCE S256, form-encoded token endpoint,
/.well-known/oauth-protected-resource, resource param binding.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from ..config import get_config
from ..db import get_pool
from ..deps import get_current_user, require_admin

log = logging.getLogger(__name__)

router = APIRouter(tags=['oauth'])

ACCESS_TTL = 3600  # 1 hour
CODE_TTL = 600  # 10 minutes


# ── Known client redirect URIs (MCP spec clients) ─────────────────────────
# When clients don't send a redirect_uri, we must pick one from this list.
# Each client uses different URIs — we accept all of them.

KNOWN_REDIRECT_URIS: dict[str, str] = {
    'claude': 'https://claude.ai/api/mcp/auth_callback',
    'chatgpt': 'https://chatgpt.com/connector_platform_oauth_redirect',
    'cursor': 'https://www.cursor.com/agents/mcp/oauth/callback',
    'vscode': 'https://vscode.dev/redirect',
}


# ── Schemas ────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    client_name: str = ''
    redirect_uris: list[str]
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None


class ApproveRequest(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str = ''
    code_challenge: str | None = None
    state: str = ''


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
        return  # open mode
    row = await pool.fetchrow('SELECT 1 FROM invite_list WHERE email = $1', email.lower())
    if row is None:
        raise HTTPException(status_code=403, detail='Not invited — ask the admin for access')


def _parse_form_or_json(request: Request, body: bytes) -> dict:
    """Parse token endpoint body — form-urlencoded OR JSON."""
    content_type = request.headers.get('content-type', '')
    if 'application/json' in content_type:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status_code=400, detail='Invalid JSON body')
    # Default: form-urlencoded
    from urllib.parse import parse_qs
    parsed = parse_qs(body.decode())
    # parse_qs returns lists; unwrap single values
    return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}


def _validate_redirect_uri(uri: str, allowed: list[str]) -> bool:
    """Validate redirect URI. Accept exact matches + port-agnostic loopback per RFC 8252."""
    if uri in allowed:
        return True
    # Port-agnostic loopback: http://127.0.0.1:<any_port>/path or http://localhost:<any_port>/path
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    if parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'):
        for allowed_uri in allowed:
            allowed_parsed = urlparse(allowed_uri)
            if (allowed_parsed.scheme == parsed.scheme
                    and allowed_parsed.hostname in ('127.0.0.1', 'localhost')
                    and allowed_parsed.path == parsed.path):
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  Discovery endpoints (all MCP clients read these)
# ═══════════════════════════════════════════════════════════════════════════

@router.get('/.well-known/oauth-protected-resource')
async def protected_resource_metadata(request: Request) -> dict:
    """RFC 9728 — MCP clients discover the auth server from here."""
    cfg = get_config()
    base = cfg.app_url.rstrip('/')
    resource_url = str(request.url).split('.well-known')[0].rstrip('/')
    return {
        'resource': resource_url,
        'authorization_servers': [base],
        'scopes_supported': ['mcp:tools'],
        'bearer_methods_supported': ['header'],
    }


@router.get('/.well-known/oauth-authorization-server')
async def oauth_metadata() -> dict:
    """RFC 8414 — authorization server metadata."""
    cfg = get_config()
    base = cfg.app_url.rstrip('/')
    return {
        'issuer': base,
        'authorization_endpoint': f'{base}/api/v1/oauth/authorize',
        'token_endpoint': f'{base}/api/v1/oauth/token',
        'registration_endpoint': f'{base}/api/v1/oauth/register',
        'scopes_supported': ['mcp:tools'],
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['none', 'client_secret_post'],
        'client_id_metadata_document_supported': False,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Dynamic Client Registration (DCR)
# ═══════════════════════════════════════════════════════════════════════════

@router.post('/api/v1/oauth/register', status_code=201)
async def register_client(req: RegisterRequest) -> dict:
    if not req.redirect_uris:
        raise HTTPException(status_code=422, detail='redirect_uris required')

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
    log.info('OAuth client registered: %s (%s)', req.client_name[:50], client_id[:12])
    return {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uris': req.redirect_uris,
        'grant_types': req.grant_types or ['authorization_code', 'refresh_token'],
        'response_types': req.response_types or ['code'],
        'token_endpoint_auth_method': req.token_endpoint_auth_method or 'none',
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Client info (approve page looks up name)
# ═══════════════════════════════════════════════════════════════════════════

@router.get('/api/v1/oauth/client/{client_id}/info')
async def client_info(client_id: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT client_id, name FROM oauth_clients WHERE client_id = $1', client_id)
    if row is None:
        # CIMD client (URL-based client_id) — not in our DB, return generic info
        if client_id.startswith('http'):
            return {'client_id': client_id, 'name': 'External MCP Client'}
        raise HTTPException(status_code=404, detail='Unknown client')
    return {'client_id': row['client_id'], 'name': row['name']}


# ═══════════════════════════════════════════════════════════════════════════
#  Authorization endpoint — redirects to dashboard approve page
# ═══════════════════════════════════════════════════════════════════════════

@router.get('/api/v1/oauth/authorize')
async def authorize_redirect(
    client_id: str = '',
    redirect_uri: str = '',
    scope: str = '',
    state: str = '',
    code_challenge: str = '',
    code_challenge_method: str = '',
    response_type: str = '',
    resource: str = '',
) -> RedirectResponse:
    cfg = get_config()
    ui = cfg.ui_url.rstrip('/')
    params = urlencode({k: v for k, v in [
        ('client_id', client_id),
        ('redirect_uri', redirect_uri),
        ('scope', scope),
        ('state', state),
        ('code_challenge', code_challenge),
        ('resource', resource),
    ] if v})
    return RedirectResponse(f'{ui}/oauth/approve?{params}')


# ═══════════════════════════════════════════════════════════════════════════
#  Approve — logged-in user consents, we mint an auth code
# ═══════════════════════════════════════════════════════════════════════════

@router.post('/api/v1/oauth/approve')
async def approve(req: ApproveRequest, user: dict = Depends(get_current_user)) -> dict:
    pool = await get_pool()

    await _invite_check(user['email'])

    client = await pool.fetchrow('SELECT * FROM oauth_clients WHERE client_id = $1', req.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail='Unknown client')

    redirect_uris: list = list(client['redirect_uris'] or [])
    if not _validate_redirect_uri(req.redirect_uri, redirect_uris):
        raise HTTPException(status_code=422, detail='redirect_uri not registered for this client')

    scopes = req.scope.split() if req.scope else ['mcp:tools']
    code = 'ac-' + secrets.token_urlsafe(32)
    await pool.execute(
        'INSERT INTO oauth_codes (code, client_id, user_id, redirect_uri, code_challenge, scopes, expires_at) '
        'VALUES ($1, $2, $3, $4, $5, $6, now() + make_interval(secs => $7))',
        code, req.client_id, user['id'], req.redirect_uri, req.code_challenge, scopes, CODE_TTL,
    )

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


# ═══════════════════════════════════════════════════════════════════════════
#  Token endpoint — accepts form-urlencoded (MCP spec requirement)
# ═══════════════════════════════════════════════════════════════════════════

@router.post('/api/v1/oauth/token')
async def token(request: Request) -> dict:
    body = await request.body()
    data = _parse_form_or_json(request, body)
    pool = await get_pool()

    grant_type = data.get('grant_type', '')
    client_id = data.get('client_id', '')
    client_secret = data.get('client_secret')

    if grant_type == 'authorization_code':
        code = data.get('code', '')
        redirect_uri = data.get('redirect_uri', '')
        code_verifier = data.get('code_verifier', '')

        if not code or not redirect_uri or not client_id:
            raise HTTPException(status_code=422, detail='code, redirect_uri, client_id required')

        row = await pool.fetchrow(
            "SELECT * FROM oauth_codes WHERE code = $1 AND used_at IS NULL AND expires_at > now()",
            code,
        )
        if row is None:
            raise HTTPException(status_code=400, detail='Invalid or expired code')
        if row['client_id'] != client_id:
            raise HTTPException(status_code=400, detail='Code/client mismatch')

        # Validate redirect_uri (port-agnostic loopback per RFC 8252)
        client = await pool.fetchrow('SELECT * FROM oauth_clients WHERE client_id = $1', client_id)
        if client is None:
            raise HTTPException(status_code=400, detail='Unknown client')
        allowed_uris = list(client['redirect_uris'] or [])
        if not _validate_redirect_uri(redirect_uri, allowed_uris):
            raise HTTPException(status_code=400, detail='redirect_uri mismatch')

        # Client secret check (only if server issued one AND client sent one)
        if client['client_secret'] and client_secret:
            if hashlib.sha256(client_secret.encode()).hexdigest() != client['client_secret']:
                raise HTTPException(status_code=401, detail='Bad client_secret')

        # PKCE check
        if row['code_challenge']:
            if not code_verifier:
                raise HTTPException(status_code=400, detail='code_verifier required')
            digest = hashlib.sha256(code_verifier.encode()).digest()
            expected = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
            if expected != row['code_challenge']:
                raise HTTPException(status_code=400, detail='PKCE mismatch')

        await pool.execute('UPDATE oauth_codes SET used_at = now() WHERE code = $1', code)

        grant = await pool.fetchrow(
            "SELECT * FROM oauth_grants WHERE user_id = $1 AND client_id = $2 AND revoked_at IS NULL",
            row['user_id'], client_id,
        )
        if grant is None:
            raise HTTPException(status_code=400, detail='Grant revoked')

        refresh_raw, refresh_hash = _new_refresh_token()
        await pool.execute(
            'UPDATE oauth_grants SET refresh_hash = $1, last_used_at = now() WHERE id = $2',
            refresh_hash, grant['id'],
        )
        access = _mint_access_token(str(row['user_id']), str(grant['id']), list(grant['scopes'] or []))
        return {
            'access_token': access,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TTL,
            'refresh_token': refresh_raw,
            'scope': ' '.join(grant['scopes'] or []),
        }

    if grant_type == 'refresh_token':
        refresh_token = data.get('refresh_token', '')
        if not refresh_token or not client_id:
            raise HTTPException(status_code=422, detail='refresh_token, client_id required')
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        grant = await pool.fetchrow(
            "SELECT * FROM oauth_grants WHERE client_id = $1 AND refresh_hash = $2 AND revoked_at IS NULL",
            client_id, digest,
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

    raise HTTPException(status_code=400, detail=f"Unsupported grant_type '{grant_type}'")


# ═══════════════════════════════════════════════════════════════════════════
#  Connected apps (self-service list + revoke)
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
#  Invite list (admin)
# ═══════════════════════════════════════════════════════════════════════════

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
