"""FastMCP Gateway — the main entry point.

Loads tools from tool_registry DB, exposes them via streamable-http with
API-key auth, proxies calls through Nango, and meters every call.

Phase 2: Redis client, abuse protection (rate limits, daily quota,
provider caps, circuit breaker) integrated into every tool call path.

Phase 6: DB-only tool registry, hot-reload via reload_registry tool,
per-user tool permissions (allowed_tools on API keys).

Run:
    python gateway/server.py
    # or
    uvicorn gateway.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
import redis.asyncio as redis
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_http_request

from .abuse import check_abuse
from .auth import authenticate_request
from .circuit_breaker import record_call_result
from .config import get_config
from .db import apply_schema, close_pool, init_pool
from .executor import execute_tool
from .metrics import record_tool_call
from .nango import close_client
from .registry import Registry, ToolDef

log = logging.getLogger(__name__)

# ── Lifespan context ─────────────────────────────────────────────────────────

@dataclass
class GatewayContext:
    pool: asyncpg.Pool
    registry: Registry
    redis: redis.Redis
    config: Any = field(default_factory=get_config)


@asynccontextmanager
async def gateway_lifespan(server: FastMCP) -> AsyncIterator[GatewayContext]:
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    log.info('Gateway starting up…')

    # Database
    pool = await init_pool(config.database_url)
    await apply_schema()

    # Redis
    redis_client = redis.from_url(config.redis_url, decode_responses=True)

    # Registry — tool_registry DB (Phase 6)
    registry = Registry()
    count = await registry.load_from_db(pool)
    if count == 0:
        log.error('No tools in tool_registry — run POST /api/v1/tools/import first')
    log.info('Loaded %d tools from tool_registry', count)

    ctx = GatewayContext(pool=pool, registry=registry, redis=redis_client, config=config)

    try:
        yield ctx
    finally:
        await redis_client.aclose()
        await close_client()
        await close_pool()
        log.info('Gateway shut down')


# ── Server (module-level export for FastMCP Cloud / uvicorn) ────────────────

mcp = FastMCP(
    name='Plugmere Gateway',
    lifespan=gateway_lifespan,
)


# ── Helper: get gateway context from a tool call ─────────────────────────────

def _get_ctx(context: Context) -> GatewayContext:
    return context.lifespan_context


def _get_authorization() -> str:
    """Read the API key from the HTTP Authorization header (never tool arguments)."""
    return get_http_request().headers.get('authorization', '')


async def _get_user_providers(pool: asyncpg.Pool, user_id: str) -> set[str]:
    """Return set of provider keys the user has access to (OAuth + API-key providers)."""
    rows = await pool.fetch(
        "SELECT DISTINCT provider FROM user_connections WHERE user_id = $1 AND status = 'active'",
        user_id,
    )
    providers = {r['provider'] for r in rows}

    key_rows = await pool.fetch(
        'SELECT DISTINCT provider FROM user_api_keys WHERE user_id = $1',
        user_id,
    )
    providers.update(r['provider'] for r in key_rows)

    return providers


# ── Health check resource ───────────────────────────────────────────────────

@mcp.resource('health://status')
async def health_check(context: Context) -> dict:
    """Gateway health: DB reachability, Redis, tool count."""
    ctx = _get_ctx(context)
    db_ok = False
    redis_ok = False

    try:
        async with ctx.pool.acquire() as conn:
            await conn.fetchval('SELECT 1')
        db_ok = True
    except Exception:
        pass

    try:
        await ctx.redis.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        'status': 'healthy' if (db_ok and redis_ok) else 'degraded',
        'tools_loaded': len(ctx.registry.tools),
        'providers': ctx.registry.providers(),
        'db': db_ok,
        'redis': redis_ok,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── Hot-reload tool registry from DB ────────────────────────────────────────

@mcp.tool()
async def reload_registry(context: Context) -> dict:
    """
    Reload the tool registry from the database.

    Call this after tools have been added, updated, or disabled
    via the control plane admin API. Takes effect immediately
    for the current gateway instance. All tools are accessible
    via the call_tool function.
    """
    ctx = _get_ctx(context)
    authorization = _get_authorization()
    auth_result = await authenticate_request(authorization, ctx.pool)
    if auth_result is None:
        return {'success': False, 'error': {'code': 'UNAUTHORIZED', 'message': 'Invalid or missing API key'}}
    row = await ctx.pool.fetchrow('SELECT role FROM users WHERE id = $1', auth_result[0])
    if row is None or row['role'] != 'admin':
        return {'success': False, 'error': {'code': 'FORBIDDEN', 'message': 'Admin only'}}
    count = await ctx.registry.load_from_db(ctx.pool)
    return {'success': True, 'tools_loaded': count, 'providers': ctx.registry.providers()}


@mcp.tool()
async def list_my_tools(context: Context) -> dict:
    """
    List tools you have access to based on your connected providers.

    Only returns tools for providers you've actually connected via OAuth
    or added API keys for. Call this first to know what's available.
    """
    ctx = _get_ctx(context)
    authorization = _get_authorization()
    auth_result = await authenticate_request(authorization, ctx.pool)
    if auth_result is None:
        return {'success': False, 'error': {'code': 'UNAUTHORIZED', 'message': 'Invalid or missing API key'}}

    user_id, _, _ = auth_result
    my_providers = await _get_user_providers(ctx.pool, user_id)

    my_tools = []
    for tool in ctx.registry.list_all():
        provider_key = tool.nango_provider_key or tool.provider
        if tool.provider in my_providers or provider_key in my_providers:
            my_tools.append({
                'name': tool.name,
                'provider': tool.provider,
                'description': tool.description[:120],
                'method': tool.method,
                'path': tool.path,
            })

    return {
        'success': True,
        'total': len(my_tools),
        'connected_providers': sorted(my_providers),
        'tools': my_tools,
    }


# ── Core tool handler (auth → permissions → abuse → execute → meter) ────────

async def _handle_tool_call(
    ctx: GatewayContext,
    tool_def: ToolDef,
    arguments: dict,
) -> dict:
    """Shared handler for all tool calls via call_tool."""
    start = time.monotonic()

    # 1. Authenticate (API key from HTTP header)
    authorization = _get_authorization()
    auth_result = await authenticate_request(authorization, ctx.pool)
    if auth_result is None:
        return {'success': False, 'error': {'code': 'UNAUTHORIZED', 'message': 'Invalid or missing API key'}}

    user_id, api_key_id, allowed_tools = auth_result
    if api_key_id is None:
        # OAuth caller: key rate limits on stable user_id, not the rotating token
        key_hash = f'oauth:{user_id}'
    else:
        raw_key = authorization.split(' ', 1)[1].strip() if ' ' in authorization else authorization
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # 1b. Connection check — user must have connected the provider
    my_providers = await _get_user_providers(ctx.pool, user_id)
    provider_key = tool_def.nango_provider_key or tool_def.provider
    if tool_def.provider not in my_providers and provider_key not in my_providers:
        return {'success': False, 'error': {
            'code': 'NOT_CONNECTED',
            'message': f"You haven't connected {tool_def.provider} yet. Go to Integrations to connect it.",
        }}

    # Resolve user email (Nango connections are keyed by email, not UUID)
    user_email = ''
    try:
        row = await ctx.pool.fetchrow('SELECT email FROM users WHERE id = $1', user_id)
        user_email = row['email'] if row else ''
    except Exception:
        pass

    # 2. Per-user tool permission check (exact tool name or provider prefix: "gmail" covers gmail_*)
    if allowed_tools and not any(
        tool_def.name == s or tool_def.name.startswith(s + '_') for s in allowed_tools
    ):
        return {'success': False, 'error': {'code': 'FORBIDDEN', 'message': f"Tool '{tool_def.name}' not allowed by this API key"}}

    # 3. Abuse protection (rate limits, daily quota, provider caps, circuit breaker)
    abuse_check = await check_abuse(
        redis_client=ctx.redis,
        pool=ctx.pool,
        config=ctx.config,
        key_hash=key_hash,
        user_id=user_id,
        tool_def=tool_def,
    )
    if abuse_check is not None:
        return abuse_check

    # 4. Execute via Nango (live connection lookup — no cached state)
    result = await execute_tool(
        nango_host=ctx.config.nango_host,
        nango_secret=ctx.config.nango_secret,
        user_id=user_id,
        user_email=user_email,
        tool_def=tool_def,
        arguments=arguments,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    is_error = not result.get('success', True)
    status = 'error' if is_error else 'success'

    # 5. Meter (fire-and-forget)
    try:
        await record_tool_call(
            pool=ctx.pool,
            user_id=user_id,
            api_key_id=api_key_id,
            tool_name=tool_def.name,
            provider=tool_def.provider,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception as e:
        log.warning('Metering write failed: %s', e)

    # 6. Circuit breaker — record error for provider tracking
    try:
        await record_call_result(
            r=ctx.redis,
            provider=tool_def.nango_provider_key,
            is_error=is_error,
            error_window_seconds=ctx.config.circuit_breaker_window,
        )
    except Exception as e:
        log.warning('Circuit breaker write failed: %s', e)

    return result


# ── Generic call_tool ───────────────────────────────────────────────────────

@mcp.tool()
async def call_tool(
    tool_name: str,
    arguments: dict | None = None,
    context: Context | None = None,
) -> dict:
    """
    Call any registered SaaS tool by name.

    Authentication uses the API key sent in the HTTP Authorization header.
    If the API key has allowed_tools set, only those tools can be called.

    Args:
        tool_name: Fully qualified tool name (e.g. gmail_getProfile, slack_postMessage)
        arguments: Tool-specific arguments (path params, query params, body)

    Returns:
        Tool execution result from the target SaaS provider via Nango proxy.
    """
    ctx = _get_ctx(context)

    tool_def = ctx.registry.get(tool_name)
    if tool_def is None:
        return {'success': False, 'error': {'code': 'NOT_FOUND', 'message': f"Tool '{tool_name}' not found"}}

    return await _handle_tool_call(ctx, tool_def, arguments or {})


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    mcp.run(transport='http', port=get_config().port)
