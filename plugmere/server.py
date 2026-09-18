"""Plugmere API — merged backend (one process, one port, one Render service).

FastAPI control plane is the outer app; the FastMCP gateway mounts inside:

    /mcp        → FastMCP gateway (LLM agents: call_tool, list_my_tools)
    /api/v1/*   → FastAPI control plane (dashboard: users, keys, tools, metrics)
    /           → service info (humans, Render health checks)

Two things make the nesting work:
1. The MCP mount is registered LAST so /api/v1/* matches the control
   plane first (Starlette matches routes in order).
2. The parent runs a COMBINED lifespan (control-plane + MCP). FastMCP
   requires its lifespan on the parent — without it the streamable-HTTP
   session manager never starts and every /mcp call 500s.

Run locally:
    uvicorn plugmere.server:app --host 0.0.0.0 --port 8000

Render start command:
    uvicorn plugmere.server:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'control-plane'))

from app.main import app  # noqa: E402
from app.main import lifespan as control_plane_lifespan  # noqa: E402

from gateway.server import mcp  # noqa: E402

mcp_app = mcp.http_app()


@asynccontextmanager
async def combined_lifespan(parent_app) -> AsyncIterator[None]:
    """Run control-plane lifespan (schema, pools) + MCP lifespan (pool,
    Redis, 1447-tool registry, streamable-HTTP session manager)."""
    async with control_plane_lifespan(parent_app):
        async with mcp_app.lifespan(parent_app):
            yield


app.router.lifespan_context = combined_lifespan


@app.get('/', include_in_schema=False)
async def root() -> dict:
    return {
        'service': 'plugmere-api',
        'status': 'ok',
        'mcp': '/mcp',
        'api': '/api/v1/health',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# Mounted LAST — catches only what /api/v1/* and / don't.
app.mount('/', mcp_app)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
