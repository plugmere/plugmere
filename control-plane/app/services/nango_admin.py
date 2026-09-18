"""Nango admin API client — sessions, connections CRUD. HTTP only, no CLI."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _get_client(host: str, secret: str) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=host,
            headers={'Authorization': f'Bearer {secret}'},
            timeout=httpx.Timeout(30.0),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def create_connect_session(
    host: str,
    secret_key: str,
    *,
    end_user_id: str,
    end_user_email: str,
    end_user_name: str | None = None,
    webhook_url_override: str | None = None,
    allowed_integrations: list[str] | None = None,
) -> dict[str, Any]:
    """
    Create a Nango Connect session. Returns {'token', 'expires_at', 'connect_url'}.

    Per docs (POST /connect/sessions): server-side endpoint, Bearer SECRET key.
    `end_user` is deprecated — identity goes in `tags` (reconciles auth webhooks).
    `webhook_url_override` routes THIS connection's webhooks to our endpoint,
    bypassing environment-level webhook URLs (handy for local dev).
    """
    payload: dict[str, Any] = {
        'tags': {
            'end_user_id': end_user_id,
            'end_user_email': end_user_email,
        },
    }
    if webhook_url_override:
        payload['webhook_url_override'] = webhook_url_override
    if allowed_integrations:
        payload['allowed_integrations'] = allowed_integrations

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f'{host}/connect/sessions',
            json=payload,
            headers={'Authorization': f'Bearer {secret_key}'},
        )
        resp.raise_for_status()
        data = resp.json()['data']
        connect_link = data.get('connect_link', '')
        if connect_link and 'apiURL=' not in connect_link:
            separator = '&' if '?' in connect_link else '?'
            connect_link = f'{connect_link}{separator}apiURL={host}'
        return {
            'token': data['token'],
            'expires_at': data.get('expires_at'),
            'connect_url': connect_link,
        }


async def list_connections(host: str, secret: str, *, user_id: str | None = None) -> list[dict[str, Any]]:
    """List connections from Nango (optionally filtered by our userId in tags)."""
    client = _get_client(host, secret)
    resp = await client.get('/connections')
    resp.raise_for_status()
    conns = resp.json().get('connections', [])
    if user_id:
        conns = [c for c in conns if c.get('tags', {}).get('end_user_id') == user_id]
    return conns


async def delete_connection(host: str, secret: str, nango_connection_id: str, provider_config_key: str) -> None:
    """Delete a connection (revokes stored tokens server-side)."""
    client = _get_client(host, secret)
    resp = await client.delete(f'/connections/{nango_connection_id}', params={'provider_config_key': provider_config_key})
    resp.raise_for_status()


async def trigger_sync_if_needed() -> None:  # placeholder for later phases
    """Reserved: post-connect backfills (Phase 4+)."""
    return
