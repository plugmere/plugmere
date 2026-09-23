"""Webhook receiver for Nango events → syncs the user_connections read-model.

Per Nango docs (webhooks-from-nango):
- Signature: X-Nango-Hmac-Sha256 = hex(HMAC_SHA256(raw_body, webhook signing key)).
  (Legacy X-Nango-Signature is plain SHA-256 — not used.)
- Payload: {"type": "auth", "operation": "creation|override|refresh|deletion",
  "connectionId", "providerConfigKey", "provider", "success": bool,
  "tags": {"end_user_id", "end_user_email", ...}}
If no signing key configured, verification is skipped (dev mode).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway.auth import invalidate_providers_cache

from .. import db

log = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/connections', tags=['connections'])

# auth operations that mean "connection usable"
_ACTIVE_OPERATIONS = {'creation', 'override'}


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not secret:
        return True  # dev mode: unverified
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.lower())


@router.post('/webhook')
async def nango_webhook(request: Request):
    from ..config import get_config

    cfg = get_config()
    raw = await request.body()

    sig = request.headers.get('X-Nango-Hmac-Sha256')
    if not verify_signature(cfg.nango_webhook_signing_key, raw, sig):
        log.warning('Rejected Nango webhook: bad signature')
        return JSONResponse(status_code=401, content={'ok': False})

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — malformed body: 400, not 500 (avoid Nango retry-storms)
        log.warning('Rejected Nango webhook: malformed JSON body')
        return JSONResponse(status_code=400, content={'ok': False, 'error': 'malformed JSON'})
    event_type = (payload.get('type') or '').lower()
    operation = (payload.get('operation') or '').lower()
    success = payload.get('success', True)
    connection_id = payload.get('connectionId')
    provider = payload.get('providerConfigKey') or payload.get('provider')

    if not connection_id or not event_type:
        return {'ok': True, 'ignored': 'missing fields'}

    # Only auth webhooks matter for the read-model; ignore sync/etc gracefully.
    if event_type != 'auth':
        return {'ok': True, 'ignored': f'unhandled type {event_type}'}

    # Resolve our user: tags.end_user_id (= our user_id), fall back to email
    tags = payload.get('tags') or {}
    user_id = tags.get('end_user_id') or None
    if not user_id and tags.get('end_user_email'):
        user_id = await db.find_user_id_by_email(tags['end_user_email'])

    try:
        if operation == 'deletion':
            try:
                pool = await db.get_pool()
                owner = await pool.fetchval(
                    'SELECT user_id FROM user_connections WHERE nango_connection_id = $1',
                    str(connection_id),
                )
            except Exception:
                owner = None  # pool unavailable (tests/dev) — skip invalidation
            await db.delete_connection(str(connection_id))
            if owner:
                invalidate_providers_cache(str(owner))
            return {'ok': True, 'action': 'deleted'}

        if user_id is None:
            log.warning('Webhook %s/%s for %s: cannot resolve user', event_type, operation, connection_id)
            return {'ok': True, 'ignored': 'unresolvable user'}

        if operation in _ACTIVE_OPERATIONS or operation == 'refresh':
            status = 'active' if success else 'error'
            await db.upsert_connection(user_id, str(provider), str(connection_id), status)
            invalidate_providers_cache(str(user_id))
            return {'ok': True, 'action': status}
        return {'ok': True, 'ignored': f'unhandled operation {operation}'}
    except Exception as e:
        log.exception('Webhook processing failed: %s', e)
        return {'ok': False, 'error': str(e)}
