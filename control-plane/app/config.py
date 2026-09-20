"""Control plane configuration."""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f'Required env var {name} is not set')
    return val


class Config:
    def __init__(self) -> None:
        self.database_url = _require('DATABASE_URL')
        self.nango_host = os.getenv('NANGO_HOST', 'http://localhost:3003')
        self.nango_secret = _require('NANGO_SECRET')
        self.nango_public_key = os.getenv('NANGO_PUBLIC_KEY', '')
        self.nango_webhook_signing_key = os.getenv('NANGO_WEBHOOK_SIGNING_KEY', '')
        self.providers_json_path = os.getenv(
            'PROVIDERS_JSON_PATH',
            # Phase 1 reads old stack's file; Phase 6 migrates to tool_registry DB.
            os.path.join(os.path.dirname(__file__), '..', '..', 'nango-mcp', 'providers.json'),
        )
        self.ui_url = os.getenv('UI_URL', 'http://localhost:5173')
        self.status_url = os.getenv('STATUS_URL', '')
        self.app_url = os.getenv('APP_URL', 'http://host.docker.internal:8001')
        # Optional per-session webhook routing; older Nango builds reject the key,
        # so only sent when explicitly set (deploy-time: our public control-plane URL).
        self.webhook_url_override = os.getenv('WEBHOOK_URL_OVERRIDE', '')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        # Managed login (Supabase). Verified lazily at request time so
        # webhook-only dev setups still boot; fails closed when unset.
        self.supabase_url = os.getenv('SUPABASE_URL', '')
        self.supabase_jwt_secret = os.getenv('SUPABASE_JWT_SECRET', '')
        # Abuse alerts (Phase 8 #10): GitHub Actions cron hits /alerts/check
        # with ALERTS_CRON_SECRET; violations go out via Brevo.
        self.alerts_cron_secret = os.getenv('ALERTS_CRON_SECRET', '')
        self.brevo_api_key = os.getenv('BREVO_API_KEY', '')
        self.alert_to = os.getenv('ALERT_TO', '')


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
