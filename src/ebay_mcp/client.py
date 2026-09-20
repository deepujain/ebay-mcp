"""Thin REST client for the eBay Buy APIs used by this connector.

Covered APIs (all JSON, all OAuth Bearer):
* Browse API   — item_summary/search, item/{item_id}            (application token)
* Offer API    — bidding/{item_id}, bidding/{item_id}/place_proxy_bid (user token)
* Order API v2 — guest checkout session + guest purchase order  (application token
  with the buy.guest.order scope)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .auth import APP_SCOPES, ApplicationTokenProvider
from .config import EbayConfig
from .errors import EbayApiError, EbayAuthError, EbayNotFoundError

# ---------------------------------------------------------------------------
# Endpoint paths
# ---------------------------------------------------------------------------
# Browse API paths verified against developer.ebay.com/api-docs/buy/browse.
SEARCH_PATH = "/buy/browse/v1/item_summary/search"
GET_ITEM_PATH = "/buy/browse/v1/item/{item_id}"

# Offer API paths verified against developer.ebay.com/api-docs/buy/offer
# ("GET /bidding/{item_id}", "POST /bidding/{item_id}/place_proxy_bid").
GET_BIDDING_PATH = "/buy/offer/v1/bidding/{item_id}"
PLACE_PROXY_BID_PATH = "/buy/offer/v1/bidding/{item_id}/place_proxy_bid"

# Order API v2 guest-checkout method names are published by eBay
# (initiateGuestCheckoutSession, placeOrder, getGuestPurchaseOrder). The exact
# REST paths below follow eBay's naming conventions but were NOT verifiable
# from the public docs at build time — re-verify against the live API before
# production use (see README "Unverified details").
INITIATE_GUEST_CHECKOUT_PATH = "/buy/order/v2/guest_checkout_session/initiate"
PLACE_GUEST_ORDER_PATH = (
    "/buy/order/v2/guest_checkout_session/{checkout_session_id}/place_order"
)
GET_GUEST_PURCHASE_ORDER_PATH = (
    "/buy/order/v2/guest_purchase_order/{purchase_order_id}"
)


# ---------------------------------------------------------------------------
# Normalized models
# ---------------------------------------------------------------------------
@dataclass
class Money:
    value: str
    currency: str

    @property
    def amount(self) -> Decimal:
        try:
            return Decimal(self.value)
        except (InvalidOperation, ValueError, TypeError):
            return Decimal("0")

    def to_dict(self) -> dict:
        return {"value": self.value, "currency": self.currency}


def _money(data: Any) -> Money | None:
    if not isinstance(data, dict) or "value" not in data:
        return None
    return Money(value=str(data["value"]), currency=str(data.get("currency", "")))


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ItemSummary:
    item_id: str
    title: str
    price: Money | None
    condition: str | None
    buying_options: list[str] = field(default_factory=list)
    item_web_url: str | None = None
    seller: str | None = None
    shipping_cost: Money | None = None
    image_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "price": self.price.to_dict() if self.price else None,
            "condition": self.condition,
            "buying_options": self.buying_options,
            "item_web_url": self.item_web_url,
            "seller": self.seller,
            "shipping_cost": self.shipping_cost.to_dict() if self.shipping_cost else None,
            "image_url": self.image_url,
        }


@dataclass
class ItemDetail(ItemSummary):
    description: str | None = None
    available_quantity: int | None = None
    end_time: str | None = None
    category_path: str | None = None

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(
            {
                "description": self.description,
                "available_quantity": self.available_quantity,
                "end_time": self.end_time,
                "category_path": self.category_path,
            }
        )
        return data


def _normalize_summary(data: dict) -> ItemSummary:
    shipping = None
    for option in data.get("shippingOptions", []) or []:
        shipping = _money((option or {}).get("shippingCost"))
        if shipping:
            break
    return ItemSummary(
        item_id=str(data.get("itemId", "")),
        title=str(data.get("title", "")),
        price=_money(data.get("price")),
        condition=data.get("condition"),
        buying_options=list(data.get("buyingOptions", []) or []),
        item_web_url=data.get("itemWebUrl"),
        seller=(data.get("seller") or {}).get("username"),
        shipping_cost=shipping,
        image_url=(data.get("image") or {}).get("imageUrl"),
    )


def _normalize_item(data: dict) -> ItemDetail:
    summary = _normalize_summary(data)
    return ItemDetail(
        item_id=summary.item_id,
        title=summary.title,
        price=summary.price,
        condition=summary.condition,
        buying_options=summary.buying_options,
        item_web_url=summary.item_web_url,
        seller=summary.seller,
        shipping_cost=summary.shipping_cost,
        image_url=summary.image_url,
        description=data.get("description") or data.get("shortDescription"),
        available_quantity=_to_int(data.get("estimatedAvailableQuantity")),
        end_time=data.get("itemEndDate") or data.get("endDate"),
        category_path=data.get("categoryPath"),
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class EbayClient:
    """Synchronous client for the eBay Buy APIs."""

    def __init__(
        self,
        config: EbayConfig,
        token_provider: ApplicationTokenProvider | None = None,
        http=None,
    ):
        self.config = config
        self.tokens = token_provider or ApplicationTokenProvider(config)
        self._http = http

    # -- internals ---------------------------------------------------------
    def _http_client(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=30)
        return self._http

    def _url(self, path: str) -> str:
        return f"{self.config.api_base}{path}"

    def _app_headers(self) -> dict:
        token = self.tokens.get_token(APP_SCOPES)
        return {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.config.marketplace_id,
            "Content-Type": "application/json",
        }

    def _user_headers(self) -> dict:
        if not self.config.user_access_token:
            raise EbayAuthError(
                "This action needs a user access token (Authorization Code grant "
                "with the buy.offer.auction scope). Set EBAY_USER_ACCESS_TOKEN — "
                "see README 'User consent for bidding'."
            )
        return {
            "Authorization": f"Bearer {self.config.user_access_token}",
            "X-EBAY-C-MARKETPLACE-ID": self.config.marketplace_id,
            "Content-Type": "application/json",
        }

    def _parse(self, resp) -> Any:
        if 200 <= resp.status_code < 300:
            if resp.status_code == 204 or not resp.text.strip():
                return {}
            try:
                return resp.json()
            except ValueError:
                return {}
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        errors = payload.get("errors", []) if isinstance(payload, dict) else []
        first = errors[0] if errors else {}
        error_id = first.get("errorId")
        domain = first.get("domain")
        message = (
            first.get("message") or first.get("longMessage") or f"HTTP {resp.status_code}"
        )
        detail = f"eBay API error {error_id} ({domain}): {message}" if error_id else (
            f"eBay API error: HTTP {resp.status_code} {message}"
        )
        if resp.status_code in (401, 403) or domain == "OAuth":
            raise EbayAuthError(detail)
        if resp.status_code == 404:
            raise EbayNotFoundError(
                detail, status_code=resp.status_code, error_id=error_id, errors=errors
            )
        raise EbayApiError(
            detail, status_code=resp.status_code, error_id=error_id, errors=errors
        )

    def _get(self, path: str, *, params=None, headers: dict) -> Any:
        resp = self._http_client().get(self._url(path), params=params, headers=headers)
        return self._parse(resp)

    def _post(self, path: str, *, json_body: dict | None = None, headers: dict) -> Any:
        resp = self._http_client().post(self._url(path), json=json_body or {}, headers=headers)
        return self._parse(resp)

    # -- Browse API (application token) ------------------------------------
    def search_items(
        self,
        q: str,
        *,
        category_ids: list[str] | None = None,
        filters: list[str] | None = None,
        sort: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search active listings. Returns {"total": int, "results": [item dicts]}."""
        params: list[tuple[str, str]] = [
            ("q", q),
            ("limit", str(max(1, min(limit, 200)))),
            ("offset", str(max(0, offset))),
        ]
        if category_ids:
            params.append(("category_ids", ",".join(category_ids)))
        for f in filters or []:
            params.append(("filter", f))
        if sort:
            params.append(("sort", sort))
        data = self._get(SEARCH_PATH, params=params, headers=self._app_headers())
        summaries = [_normalize_summary(s) for s in data.get("itemSummaries", []) or []]
        return {
            "total": data.get("total", len(summaries)),
            "results": [s.to_dict() for s in summaries],
        }

    def get_item(self, item_id: str) -> ItemDetail:
        """Full details for one listing (RESTful item id, e.g. v1|123|0)."""
        data = self._get(
            GET_ITEM_PATH.format(item_id=item_id), headers=self._app_headers()
        )
        return _normalize_item(data)

    # -- Offer API (user token) --------------------------------------------
    def get_bidding(self, item_id: str) -> dict:
        """Bidding state for an auction the user is involved in."""
        return self._get(
            GET_BIDDING_PATH.format(item_id=item_id), headers=self._user_headers()
        )

    def place_proxy_bid(self, item_id: str, max_amount: str, currency: str) -> dict:
        """Place a proxy (max) bid on an auction item. Requires user token."""
        body = {"maxAmount": {"value": max_amount, "currency": currency}}
        return self._post(
            PLACE_PROXY_BID_PATH.format(item_id=item_id),
            json_body=body,
            headers=self._user_headers(),
        )

    # -- Order API v2 guest checkout (application token) --------------------
    def initiate_guest_checkout(
        self,
        line_items: list[dict],
        contact_email: str,
        shipping_address: dict,
    ) -> dict:
        """Open a guest checkout session. Returns the raw session payload
        (includes the checkout session id)."""
        body = {
            "lineItemInputs": line_items,
            "contact": {"email": contact_email},
            "shippingAddress": shipping_address,
        }
        return self._post(
            INITIATE_GUEST_CHECKOUT_PATH, json_body=body, headers=self._app_headers()
        )

    def place_guest_order(self, checkout_session_id: str) -> dict:
        """Pay for / place the order for an open guest checkout session."""
        return self._post(
            PLACE_GUEST_ORDER_PATH.format(checkout_session_id=checkout_session_id),
            json_body={},
            headers=self._app_headers(),
        )

    def get_guest_purchase_order(self, purchase_order_id: str) -> dict:
        """Look up a guest purchase order (status, line items, totals)."""
        return self._get(
            GET_GUEST_PURCHASE_ORDER_PATH.format(purchase_order_id=purchase_order_id),
            headers=self._app_headers(),
        )
