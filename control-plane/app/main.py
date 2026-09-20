"""Control plane — FastAPI app.

Phase 3: connections (Connect sessions, webhook sync, catalog).
Phase 4: auth (Supabase JWT), users, API keys, metrics — all admin-guarded
server-side; audit log on every admin/self-service action.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_config
from .db import apply_schema, close_pool, get_pool
from .routers import (
    api_keys,
    auth,
    connections,
    connections_api,
    metrics,
    oauth,
    tools,
    user_api_keys,
    users,
)
from .services import catalog as catalog_svc
from .services import nango_admin

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
    log.info('Control plane starting…')
    await apply_schema()
    try:
        yield
    finally:
        await nango_admin.aclose()
        await close_pool()
        log.info('Control plane shut down')


app = FastAPI(
    title='Plugmere Control',
    description='Every plug in its place.',
    lifespan=lifespan,
)

# CORS allowlist — UI origin only (§3.13)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_config().ui_url],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(connections.router)
app.include_router(connections_api.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(tools.router)
app.include_router(metrics.router)
app.include_router(user_api_keys.router)
app.include_router(oauth.router)


@app.get('/api/v1/health')
async def health() -> dict:
    return {
        'status': 'ok',
        'service': 'control-plane',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


@app.get('/api/v1/catalog')
async def provider_catalog() -> list[dict]:
    """Provider catalog for the dashboard Integrations page (live from tool_registry DB)."""
    cfg = get_config()
    pool = await get_pool()
    return await catalog_svc.load_catalog_from_db(pool, cfg.nango_host, cfg.nango_secret)


if __name__ == '__main__':
    import os

    import uvicorn

    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('CONTROL_PLANE_PORT', '8001')))
