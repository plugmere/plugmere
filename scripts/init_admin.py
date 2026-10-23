#!/usr/bin/env python3
"""Create (or promote) the first admin user — replaces manual SQL on a fresh install.

Dashboard login refuses unknown emails ("No provisioned account for this
identity") and the users API is admin-guarded, so a brand-new install has no
way in. Run this once with your own email:

  python scripts/init_admin.py you@example.com "Your Name"

- applies the schema first (idempotent) — works on a completely empty database
- creates the user as an active admin if missing; promotes an existing member
- adds the user to invite_list so they keep OAuth-connector access after the
  first invite flips the system into invite-only mode (oauth.py:_invite_check)

DATABASE_URL comes from the environment or the root .env (env wins).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # gateway package importable from a bare clone

from gateway.db import SCHEMA_SQL  # noqa: E402

load_dotenv(ROOT / '.env')


async def init_admin(email: str, name: str = '') -> str:
    """Ensure an active admin exists for `email`. Returns what happened."""
    import os

    dsn = os.getenv('DATABASE_URL', '')
    if not dsn:
        raise SystemExit('DATABASE_URL not set — fill in the root .env first')

    email = email.strip().lower()
    if '@' not in email:
        raise SystemExit(f'Not an email address: {email}')

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SCHEMA_SQL)  # idempotent — empty DB works

        row = await conn.fetchrow('SELECT id, role, status FROM users WHERE email = $1', email)
        if row is None:
            user_id = await conn.fetchval(
                "INSERT INTO users (email, name, role, status) VALUES ($1, $2, 'admin', 'active') RETURNING id",
                email,
                name,
            )
            action = 'created as admin'
        elif row['role'] != 'admin' or row['status'] != 'active':
            user_id = row['id']
            await conn.execute(
                "UPDATE users SET role = 'admin', status = 'active', updated_at = now() WHERE id = $1",
                user_id,
            )
            action = f"promoted from {row['role']}/{row['status']} to admin/active"
        else:
            user_id = row['id']
            action = 'already an active admin'

        await conn.execute(
            'INSERT INTO invite_list (email, added_by) VALUES ($1, $2) ON CONFLICT DO NOTHING',
            email,
            user_id,
        )
    finally:
        await conn.close()

    return action


def main() -> None:
    parser = argparse.ArgumentParser(description='Create or promote the first admin user')
    parser.add_argument('email', help='admin email (must match the Google account you log in with)')
    parser.add_argument('name', nargs='?', default='', help='display name (optional)')
    args = parser.parse_args()

    import asyncio

    action = asyncio.run(init_admin(args.email, args.name))
    print(f'{args.email}: {action}')
    print('Next: python scripts/seed_registry.py import   (load the curated tool menu)')


if __name__ == '__main__':
    main()
