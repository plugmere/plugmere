"""Tools CRUD — admin endpoints for the tool_registry table."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import get_config
from ..db import get_pool
from ..deps import require_admin
from ..services.tool_import import import_openapi_spec, import_providers_json

router = APIRouter(prefix='/api/v1/tools', tags=['tools'])


# ── Schemas ──────────────────────────────────────────────────────────────────

class ToolPatch(BaseModel):
    description: str | None = None
    method: str | None = None
    path: str | None = None
    input_schema: dict[str, Any] | None = None
    required_scopes: list[str] | None = None
    enabled: bool | None = None
    public: bool | None = None
    tags: list[str] | None = None


class ToolOut(BaseModel):
    id: str
    provider: str
    name: str
    description: str
    method: str
    path: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    required_scopes: list[str]
    enabled: bool
    public: bool
    tags: list[str]
    version: int
    created_at: str
    updated_at: str


def _row_to_tool(row: Any) -> dict:
    input_schema = json.loads(row['input_schema']) if isinstance(row['input_schema'], str) else (row['input_schema'] or {})
    output_schema = (
        json.loads(row['output_schema']) if isinstance(row['output_schema'], str) else row['output_schema']
    ) if row['output_schema'] else None
    return ToolOut(
        id=str(row['id']),
        provider=row['provider'],
        name=row['name'],
        description=row['description'] or '',
        method=row['method'],
        path=row['path'],
        input_schema=input_schema,
        output_schema=output_schema,
        required_scopes=list(row['required_scopes'] or []),
        enabled=row['enabled'],
        public=row['public'],
        tags=list(row['tags'] or []),
        version=row['version'],
        created_at=row['created_at'].isoformat(),
        updated_at=row['updated_at'].isoformat(),
    ).model_dump()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get('')
async def list_tools(
    provider: str | None = Query(None),
    enabled: bool | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    _admin: dict = Depends(require_admin),
) -> list[dict]:
    pool = await get_pool()
    clauses: list[str] = []
    params: list[Any] = []
    idx = 1

    if provider:
        clauses.append(f'provider = ${idx}')
        params.append(provider)
        idx += 1
    if enabled is not None:
        clauses.append(f'enabled = ${idx}')
        params.append(enabled)
        idx += 1
    if q:
        clauses.append(f'(name ILIKE ${idx} OR description ILIKE ${idx})')
        params.append(f'%{q}%')
        idx += 1

    where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
    rows = await pool.fetch(
        f'SELECT * FROM tool_registry {where} ORDER BY provider, name LIMIT ${idx} OFFSET ${idx + 1}',
        *params,
        limit,
        offset,
    )
    return [_row_to_tool(r) for r in rows]


@router.get('/count')
async def tools_count(
    provider: str | None = Query(None),
    enabled: bool | None = Query(None),
    _admin: dict = Depends(require_admin),
) -> dict:
    pool = await get_pool()
    clauses: list[str] = []
    params: list[Any] = []
    idx = 1
    if provider:
        clauses.append(f'provider = ${idx}')
        params.append(provider)
        idx += 1
    if enabled is not None:
        clauses.append(f'enabled = ${idx}')
        params.append(enabled)
        idx += 1
    where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
    total = await pool.fetchval(f'SELECT count(*) FROM tool_registry {where}', *params)
    return {'total': total}


@router.patch('/{tool_id}')
async def patch_tool(
    tool_id: str,
    body: ToolPatch,
    admin: dict = Depends(require_admin),
) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT * FROM tool_registry WHERE id = $1', tool_id)
    if not row:
        raise HTTPException(404, 'Tool not found')

    updates: list[str] = []
    params: list[Any] = []
    idx = 1

    for field in ('description', 'method', 'path', 'required_scopes', 'tags'):
        val = getattr(body, field)
        if val is not None:
            updates.append(f'{field} = ${idx}')
            params.append(val)
            idx += 1

    if body.input_schema is not None:
        updates.append(f'input_schema = ${idx}')
        params.append(json.dumps(body.input_schema))
        idx += 1

    if body.enabled is not None:
        updates.append(f'enabled = ${idx}')
        params.append(body.enabled)
        idx += 1

    if body.public is not None:
        updates.append(f'public = ${idx}')
        params.append(body.public)
        idx += 1

    if not updates:
        return _row_to_tool(row)

    updates.append('version = version + 1')
    updates.append('updated_at = now()')
    params.append(tool_id)

    updated = await pool.fetchrow(
        f'UPDATE tool_registry SET {", ".join(updates)} WHERE id = ${idx} RETURNING *',
        *params,
    )

    # Audit log
    await pool.execute(
        "INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) "
        "VALUES ('admin', $1, 'tool.changed', $2, $3)",
        admin['id'],
        row['name'],
        json.dumps({'tool_id': tool_id, 'fields_changed': [f for f in body.model_fields_set if getattr(body, f) is not None]}),
    )

    return _row_to_tool(updated)


@router.delete('/{tool_id}')
async def delete_tool(tool_id: str, admin: dict = Depends(require_admin)) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow('SELECT name FROM tool_registry WHERE id = $1', tool_id)
    if not row:
        raise HTTPException(404, 'Tool not found')

    await pool.execute('DELETE FROM tool_registry WHERE id = $1', tool_id)
    await pool.execute(
        "INSERT INTO audit_log (actor_type, actor_id, action, target) VALUES ('admin', $1, 'tool.deleted', $2)",
        admin['id'],
        row['name'],
    )
    return {'deleted': row['name']}


@router.post('/import')
async def import_tools(admin: dict = Depends(require_admin)) -> dict:
    """Import/update tools from providers.json into the tool_registry table."""
    pool = await get_pool()
    cfg = get_config()
    try:
        result = await import_providers_json(pool, cfg.providers_json_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail='providers.json not found (it is not shipped with the repo). '
            'For a fresh install run instead: python scripts/seed_registry.py import',
        ) from None

    await pool.execute(
        "INSERT INTO audit_log (actor_type, actor_id, action, metadata) VALUES ('admin', $1, 'tool.imported', $2)",
        admin['id'],
        json.dumps(result),
    )
    return result


# ── OpenAPI spec import ─────────────────────────────────────────────────────

class OpenAPIImportRequest(BaseModel):
    provider: str
    spec: dict[str, Any]
    nango_provider_key: str | None = None


@router.post('/import-openapi')
async def import_openapi(body: OpenAPIImportRequest, admin: dict = Depends(require_admin)) -> dict:
    """Import tools from an OpenAPI/Swagger spec into the tool_registry table."""
    pool = await get_pool()
    result = await import_openapi_spec(
        pool=pool,
        provider=body.provider,
        spec=body.spec,
        nango_provider_key=body.nango_provider_key,
    )

    await pool.execute(
        "INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) "
        "VALUES ('admin', $1, 'tool.openapi_imported', $2, $3)",
        admin['id'],
        body.provider,
        json.dumps(result),
    )
    return result
