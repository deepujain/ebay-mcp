"""Environment-based configuration.

All secrets come from environment variables — nothing is hardcoded.
See .env.example for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import EbayConfigError

SANDBOX_API_BASE = "https://api.sandbox.ebay.com"
PRODUCTION_API_BASE = "https://api.ebay.com"
SANDBOX_AUTH_HOST = "auth.sandbox.ebay.com"
PRODUCTION_AUTH_HOST = "auth.ebay.com"

VALID_ENVS = ("sandbox", "production")


@dataclass(frozen=True)
class EbayConfig:
    env: str
    client_id: str
    client_secret: str
    marketplace_id: str
    user_access_token: str | None
    user_refresh_token: str | None
    runame: str | None
    watchlist_path: Path
    host: str
    port: int

    @property
    def api_base(self) -> str:
        return SANDBOX_API_BASE if self.env == "sandbox" else PRODUCTION_API_BASE

    @property
    def auth_host(self) -> str:
        return SANDBOX_AUTH_HOST if self.env == "sandbox" else PRODUCTION_AUTH_HOST

    @property
    def authorize_url(self) -> str:
        return f"https://{self.auth_host}/oauth2/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.api_base}/identity/v1/oauth2/token"

    @property
    def has_user_token(self) -> bool:
        return bool(self.user_access_token)


def _get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise EbayConfigError(
            f"Missing required environment variable: {name}. "
            "See .env.example and the README for setup instructions."
        )
    return value


def get_config() -> EbayConfig:
    """Build config from the environment. Raises EbayConfigError on problems."""
    env = (os.environ.get("EBAY_ENV", "sandbox") or "sandbox").lower()
    if env not in VALID_ENVS:
        raise EbayConfigError(
            f"EBAY_ENV must be one of {VALID_ENVS}, got {env!r}."
        )

    client_id = _get("EBAY_CLIENT_ID", required=True)
    client_secret = _get("EBAY_CLIENT_SECRET", required=True)
    assert client_id and client_secret  # for type checkers; _get raises otherwise

    marketplace = os.environ.get("EBAY_MARKETPLACE", "EBAY_US") or "EBAY_US"

    port_raw = os.environ.get("EBAY_PORT", "8000") or "8000"
    try:
        port = int(port_raw)
    except ValueError:
        raise EbayConfigError(f"EBAY_PORT must be an integer, got {port_raw!r}.")

    watchlist_path = Path(
        os.environ.get("EBAY_WATCHLIST_PATH", "./data/watchlist.json") or "./data/watchlist.json"
    )

    return EbayConfig(
        env=env,
        client_id=client_id,
        client_secret=client_secret,
        marketplace_id=marketplace,
        user_access_token=os.environ.get("EBAY_USER_ACCESS_TOKEN") or None,
        user_refresh_token=os.environ.get("EBAY_USER_REFRESH_TOKEN") or None,
        runame=os.environ.get("EBAY_RUNAME") or None,
        watchlist_path=watchlist_path,
        host=os.environ.get("EBAY_HOST", "127.0.0.1") or "127.0.0.1",
        port=port,
    )
