"""Abuse monitoring (#10) — scheduled rules check + Brevo email alerts.

GitHub Actions cron POSTs here every 15 min with the shared cron secret
(ALERTS_CRON_SECRET header). Rules run over tool_calls + oauth_clients;
any violation goes out as one email via Brevo. Notify-only — no auto-block.

v1 rules:
- error_spike: >20 errors in last 15 min (any provider)
- provider_down: any provider with 100% errors on >=5 calls in last 15 min
- dcr_spam: >10 DCR client registrations in last hour
- quota_hot: any user over 80% of daily quota
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import get_config
from ..db import get_pool

log = logging.getLogger(__name__)

router = APIRouter(tags=['alerts'])

ERROR_SPIKE_THRESHOLD = 20
DCR_SPAM_THRESHOLD = 10
QUOTA_PCT = 0.8


async def _send_brevo(subject: str, body: str, to: str | None = None) -> bool:
    cfg = get_config()
    recipient = to or cfg.alert_to
    if not cfg.brevo_api_key or not recipient:
        log.warning('Alerts misconfigured: BREVO_API_KEY/ALERT_TO missing')
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                'https://api.brevo.com/v3/smtp/email',
                headers={'api-key': cfg.brevo_api_key, 'Content-Type': 'application/json'},
                json={
                    'sender': {'email': cfg.alert_to, 'name': 'Plugmere Alerts'},
                    'to': [{'email': recipient}],
                    'subject': subject,
                    'textContent': body,
                },
            )
            r.raise_for_status()
        return True
    except Exception as e:
        log.error('Brevo send failed: %s', e)
        return False


async def _probe(url: str, timeout_s: float = 10.0) -> tuple[bool, int]:
    """GET a health URL. Returns (ok, latency_ms)."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url)
            ms = int((time.monotonic() - start) * 1000)
            return (r.status_code < 500, ms)
    except Exception:
        return (False, int((time.monotonic() - start) * 1000))


async def _record_status(pool, service: str, ok: bool, latency_ms: int) -> None:
    await pool.execute(
        'INSERT INTO status_checks (service, ok, latency_ms) VALUES ($1, $2, $3)',
        service, ok, latency_ms,
    )
    # Prune history older than 90 days
    await pool.execute("DELETE FROM status_checks WHERE created_at < now() - make_interval(days => 90)")


@router.post('/api/v1/alerts/check')
async def check_alerts(request: Request, test: bool = False) -> dict:
    cfg = get_config()
    secret = request.headers.get('X-Cron-Secret', '')
    if not cfg.alerts_cron_secret or secret != cfg.alerts_cron_secret:
        raise HTTPException(status_code=401, detail='Bad cron secret')

    if test:
        sent = await _send_brevo('[Plugmere] test alert', 'Test fire — the alarm works. No action needed.')
        return {'ok': True, 'test': True, 'emailed': sent}

    pool = await get_pool()
    findings: list[str] = []

    # 1. Error spike (last 15 min)
    errors_15m = await pool.fetchval(
        "SELECT count(*) FROM tool_calls WHERE status = 'error' AND created_at > now() - make_interval(mins => 15)"
    )
    if errors_15m and errors_15m >= ERROR_SPIKE_THRESHOLD:
        findings.append(f'error_spike: {errors_15m} errors in last 15 min (threshold {ERROR_SPIKE_THRESHOLD})')

    # 2. Provider fully failing (>=5 calls, 100% errors, last 15 min)
    dead = await pool.fetch(
        "SELECT provider, count(*) AS calls, count(*) FILTER (WHERE status = 'error') AS errors "
        'FROM tool_calls WHERE created_at > now() - make_interval(mins => 15) '
        'GROUP BY provider HAVING count(*) >= 5 AND count(*) FILTER (WHERE status = \'error\') = count(*)'
    )
    for r in dead:
        findings.append(f"provider_down: {r['provider']} — {r['calls']} calls, all errors (15 min)")

    # 3. DCR spam (last hour)
    dcr_1h = await pool.fetchval(
        'SELECT count(*) FROM oauth_clients WHERE created_at > now() - make_interval(hours => 1)'
    )
    if dcr_1h and dcr_1h >= DCR_SPAM_THRESHOLD:
        findings.append(f'dcr_spam: {dcr_1h} client registrations in last hour (threshold {DCR_SPAM_THRESHOLD})')

    # 4. Quota hot (any user over 80% of DAILY_QUOTA today)
    quota = int(os.getenv('DAILY_QUOTA', '5000'))
    hot = await pool.fetch(
        'SELECT u.email, count(*) AS calls FROM tool_calls t JOIN users u ON u.id = t.user_id '
        "WHERE t.created_at > date_trunc('day', now()) "
        'GROUP BY u.email HAVING count(*) >= $1',
        int(quota * QUOTA_PCT),
    )
    for r in hot:
        findings.append(f"quota_hot: {r['email']} at {r['calls']}/{quota} today")

    if findings:
        body = 'Plugmere abuse monitor fired:\n\n' + '\n'.join(f'- {f}' for f in findings)
        sent = await _send_brevo(f'[Plugmere] {len(findings)} alert(s)', body)
        # Notify status-page subscribers on provider outages
        if any(f.startswith('provider_down') for f in findings):
            subs = await pool.fetch('SELECT email FROM status_subscribers')
            for s in subs:
                await _send_brevo(
                    '[Plugmere] Service disruption',
                    'One or more providers are failing:\n\n' + '\n'.join(f'- {f}' for f in findings
                        if f.startswith('provider_down')) + '\n\nLive status: ' + cfg.app_url.replace('api', 'status'),
                    to=s['email'],
                )
        await pool.execute(
            "INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) "
            "VALUES ('system', 'alerts', 'alerts.fired', $1, '{}')",
            '; '.join(findings)[:500],
        )

    # Status history probes (every run, findings or not)
    api_ok, api_ms = True, 0  # this endpoint ran = API alive
    await _record_status(pool, 'api', api_ok, api_ms)
    try:
        db_start = time.monotonic()
        await pool.fetchval('SELECT 1')
        await _record_status(pool, 'neon', True, int((time.monotonic() - db_start) * 1000))
    except Exception:
        await _record_status(pool, 'neon', False, 0)
    nango_ok, nango_ms = await _probe(cfg.nango_host.rstrip('/') + '/health')
    await _record_status(pool, 'nango', nango_ok, nango_ms)

    if findings:
        return {'ok': True, 'fired': findings, 'emailed': sent}

    return {'ok': True, 'fired': []}


