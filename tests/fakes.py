"""Test doubles and shared fixtures."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ebay_mcp.config import EbayConfig


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    @property
    def text(self):
        return self._text if self._text else str(self._payload)


class FakeHttp:
    """Routes (METHOD, url-substring) -> FakeResponse or handler fn."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.routes: list[tuple[str, str, object]] = []

    def add(self, method, substring, response_or_fn):
        self.routes.append((method.upper(), substring, response_or_fn))
        return self

    def _dispatch(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for route_method, substring, resp in self.routes:
            if route_method == method and substring in url:
                if callable(resp):
                    return resp(method, url, kwargs)
                return resp
        return FakeResponse(
            500,
            {
                "errors": [
                    {
                        "errorId": 99999,
                        "domain": "TEST",
                        "message": f"no fake route for {method} {url}",
                    }
                ]
            },
        )

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, kwargs)

    def count(self, method, substring):
        return sum(
            1 for m, u, _ in self.calls if m == method.upper() and substring in u
        )

    def last_call(self, method, substring):
        for m, u, kw in reversed(self.calls):
            if m == method.upper() and substring in u:
                return u, kw
        raise AssertionError(f"no call recorded for {method} {substring}")


def make_config(**overrides) -> EbayConfig:
    defaults = dict(
        env="sandbox",
        client_id="test-app-id",
        client_secret="test-cert-id",
        marketplace_id="EBAY_US",
        user_access_token=None,
        user_refresh_token=None,
        runame=None,
        watchlist_path=Path(tempfile.mkdtemp()) / "watchlist.json",
        host="127.0.0.1",
        port=8000,
    )
    defaults.update(overrides)
    return EbayConfig(**defaults)


def token_response(token="APP-TOKEN-123", expires_in=7200):
    return FakeResponse(
        200,
        {
            "access_token": token,
            "expires_in": expires_in,
            "token_type": "Application Access Token",
        },
    )


# ---------------------------------------------------------------------------
# eBay API fixtures (shaped like real Browse/Offer/Order payloads)
# ---------------------------------------------------------------------------
SEARCH_RESPONSE = {
    "total": 2,
    "limit": 50,
    "offset": 0,
    "itemSummaries": [
        {
            "itemId": "v1|256012345678|0",
            "title": 'Lenovo ThinkPad X1 Carbon Gen 9 14" i7 16GB 512GB',
            "price": {"value": "549.99", "currency": "USD"},
            "condition": "Used",
            "buyingOptions": ["FIXED_PRICE"],
            "itemWebUrl": "https://www.ebay.com/itm/256012345678",
            "seller": {"username": "techdeals_refurb"},
            "shippingOptions": [
                {"shippingCost": {"value": "0.00", "currency": "USD"}}
            ],
            "image": {"imageUrl": "https://i.ebayimg.com/img1.jpg"},
        },
        {
            "itemId": "v1|256087654321|0",
            "title": "Apple MacBook Pro 14 M3 Pro - Auction",
            "price": {"value": "1200.00", "currency": "USD"},
            "condition": "Used",
            "buyingOptions": ["AUCTION"],
            "itemWebUrl": "https://www.ebay.com/itm/256087654321",
            "seller": {"username": "auctionhouse"},
            "shippingOptions": [
                {"shippingCost": {"value": "15.00", "currency": "USD"}}
            ],
            "image": {"imageUrl": "https://i.ebayimg.com/img2.jpg"},
        },
    ],
}

SEARCH_EMPTY = {"total": 0, "limit": 50, "offset": 0, "itemSummaries": []}

ITEM_FIXED = {
    "itemId": "v1|256012345678|0",
    "title": 'Lenovo ThinkPad X1 Carbon Gen 9 14" i7 16GB 512GB',
    "price": {"value": "549.99", "currency": "USD"},
    "condition": "Used",
    "buyingOptions": ["FIXED_PRICE"],
    "itemWebUrl": "https://www.ebay.com/itm/256012345678",
    "seller": {"username": "techdeals_refurb"},
    "shippingOptions": [{"shippingCost": {"value": "5.00", "currency": "USD"}}],
    "estimatedAvailableQuantity": 3,
    "description": "Off-lease business laptop in great condition.",
    "categoryPath": "Computers/Tablets & Networking/Laptops",
}

ITEM_AUCTION = {
    "itemId": "v1|256087654321|0",
    "title": "Apple MacBook Pro 14 M3 Pro - Auction",
    "price": {"value": "1200.00", "currency": "USD"},
    "condition": "Used",
    "buyingOptions": ["AUCTION"],
    "itemWebUrl": "https://www.ebay.com/itm/256087654321",
    "seller": {"username": "auctionhouse"},
    "shippingOptions": [{"shippingCost": {"value": "15.00", "currency": "USD"}}],
    "itemEndDate": "2026-09-25T18:00:00.000Z",
}

ERROR_OAUTH = FakeResponse(
    401,
    {
        "errors": [
            {
                "errorId": 1001,
                "domain": "OAuth",
                "category": "REQUEST",
                "message": "Invalid access token",
                "longMessage": "Invalid access token. Check the value of the "
                "Authorization HTTP request header.",
            }
        ]
    },
)

ERROR_NOT_FOUND = FakeResponse(
    404,
    {
        "errors": [
            {
                "errorId": 11010,
                "domain": "API_BROWSE",
                "category": "REQUEST",
                "message": "The specified item ID was not found.",
            }
        ]
    },
)

ERROR_BID_TOO_LOW = FakeResponse(
    400,
    {
        "errors": [
            {
                "errorId": 12009,
                "domain": "API_OFFER",
                "category": "REQUEST",
                "message": "The bid amount is below the minimum required bid.",
            }
        ]
    },
)

BID_OK = FakeResponse(200, {"bidId": "bid-9f3a2c", "status": "BID_PLACED"})

BIDDING_INFO = FakeResponse(
    200,
    {
        "bidId": "bid-77aa",
        "itemId": "v1|256087654321|0",
        "maxBidPrice": {"value": "1250.00", "currency": "USD"},
    },
)

CHECKOUT_SESSION_OK = FakeResponse(200, {"checkoutSessionId": "sess-abc-123"})

PLACE_ORDER_OK = FakeResponse(
    200, {"purchaseOrderId": "po-xyz-789", "orderStatus": "CREATED"}
)

GUEST_ORDER_OK = FakeResponse(
    200,
    {
        "purchaseOrderId": "po-xyz-789",
        "orderStatus": "CREATED",
        "lineItems": [{"itemId": "v1|256012345678|0", "quantity": 1}],
    },
)
