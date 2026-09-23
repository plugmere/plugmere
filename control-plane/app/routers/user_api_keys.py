"""User API keys — store/retrieve API keys for API-key providers (Maps, SerpAPI, etc.)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gateway.auth import invalidate_providers_cache

from ..db import get_pool
from ..deps import get_current_user

router = APIRouter(prefix='/api/v1/user-api-keys', tags=['user-api-keys'])


class SaveApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class ApiKeyResponse(BaseModel):
    provider: str
    has_key: bool
    created_at: str | None = None


@router.get('/')
async def list_api_keys(user: dict = Depends(get_current_user)) -> list[ApiKeyResponse]:
    """List all API keys for the current user (does NOT return actual keys)."""
    pool = await get_pool()
    rows = await pool.fetch(
        'SELECT provider, created_at FROM user_api_keys WHERE user_id = $1',
        user['id'],
    )
    return [
        ApiKeyResponse(
            provider=r['provider'],
            has_key=True,
            created_at=r['created_at'].isoformat() if r['created_at'] else None,
        )
        for r in rows
    ]


@router.get('/{provider}')
async def get_api_key(provider: str, user: dict = Depends(get_current_user)) -> dict:
    """Get API key for a provider (masked)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        'SELECT api_key, created_at FROM user_api_keys WHERE user_id = $1 AND provider = $2',
        user['id'], provider,
    )
    if not row:
        raise HTTPException(status_code=404, detail='No API key for this provider')
    key = row['api_key']
    masked = key[:4] + '****' + key[-4:] if len(key) > 8 else '****'
    return {
        'provider': provider,
        'api_key_masked': masked,
        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
    }


@router.post('/')
async def save_api_key(req: SaveApiKeyRequest, user: dict = Depends(get_current_user)) -> dict:
    """Save or update API key for a provider."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO user_api_keys (user_id, provider, api_key, updated_at)
           VALUES ($1, $2, $3, now())
           ON CONFLICT (user_id, provider)
           DO UPDATE SET api_key = $3, updated_at = now()""",
        user['id'], req.provider, req.api_key,
    )
    invalidate_providers_cache(str(user['id']))
    return {'status': 'ok', 'provider': req.provider}


@router.delete('/{provider}')
async def delete_api_key(provider: str, user: dict = Depends(get_current_user)) -> dict:
    """Delete API key for a provider."""
    pool = await get_pool()
    result = await pool.execute(
        'DELETE FROM user_api_keys WHERE user_id = $1 AND provider = $2',
        user['id'], provider,
    )
    if result == 'DELETE 0':
        raise HTTPException(status_code=404, detail='No API key for this provider')
    invalidate_providers_cache(str(user['id']))
    return {'status': 'deleted', 'provider': provider}