# ── Public status API (no auth — feeds the status page) ────────────────────

@router.get('/api/v1/status')
async def status_current() -> dict:
    """Latest probe per service + overall."""
    pool = await get_pool()
    rows = await pool.fetch(
        'SELECT DISTINCT ON (service) service, ok, latency_ms, created_at '
        'FROM status_checks ORDER BY service, created_at DESC'
    )
    services = {
        r['service']: {
            'ok': r['ok'], 'latency_ms': r['latency_ms'],
            'checked_at': r['created_at'].isoformat(),
        }
        for r in rows
    }
    return {
        'overall': 'up' if services and all(s['ok'] for s in services.values()) else 'degraded',
        'services': services,
    }


@router.get('/api/v1/status/history')
async def status_history(days: int = 7) -> dict:
    """Per-day up/down counts per service for the history strip."""
    days = max(1, min(days, 90))
    pool = await get_pool()
    rows = await pool.fetch(
        'SELECT service, date_trunc(\'day\', created_at)::date AS day, '
        'count(*) AS checks, count(*) FILTER (WHERE ok) AS ups '
        'FROM status_checks WHERE created_at > now() - make_interval(days => $1) '
        'GROUP BY service, day ORDER BY day',
        days,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r['service'], []).append({
            'day': r['day'].isoformat(), 'checks': r['checks'], 'ups': r['ups'],
        })
    return {'days': days, 'history': out}


@router.get('/api/v1/status/incidents')
async def status_incidents(limit: int = 20) -> list[dict]:
    """Recent fired alerts from the audit log = incident timeline."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT target, created_at FROM audit_log WHERE action = 'alerts.fired' "
        'ORDER BY created_at DESC LIMIT $1',
        max(1, min(limit, 100)),
    )
    return [{'summary': r['target'], 'at': r['created_at'].isoformat()} for r in rows]


@router.get('/api/v1/status/providers')
async def status_providers() -> list[dict]:
    """Per-provider health from recent traffic (last 1h): calls, errors, state."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT provider, count(*) AS calls, count(*) FILTER (WHERE status = 'error') AS errors, "
        'max(created_at) AS last_seen FROM tool_calls '
        "WHERE created_at > now() - make_interval(hours => 1) GROUP BY provider ORDER BY provider"
    )
    return [
        {
            'provider': r['provider'], 'calls': r['calls'], 'errors': r['errors'],
            'error_rate': (r['errors'] / r['calls']) if r['calls'] else 0,
            'state': 'down' if r['calls'] >= 5 and r['errors'] == r['calls'] else (
                'flaky' if r['errors'] > 0 else 'up'),
            'last_seen': r['last_seen'].isoformat(),
        }
        for r in rows
    ]


class SubscribeRequest(BaseModel):
    email: str


@router.post('/api/v1/status/subscribe', status_code=201)
async def status_subscribe(req: SubscribeRequest) -> dict:
    pool = await get_pool()
    email = req.email.lower().strip()
    if '@' not in email:
        raise HTTPException(status_code=422, detail='Invalid email')
    await pool.execute('INSERT INTO status_subscribers (email) VALUES ($1) ON CONFLICT DO NOTHING', email)
    return {'ok': True}


@router.post('/api/v1/status/unsubscribe')
async def status_unsubscribe(req: SubscribeRequest) -> dict:
    pool = await get_pool()
    await pool.execute('DELETE FROM status_subscribers WHERE email = $1', req.email.lower().strip())
    return {'ok': True}
