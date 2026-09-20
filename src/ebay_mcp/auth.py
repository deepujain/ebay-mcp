"""eBay OAuth 2.0 helpers.

Two token types are used:

* **Application token** (client credentials grant): covers the Browse API
  (search / item details, read-only) and the Order API v2 guest checkout.
  No user interaction is needed; the server mints it from the App ID + Cert ID.
* **User token** (authorization code grant): required for the Offer API
  (``place_proxy_bid`` on auction items). The user completes consent once in a
  browser; the resulting access/refresh tokens are stored in env vars and are
  never logged or echoed.
"""
from __future__ import annotations

import base64
import time
from urllib.parse import urlencode

from .config import EbayConfig
from .errors import EbayAuthError

# Exact scope strings from https://developer.ebay.com/api-docs/static/oauth-scopes.html
SCOPE_BROWSE = "https://api.ebay.com/oauth/api_scope"
SCOPE_BUY_GUEST_ORDER = "https://api.ebay.com/oauth/api_scope/buy.guest.order"
SCOPE_BUY_OFFER_AUCTION = "https://api.ebay.com/oauth/api_scope/buy.offer.auction"

#: Scopes baked into the application token.
APP_SCOPES = (SCOPE_BROWSE, SCOPE_BUY_GUEST_ORDER)
#: Scopes the user must consent to for auction bidding.
USER_SCOPES = (SCOPE_BROWSE, SCOPE_BUY_OFFER_AUCTION)

#: Refresh a bit before actual expiry so in-flight calls never use a dead token.
TOKEN_SKEW_SECONDS = 60


class ApplicationTokenProvider:
    """Fetches and caches eBay application access tokens (client credentials)."""

    def __init__(self, config: EbayConfig, http=None, clock=time.time):
        self._config = config
        self._http = http
        self._clock = clock
        self._cache: dict[frozenset, tuple[str, float]] = {}

    def _http_client(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=30)
        return self._http

    def get_token(self, scopes: tuple = APP_SCOPES) -> str:
        """Return a valid application token, refreshing it when needed."""
        key = frozenset(scopes)
        cached = self._cache.get(key)
        now = self._clock()
        if cached and cached[1] > now + TOKEN_SKEW_SECONDS:
            return cached[0]
        token, expires_in = self._fetch(scopes)
        self._cache[key] = (token, now + expires_in)
        return token

    def _fetch(self, scopes: tuple) -> tuple[str, int]:
        credentials = base64.b64encode(
            f"{self._config.client_id}:{self._config.client_secret}".encode()
        ).decode()
        body = urlencode(
            {"grant_type": "client_credentials", "scope": " ".join(scopes)}
        )
        resp = self._http_client().post(
            self._config.token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=body,
        )
        if resp.status_code != 200:
            raise EbayAuthError(
                "eBay OAuth client-credentials request failed "
                f"(HTTP {resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise EbayAuthError(
                "eBay OAuth response contained no access_token: "
                f"{resp.text[:300]}"
            )
        return token, int(data.get("expires_in", 7200))


def build_authorize_url(
    config: EbayConfig, scopes: tuple = USER_SCOPES, state: str = "ebay-mcp"
) -> str:
    """Build the browser URL the user visits once to grant bidding consent."""
    if not config.runame:
        raise EbayAuthError(
            "EBAY_RUNAME is required to build the user-consent URL. "
            "Register a RuName (redirect URI name) in the eBay developer portal."
        )
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.runame,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
    }
    return f"{config.authorize_url}?{urlencode(params)}"


def exchange_code_for_tokens(config: EbayConfig, code: str, http=None) -> dict:
    """Exchange the authorization code for user access + refresh tokens."""
    import httpx

    client = http or httpx.Client(timeout=30)
    credentials = base64.b64encode(
        f"{config.client_id}:{config.client_secret}".encode()
    ).decode()
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.runame or "",
        }
    )
    resp = client.post(
        config.token_url,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=body,
    )
    if resp.status_code != 200:
        raise EbayAuthError(
            "Authorization-code exchange failed "
            f"(HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()


def refresh_user_token(config: EbayConfig, http=None) -> dict:
    """Refresh an expired user access token using the stored refresh token."""
    import httpx

    if not config.user_refresh_token:
        raise EbayAuthError("EBAY_USER_REFRESH_TOKEN is not set; cannot refresh.")
    client = http or httpx.Client(timeout=30)
    credentials = base64.b64encode(
        f"{config.client_id}:{config.client_secret}".encode()
    ).decode()
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": config.user_refresh_token,
            "scope": " ".join(USER_SCOPES),
        }
    )
    resp = client.post(
        config.token_url,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=body,
    )
    if resp.status_code != 200:
        raise EbayAuthError(
            "User-token refresh failed "
            f"(HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()
