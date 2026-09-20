"""Unit tests: environment configuration."""
import pytest

from ebay_mcp import config as config_mod
from ebay_mcp.config import get_config
from ebay_mcp.errors import EbayConfigError


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_ENV",
        "EBAY_MARKETPLACE",
        "EBAY_USER_ACCESS_TOKEN",
        "EBAY_USER_REFRESH_TOKEN",
        "EBAY_RUNAME",
        "EBAY_HOST",
        "EBAY_PORT",
        "EBAY_WATCHLIST_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EBAY_CLIENT_ID", "my-app-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "my-cert-id")
    return monkeypatch


def test_defaults(clean_env):
    cfg = get_config()
    assert cfg.env == "sandbox"
    assert cfg.marketplace_id == "EBAY_US"
    assert cfg.port == 8000
    assert cfg.host == "127.0.0.1"
    assert cfg.api_base == "https://api.sandbox.ebay.com"
    assert cfg.auth_host == "auth.sandbox.ebay.com"
    assert cfg.token_url == "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    assert cfg.authorize_url == "https://auth.sandbox.ebay.com/oauth2/authorize"
    assert cfg.user_access_token is None
    assert cfg.has_user_token is False


def test_missing_client_id(clean_env):
    clean_env.delenv("EBAY_CLIENT_ID")
    with pytest.raises(EbayConfigError, match="EBAY_CLIENT_ID"):
        get_config()


def test_missing_client_secret(clean_env):
    clean_env.delenv("EBAY_CLIENT_SECRET")
    with pytest.raises(EbayConfigError, match="EBAY_CLIENT_SECRET"):
        get_config()


def test_invalid_env(clean_env):
    clean_env.setenv("EBAY_ENV", "moon")
    with pytest.raises(EbayConfigError, match="EBAY_ENV"):
        get_config()


def test_production_urls(clean_env):
    clean_env.setenv("EBAY_ENV", "production")
    cfg = get_config()
    assert cfg.api_base == "https://api.ebay.com"
    assert cfg.auth_host == "auth.ebay.com"


def test_overrides(clean_env):
    clean_env.setenv("EBAY_MARKETPLACE", "EBAY_GB")
    clean_env.setenv("EBAY_PORT", "9001")
    clean_env.setenv("EBAY_USER_ACCESS_TOKEN", "user-tok")
    clean_env.setenv("EBAY_RUNAME", "my-runame")
    cfg = get_config()
    assert cfg.marketplace_id == "EBAY_GB"
    assert cfg.port == 9001
    assert cfg.has_user_token is True
    assert cfg.runame == "my-runame"


def test_invalid_port(clean_env):
    clean_env.setenv("EBAY_PORT", "notaport")
    with pytest.raises(EbayConfigError, match="EBAY_PORT"):
        get_config()
