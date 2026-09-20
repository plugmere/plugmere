"""Gateway configuration — env vars loaded and validated at startup."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (before reading any env vars)
load_dotenv(Path(__file__).resolve().parent.parent / '.env')


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f'Required env var {name} is not set')
    return val


@dataclass(frozen=True)
class Config:
    # ── Database ──────────────────────────────────────────────
    database_url: str = field(default_factory=lambda: _require('DATABASE_URL'))

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str = field(default_factory=lambda: _require('REDIS_URL'))

    # ── Nango ─────────────────────────────────────────────────
    nango_host: str = field(default_factory=lambda: _require('NANGO_HOST'))
    nango_secret: str = field(default_factory=lambda: _require('NANGO_SECRET'))

    # ── Gateway ───────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))
    port: int = field(default_factory=lambda: int(os.getenv('PORT', '8000')))

    # ── Gateway OAuth (Phase 8) ─────────────────────────────────────
    oauth_jwt_secret: str = field(default_factory=lambda: os.getenv('OAUTH_JWT_SECRET', ''))
    app_url: str = field(default_factory=lambda: os.getenv('APP_URL', ''))
    daily_quota: int = field(default_factory=lambda: int(os.getenv('DAILY_QUOTA', '5000')))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv('RATE_LIMIT_PER_MINUTE', '60')))
    provider_cap_per_hour: int = field(default_factory=lambda: int(os.getenv('PROVIDER_CAP_PER_HOUR', '200')))
    circuit_breaker_threshold: float = field(default_factory=lambda: float(os.getenv('CIRCUIT_BREAKER_THRESHOLD', '0.5')))
    circuit_breaker_min_calls: int = field(default_factory=lambda: int(os.getenv('CIRCUIT_BREAKER_MIN_CALLS', '5')))
    circuit_breaker_window: int = field(default_factory=lambda: int(os.getenv('CIRCUIT_BREAKER_WINDOW', '60')))
    circuit_breaker_cooldown: int = field(default_factory=lambda: int(os.getenv('CIRCUIT_BREAKER_COOLDOWN', '120')))
    max_connections_per_user: int = field(default_factory=lambda: int(os.getenv('MAX_CONNECTIONS_PER_USER', '15')))


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
