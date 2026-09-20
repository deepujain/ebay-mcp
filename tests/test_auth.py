"""Unit tests: OAuth token handling."""
import pytest

from ebay_mcp import auth
from ebay_mcp.auth import (
    APP_SCOPES,
    ApplicationTokenProvider,
    build_authorize_url,
    exchange_code_for_tokens,
    refresh_user_token,
)
from ebay_mcp.errors import EbayAuthError
from tests.fakes import FakeHttp, FakeResponse, make_config, token_response


def make_provider(http=None, clock=None, **cfg_overrides):
    cfg = make_config(**cfg_overrides)
    return ApplicationTokenProvider(cfg, http=http, clock=clock or __import__("time").time)


def test_get_token_posts_client_credentials():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    provider = make_provider(http=http)
    token = provider.get_token()
    assert token == "APP-TOKEN-123"
    url, kwargs = http.last_call("POST", "oauth2/token")
    assert url == "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
    headers = kwargs["headers"]
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    body = kwargs["content"]
    assert "grant_type=client_credentials" in body
    assert "scope=" in body
    # default scopes include browse + guest checkout
    assert "buy.guest.order" in body


def test_token_is_cached():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    provider = make_provider(http=http)
    assert provider.get_token() == provider.get_token()
    assert http.count("POST", "oauth2/token") == 1


def test_token_refreshed_after_expiry():
    now = [1_000_000.0]
    http = FakeHttp().add("POST", "oauth2/token", token_response(expires_in=100))
    provider = make_provider(http=http, clock=lambda: now[0])
    provider.get_token()
    now[0] += 200  # past expiry (100s) + skew
    provider.get_token()
    assert http.count("POST", "oauth2/token") == 2


def test_token_not_refreshed_when_still_valid():
    now = [1_000_000.0]
    http = FakeHttp().add("POST", "oauth2/token", token_response(expires_in=7200))
    provider = make_provider(http=http, clock=lambda: now[0])
    provider.get_token()
    now[0] += 3600
    provider.get_token()
    assert http.count("POST", "oauth2/token") == 1


def test_token_failure_raises_auth_error():
    http = FakeHttp().add("POST", "oauth2/token", FakeResponse(400, {"error": "invalid_client"}))
    provider = make_provider(http=http)
    with pytest.raises(EbayAuthError, match="400"):
        provider.get_token()


def test_token_response_without_access_token_raises():
    http = FakeHttp().add("POST", "oauth2/token", FakeResponse(200, {"expires_in": 7200}))
    provider = make_provider(http=http)
    with pytest.raises(EbayAuthError, match="no access_token"):
        provider.get_token()


def test_build_authorize_url():
    cfg = make_config(runame="my-runame-123")
    url = build_authorize_url(cfg)
    assert url.startswith("https://auth.sandbox.ebay.com/oauth2/authorize?")
    assert "client_id=test-app-id" in url
    assert "redirect_uri=my-runame-123" in url
    assert "response_type=code" in url
    assert "buy.offer.auction" in url


def test_build_authorize_url_requires_runame():
    cfg = make_config(runame=None)
    with pytest.raises(EbayAuthError, match="EBAY_RUNAME"):
        build_authorize_url(cfg)


def test_exchange_code_for_tokens():
    http = FakeHttp().add(
        "POST", "oauth2/token",
        FakeResponse(200, {"access_token": "USER-TOK", "refresh_token": "REF-TOK", "expires_in": 7200}),
    )
    cfg = make_config(runame="my-runame-123")
    data = exchange_code_for_tokens(cfg, "auth-code-xyz", http=http)
    assert data["access_token"] == "USER-TOK"
    url, kwargs = http.last_call("POST", "oauth2/token")
    assert "grant_type=authorization_code" in kwargs["content"]
    assert "code=auth-code-xyz" in kwargs["content"]


def test_exchange_code_failure_raises():
    http = FakeHttp().add("POST", "oauth2/token", FakeResponse(400, {"error": "bad"}))
    cfg = make_config(runame="my-runame-123")
    with pytest.raises(EbayAuthError):
        exchange_code_for_tokens(cfg, "bad-code", http=http)


def test_refresh_user_token():
    http = FakeHttp().add(
        "POST", "oauth2/token",
        FakeResponse(200, {"access_token": "USER-TOK-2", "expires_in": 7200}),
    )
    cfg = make_config(user_refresh_token="REF-TOK")
    data = refresh_user_token(cfg, http=http)
    assert data["access_token"] == "USER-TOK-2"
    _, kwargs = http.last_call("POST", "oauth2/token")
    assert "grant_type=refresh_token" in kwargs["content"]


def test_refresh_user_token_without_refresh_token():
    cfg = make_config(user_refresh_token=None)
    with pytest.raises(EbayAuthError, match="EBAY_USER_REFRESH_TOKEN"):
        refresh_user_token(cfg, http=FakeHttp())


def test_scope_constants_exact():
    assert auth.SCOPE_BROWSE == "https://api.ebay.com/oauth/api_scope"
    assert auth.SCOPE_BUY_GUEST_ORDER == "https://api.ebay.com/oauth/api_scope/buy.guest.order"
    assert auth.SCOPE_BUY_OFFER_AUCTION == "https://api.ebay.com/oauth/api_scope/buy.offer.auction"
    assert set(APP_SCOPES) == {auth.SCOPE_BROWSE, auth.SCOPE_BUY_GUEST_ORDER}
