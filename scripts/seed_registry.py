#!/usr/bin/env python3
"""Seed the tool registry — moves the curated tool menu between DB and file.

tool_registry (Postgres) is the source of truth, but a database doesn't ship
with a git clone: a fresh install starts empty ("No tools in tool_registry").
This script exports the menu to a committed seed file, and imports it back:

  maintainer — dump the live registry to the seed file:
      python scripts/seed_registry.py export

  self-hoster — load the seed into a fresh database (run ONCE before/after
  starting the server; applies the schema first, so an empty DB works):
      python scripts/seed_registry.py import

Seed file: seed/tool_registry.json (committed to the repo).
DATABASE_URL comes from the environment or the root .env (env wins).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # gateway package importable from a bare clone

from gateway.db import SCHEMA_SQL  # noqa: E402

load_dotenv(ROOT / '.env')

DEFAULT_SEED = ROOT / 'seed' / 'tool_registry.json'

EXPORT_SQL = """
    SELECT provider, name, description, method, path,
           input_schema, output_schema, required_scopes, security_scheme,
           enabled, public, tags, version
      FROM tool_registry
  ORDER BY provider, name
"""

UPSERT_SQL = """
    INSERT INTO tool_registry
        (provider, name, description, method, path, input_schema, output_schema,
         required_scopes, security_scheme, enabled, public, tags, version)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
    ON CONFLICT (name) DO UPDATE SET
        provider = EXCLUDED.provider,
        description = EXCLUDED.description,
        method = EXCLUDED.method,
        path = EXCLUDED.path,
        input_schema = EXCLUDED.input_schema,
        output_schema = EXCLUDED.output_schema,
        required_scopes = EXCLUDED.required_scopes,
        security_scheme = EXCLUDED.security_scheme,
        enabled = EXCLUDED.enabled,
        public = EXCLUDED.public,
        tags = EXCLUDED.tags,
        version = EXCLUDED.version,
        updated_at = now()
"""


def _parse_json(value: object) -> object:
    """asyncpg may return JSONB as str or as a parsed object — accept both."""
    return json.loads(value) if isinstance(value, str) else value


def normalize_tool(record: object) -> dict:
    """Convert a tool_registry row (asyncpg Record or mapping) to a seed entry."""
    rec = dict(record)  # type: ignore[arg-type]
    return {
        'provider': rec['provider'],
        'name': rec['name'],
        'description': rec['description'] or '',
        'method': rec['method'],
        'path': rec['path'],
        'input_schema': _parse_json(rec['input_schema']) or {},
        'output_schema': _parse_json(rec['output_schema']) if rec['output_schema'] else None,
        'required_scopes': list(rec['required_scopes'] or []),
        'security_scheme': _parse_json(rec['security_scheme']) if rec['security_scheme'] else None,
        'enabled': bool(rec['enabled']),
        'public': bool(rec['public']),
        'tags': list(rec['tags'] or []),
        'version': int(rec['version']),
    }


def seed_args(tool: dict) -> tuple:
    """Build one executemany argument tuple (JSONB columns as JSON text)."""
    return (
        tool['provider'],
        tool['name'],
        tool.get('description', ''),
        tool.get('method', 'GET'),
        tool.get('path', ''),
        json.dumps(tool.get('input_schema') or {}),
        json.dumps(tool['output_schema']) if tool.get('output_schema') else None,
        list(tool.get('required_scopes') or []),
        json.dumps(tool['security_scheme']) if tool.get('security_scheme') else None,
        bool(tool.get('enabled', True)),
        bool(tool.get('public', False)),
        list(tool.get('tags') or []),
        int(tool.get('version', 1)),
    )


def load_seed(path: Path) -> list[dict]:
    """Read + validate the seed file. Fails loudly on truncation/garbage."""
    data = json.loads(path.read_text(encoding='utf-8'))
    tools = data.get('tools') if isinstance(data, dict) else data
    if not isinstance(tools, list) or not tools:
        raise ValueError(f'{path}: no tools found — re-export from a populated registry')
    count = data.get('count') if isinstance(data, dict) else None
    if count is not None and count != len(tools):
        raise ValueError(f'{path}: count mismatch ({count} declared, {len(tools)} present) — file truncated?')
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict) or not tool.get('name') or not tool.get('provider'):
            raise ValueError(f'{path}: entry #{i} missing name/provider')
    return tools


def _dsn() -> str:
    import os

    dsn = os.getenv('DATABASE_URL', '')
    if not dsn:
        raise SystemExit('DATABASE_URL not set — fill in the root .env first')
    return dsn


async def export_seed(out_path: Path) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        records = await conn.fetch(EXPORT_SQL)
    finally:
        await conn.close()
    if not records:
        raise SystemExit('tool_registry is empty — nothing to export (run the import first?)')
    tools = [normalize_tool(r) for r in records]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {'exported_at': datetime.now(UTC).isoformat(), 'count': len(tools), 'tools': tools},
            indent=1,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    providers = len({t['provider'] for t in tools})
    return {'exported': len(tools), 'providers': providers, 'path': str(out_path)}


async def import_seed(seed_path: Path) -> dict:
    tools = load_seed(seed_path)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(SCHEMA_SQL)  # idempotent — makes an empty DB work
        async with conn.transaction():
            await conn.executemany(UPSERT_SQL, [seed_args(t) for t in tools])
        providers = await conn.fetchval('SELECT count(DISTINCT provider) FROM tool_registry')
        total = await conn.fetchval('SELECT count(*) FROM tool_registry')
    finally:
        await conn.close()
    return {'imported': len(tools), 'providers': providers, 'registry_total': total}


def main() -> None:
    parser = argparse.ArgumentParser(description='Export/import the curated tool_registry seed file')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_export = sub.add_parser('export', help='dump the live registry -> seed/tool_registry.json')
    p_export.add_argument('--out', type=Path, default=DEFAULT_SEED)
    p_import = sub.add_parser('import', help='load seed/tool_registry.json -> this DATABASE_URL')
    p_import.add_argument('--file', type=Path, default=DEFAULT_SEED)
    args = parser.parse_args()

    import asyncio

    if args.cmd == 'export':
        result = asyncio.run(export_seed(args.out))
        print(f"Exported {result['exported']} tools ({result['providers']} providers) -> {result['path']}")
    else:
        result = asyncio.run(import_seed(args.file))
        print(
            f"Imported {result['imported']} tools from seed — "
            f"registry now holds {result['registry_total']} tools across {result['providers']} providers"
        )


if __name__ == '__main__':
    main()
