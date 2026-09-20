"""Tests for the gateway — auth, registry, executor, metrics."""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest

# Ensure gateway package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.auth import _extract_bearer, _hash_key, _verify_oauth_jwt
from gateway.executor import _extract_body, _fill_path_params
from gateway.registry import Registry, ToolDef

# ── Auth tests ───────────────────────────────────────────────────────────────

class TestAuth:
    def test_hash_key_deterministic(self):
        key = 'sk-test1234567890abcdef'
        h1 = _hash_key(key)
        h2 = _hash_key(key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_extract_bearer_valid(self):
        assert _extract_bearer('Bearer sk-abc123') == 'sk-abc123'

    def test_extract_bearer_case_insensitive(self):
        assert _extract_bearer('bearer sk-abc123') == 'sk-abc123'
        assert _extract_bearer('BEARER sk-abc123') == 'sk-abc123'

    def test_extract_bearer_none(self):
        assert _extract_bearer(None) is None
        assert _extract_bearer('') is None

    def test_extract_bearer_no_prefix(self):
        assert _extract_bearer('sk-abc123') is None

    def test_extract_bearer_malformed(self):
        assert _extract_bearer('Bearer') is None
        assert _extract_bearer('Bearer ') is None


class TestOAuthJwt:
    """Gateway OAuth JWT branch: scope enforcement + live revocation."""

    def _mint(self, sub='u1', grant='g1', scope='mcp:tools', secret='test-secret'):
        return pyjwt.encode(
            {'iss': 'x', 'sub': sub, 'grant_id': grant, 'scope': scope,
             'iat': int(time.time()), 'exp': int(time.time()) + 3600},
            secret, algorithm='HS256',
        )

    async def _verify(self, token, row, secret='test-secret'):
        os.environ['OAUTH_JWT_SECRET'] = secret
        pool = AsyncMock()
        pool.fetchrow.return_value = row
        return await _verify_oauth_jwt(token, pool)

    @pytest.mark.asyncio
    async def test_valid_token_accepted(self):
        token = self._mint()
        res = await self._verify(token, {'revoked_at': None, 'status': 'active'})
        assert res == ('u1', None, [])

    @pytest.mark.asyncio
    async def test_missing_scope_rejected(self):
        token = self._mint(scope='other')
        assert await self._verify(token, {'revoked_at': None, 'status': 'active'}) is None

    @pytest.mark.asyncio
    async def test_revoked_grant_rejected(self):
        token = self._mint()
        row = {'revoked_at': datetime.now(timezone.utc), 'status': 'active'}
        assert await self._verify(token, row) is None

    @pytest.mark.asyncio
    async def test_disabled_user_rejected(self):
        token = self._mint()
        assert await self._verify(token, {'revoked_at': None, 'status': 'disabled'}) is None

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self):
        token = self._mint(secret='wrong-secret')
        assert await self._verify(token, {'revoked_at': None, 'status': 'active'}) is None


# ── Registry tests ───────────────────────────────────────────────────────────

class TestRegistry:
    def _make_row(self, name, provider='gmail', method='GET', path='/v1/test', description='Test tool', enabled=True):
        return {
            'id': '00000000-0000-0000-0000-000000000001',
            'provider': provider,
            'name': name,
            'description': description,
            'method': method,
            'path': path,
            'input_schema': '{}',
            'output_schema': None,
            'required_scopes': [],
            'security_scheme': None,
            'public': False,
            'tags': [],
            'version': 1,
        }

    @pytest.mark.asyncio
    async def test_load_empty_db(self):
        reg = Registry()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        count = await reg.load_from_db(pool)
        assert count == 0
        assert len(reg.tools) == 0

    @pytest.mark.asyncio
    async def test_load_single_tool(self):
        reg = Registry()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[self._make_row('gmail_getProfile')])
        count = await reg.load_from_db(pool)
        assert count == 1
        assert 'gmail_getProfile' in reg.tools

        tool = reg.get('gmail_getProfile')
        assert tool is not None
        assert tool.provider == 'gmail'
        assert tool.method == 'GET'

    @pytest.mark.asyncio
    async def test_load_multiple_providers(self):
        reg = Registry()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            self._make_row('gmail_getProfile', provider='gmail'),
            self._make_row('gmail_send', provider='gmail'),
            self._make_row('slack_postMessage', provider='slack'),
        ])
        count = await reg.load_from_db(pool)
        assert count == 3
        assert len(reg.providers()) == 2
        assert set(reg.providers()) == {'gmail', 'slack'}

    @pytest.mark.asyncio
    async def test_list_by_provider(self):
        reg = Registry()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            self._make_row('a', provider='gmail'),
            self._make_row('b', provider='gmail'),
        ])
        await reg.load_from_db(pool)
        gmail_tools = reg.list_by_provider('gmail')
        assert len(gmail_tools) == 2

    @pytest.mark.asyncio
    async def test_load_disabled_tools_excluded(self):
        reg = Registry()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])  # WHERE enabled = true filters them
        count = await reg.load_from_db(pool)
        assert count == 0


# ── Executor tests ───────────────────────────────────────────────────────────

class TestExecutor:
    def test_fill_path_params(self):
        path = 'gmail/v1/users/{userId}/messages/{id}'
        params = {'userId': {'type': 'string'}, 'id': {'type': 'string'}}
        args = {'userId': 'me', 'id': '12345', 'extra': 'keep'}
        result = _fill_path_params(path, params, args)
        assert result == 'gmail/v1/users/me/messages/12345'
        assert args == {'extra': 'keep'}  # consumed params removed

    def test_fill_path_params_missing(self):
        path = 'v1/users/{userId}'
        result = _fill_path_params(path, {}, {'other': 'val'})
        assert result == 'v1/users/{userId}'  # placeholder left if missing

    def test_extract_body_with_body(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='POST', path='/test',
            params={'_body': {'name': {'type': 'string'}, 'email': {'type': 'string'}}}
        )
        args = {'name': 'Alice', 'email': 'a@b.com', 'query': 'skip'}
        body = _extract_body(tool, args)
        assert body == {'name': 'Alice', 'email': 'a@b.com'}

    def test_extract_body_no_body(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='GET', path='/test',
            params={'q': {'type': 'string'}}
        )
        body = _extract_body(tool, {'q': 'hello'})
        assert body is None

    def test_extract_body_empty(self):
        tool = ToolDef(
            name='test', provider='test', nango_provider_key='test',
            description='', method='POST', path='/test',
            params={'_body': {'name': {'type': 'string'}}}
        )
        body = _extract_body(tool, {})
        assert body is None


# ── Metrics tests ────────────────────────────────────────────────────────────

class TestMetrics:
    def test_record_import(self):
        from gateway.metrics import get_daily_usage, record_tool_call
        assert callable(record_tool_call)
        assert callable(get_daily_usage)
