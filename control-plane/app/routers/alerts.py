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

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import get_config
from ..db import get_pool

log = logging.getLogger(__name__)

router = APIRouter(tags=['alerts'])

ERROR_SPIKE_THRESHOLD = 20
DCR_SPAM_THRESHOLD = 10
QUOTA_PCT = 0.8


async def _send_brevo(subject: str, body: str) -> bool:
    cfg = get_config()
    if not cfg.brevo_api_key or not cfg.alert_to:
        log.warning('Alerts misconfigured: BREVO_API_KEY/ALERT_TO missing')
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                'https://api.brevo.com/v3/smtp/email',
                headers={'api-key': cfg.brevo_api_key, 'Content-Type': 'application/json'},
                json={
                    'sender': {'email': cfg.alert_to, 'name': 'Plugmere Alerts'},
                    'to': [{'email': cfg.alert_to}],
                    'subject': subject,
                    'textContent': body,
                },
            )
            r.raise_for_status()
        return True
    except Exception as e:
        log.error('Brevo send failed: %s', e)
        return False


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
        await pool.execute(
            "INSERT INTO audit_log (actor_type, actor_id, action, target, metadata) "
            "VALUES ('system', 'alerts', 'alerts.fired', $1, '{}')",
            '; '.join(findings)[:500],
        )
        return {'ok': True, 'fired': findings, 'emailed': sent}

    return {'ok': True, 'fired': []}
