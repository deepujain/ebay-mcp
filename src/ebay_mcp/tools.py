"""MCP tool implementations.

APPROVAL CONTRACT (this is the safety contract surfaced to Muse reviewers):

* Read-only tools — ``search_listings``, ``get_item_details``, ``watch_item``,
  ``list_watches``, ``remove_watch``, ``get_order_status`` — execute immediately.
* Mutating tools — ``place_bid`` and ``buy_now`` — are strictly TWO-PHASE:

  1. Called **without** ``confirm=True`` they NEVER call eBay's mutating
     endpoints. They fetch a fresh quote and return
     ``{"status": "pending_confirmation", ...}`` with a tamper-evident
     ``confirmation_token`` that binds item id + price + quantity/max-bid.
  2. Called **with** ``confirm=True`` and the token, they re-fetch the listing,
     abort with ``PriceChangedError``/``ItemNotAvailableError`` if anything
     moved, and only then execute the mutation.

  Confirmation tokens are HMAC-SHA256 signed with the server's client secret,
  expire after 15 minutes, and are validated with constant-time comparison.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation

from .client import EbayClient, ItemDetail
from .errors import (
    ConfirmationError,
    EbayApiError,
    EbayError,
    EbayNotFoundError,
    ItemNotAuctionError,
    ItemNotAvailableError,
    PriceChangedError,
)
from .watchlist import WatchlistStore

CONFIRM_TTL_SECONDS = 900  # confirmation tokens live 15 minutes
_TOKEN_SEP = "~"


def _to_decimal(value: str | None) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _sign(secret: str, action: str, fields: list[str], expires: int) -> str:
    msg = _TOKEN_SEP.join([action, *fields, str(expires)])
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_confirmation_token(
    secret: str,
    action: str,
    fields: list[str],
    ttl: int = CONFIRM_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Build a self-contained, tamper-evident confirmation token."""
    if any(_TOKEN_SEP in f for f in fields):
        raise ValueError("confirmation field contains separator")
    expires = int((now if now is not None else time.time()) + ttl)
    sig = _sign(secret, action, fields, expires)
    return _TOKEN_SEP.join([str(expires), sig, *fields])


def verify_confirmation_token(
    secret: str,
    action: str,
    field_count: int,
    token: str,
    now: float | None = None,
) -> list[str]:
    """Validate a token; return the bound fields. Raises ConfirmationError."""
    now = now if now is not None else time.time()
    parts = token.split(_TOKEN_SEP)
    if len(parts) != 2 + field_count:
        raise ConfirmationError("Malformed confirmation token.")
    expires_raw, sig, *fields = parts
    try:
        expires = int(expires_raw)
    except ValueError:
        raise ConfirmationError("Malformed confirmation token.")
    if expires < now:
        raise ConfirmationError(
            "Confirmation token expired — please request a fresh quote."
        )
    expected = _sign(secret, action, fields, expires)
    if not hmac.compare_digest(expected, sig):
        raise ConfirmationError(
            "Confirmation token is invalid (wrong action or tampered)."
        )
    return fields


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------
def search_listings(
    client: EbayClient,
    query: str,
    *,
    max_price: float | None = None,
    min_price: float | None = None,
    condition: str | None = None,
    buying_option: str | None = None,
    sort: str = "price",
    limit: int = 10,
    currency: str = "USD",
) -> dict:
    """Search active eBay listings.

    condition: NEW, USED, REFURBISHED, etc. buying_option: FIXED_PRICE, AUCTION.
    sort: price, -price, newlyListed, endingSoonest.
    """
    filters: list[str] = []
    if max_price is not None or min_price is not None:
        lo = "" if min_price is None else str(min_price)
        hi = "" if max_price is None else str(max_price)
        filters.append(f"price:[{lo}..{hi}]")
        filters.append(f"priceCurrency:{currency}")
    if condition:
        filters.append("conditions:{" + condition.upper() + "}")
    if buying_option:
        filters.append("buyingOptions:{" + buying_option.upper() + "}")
    return client.search_items(
        query, filters=filters, sort=sort, limit=limit
    )


def get_item_details(client: EbayClient, item_id: str) -> dict:
    """Full details for one listing (RESTful item id, e.g. 'v1|123456789|0')."""
    return client.get_item(item_id).to_dict()


def watch_item(
    client: EbayClient,
    store: WatchlistStore,
    item_id: str,
    target_price: float | None = None,
) -> dict:
    """Add a listing to the server-side watchlist with an optional target price."""
    item = client.get_item(item_id)
    entry = store.add(
        item_id=item_id,
        title=item.title,
        target_price=str(target_price) if target_price is not None else None,
        currency=item.price.currency if item.price else "USD",
        last_price=item.price.value if item.price else None,
    )
    return {
        "status": "watching",
        "watch": entry,
        "current": item.to_dict(),
    }


def list_watches(client: EbayClient, store: WatchlistStore) -> dict:
    """Re-check every watched listing; flag price drops, target hits, stock-outs."""
    report: list[dict] = []
    for entry in store.all():
        item_id = entry["item_id"]
        try:
            item = client.get_item(item_id)
        except EbayNotFoundError:
            store.update(item_id, last_status="ended")
            report.append(
                {**entry, "status": "ended", "note": "Listing no longer found."}
            )
            continue
        current = item.price.value if item.price else None
        status, note = "watching", None
        if item.available_quantity == 0:
            status, note = "out_of_stock", "No quantity available."
        elif entry.get("target_price") and current is not None and _to_decimal(
            current
        ) <= _to_decimal(entry["target_price"]):
            status = "target_hit"
            note = (
                f"At or below target {entry['target_price']} "
                f"{entry.get('currency', '')}."
            )
        elif (
            entry.get("last_price")
            and current is not None
            and _to_decimal(current) < _to_decimal(entry["last_price"])
        ):
            status = "price_drop"
            note = f"Dropped from {entry['last_price']} to {current}."
        store.update(item_id, last_price=current, last_status=status)
        report.append(
            {
                **entry,
                "current_price": current,
                "status": status,
                "note": note,
                "buying_options": item.buying_options,
                "item_web_url": item.item_web_url,
            }
        )
    return {"watches": report}


def remove_watch(store: WatchlistStore, item_id: str) -> dict:
    """Stop watching a listing."""
    removed = store.remove(item_id)
    return {"status": "removed" if removed else "not_found", "item_id": item_id}


def get_order_status(client: EbayClient, purchase_order_id: str) -> dict:
    """Look up a guest purchase order placed through this connector."""
    data = client.get_guest_purchase_order(purchase_order_id)
    return {
        "purchase_order_id": purchase_order_id,
        "status": data.get("orderStatus") or data.get("status"),
        "raw": data,
    }


# ---------------------------------------------------------------------------
# Mutating tools (two-phase, approval-gated)
# ---------------------------------------------------------------------------
def _require_auction(item: ItemDetail) -> None:
    if "AUCTION" not in (item.buying_options or []):
        raise ItemNotAuctionError(
            f"Item {item.item_id} is not an auction "
            f"(buying options: {item.buying_options})."
        )


def _require_fixed_price(item: ItemDetail) -> None:
    if "FIXED_PRICE" not in (item.buying_options or []):
        raise ItemNotAvailableError(
            f"Item {item.item_id} is not Buy-It-Now "
            f"(buying options: {item.buying_options})."
        )


def _require_available(item: ItemDetail, quantity: int) -> None:
    if item.available_quantity is not None and item.available_quantity < quantity:
        raise ItemNotAvailableError(
            f"Only {item.available_quantity} available; requested {quantity}."
        )


def place_bid(
    client: EbayClient,
    secret: str,
    item_id: str,
    max_bid: float,
    currency: str = "USD",
    confirm: bool = False,
    confirmation_token: str | None = None,
) -> dict:
    """Place a proxy (max) bid on an auction item. Two-phase: without
    confirm=True returns a pending quote; with confirm=True + token executes."""
    if not confirm:
        item = client.get_item(item_id)
        _require_auction(item)
        bidding_info: dict = {}
        try:
            bidding_info = client.get_bidding(item_id)
        except EbayError:
            pass  # user token may be absent; the listing quote is still valid
        price = item.price.value if item.price else "0"
        token = make_confirmation_token(
            secret, "place_bid", [item_id, price, str(max_bid), currency]
        )
        return {
            "status": "pending_confirmation",
            "action": "place_bid",
            "item": item.to_dict(),
            "max_bid": {"value": str(max_bid), "currency": currency},
            "bidding": bidding_info,
            "confirmation_token": token,
            "expires_in_seconds": CONFIRM_TTL_SECONDS,
            "note": (
                "No bid has been placed. To execute, call place_bid again with "
                "confirm=true and this confirmation_token."
            ),
        }

    if not confirmation_token:
        raise ConfirmationError(
            "confirm=true requires the confirmation_token from the pending quote."
        )
    fields = verify_confirmation_token(secret, "place_bid", 4, confirmation_token)
    tok_item, tok_price, tok_max, tok_currency = fields
    if tok_item != item_id:
        raise ConfirmationError("Confirmation token was issued for a different item.")
    if _to_decimal(str(max_bid)) != _to_decimal(tok_max) or currency != tok_currency:
        raise ConfirmationError(
            "max_bid/currency differ from the quoted values; bid NOT placed."
        )

    # Re-validate the listing immediately before spending.
    item = client.get_item(item_id)
    _require_auction(item)
    current_price = item.price.value if item.price else "0"
    if _to_decimal(current_price) != _to_decimal(tok_price):
        raise PriceChangedError(
            f"Price moved from {tok_price} to {current_price} since the quote; "
            "bid NOT placed. Request a fresh quote."
        )
    result = client.place_proxy_bid(
        item_id, max_amount=str(max_bid), currency=currency
    )
    return {
        "status": "bid_placed",
        "item_id": item_id,
        "max_bid": {"value": str(max_bid), "currency": currency},
        "response": result,
    }


def _build_quote(item: ItemDetail, quantity: int) -> dict:
    currency = item.price.currency if item.price else "USD"
    unit = item.price.amount if item.price else Decimal("0")
    shipping = item.shipping_cost.amount if item.shipping_cost else Decimal("0")
    total = unit * quantity + shipping
    return {
        "unit_price": {"value": f"{unit:.2f}", "currency": currency},
        "quantity": quantity,
        "shipping": {"value": f"{shipping:.2f}", "currency": currency},
        "total": {"value": f"{total:.2f}", "currency": currency},
    }


def _extract_purchase_order_id(payload: dict) -> str | None:
    for key in ("purchaseOrderId", "purchase_order_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


_CONTACT_FIELDS = (
    "email",
    "recipient",
    "address_line1",
    "city",
    "state",
    "postal_code",
)


def buy_now(
    client: EbayClient,
    secret: str,
    item_id: str,
    quantity: int = 1,
    email: str | None = None,
    recipient: str | None = None,
    address_line1: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    country: str = "US",
    confirm: bool = False,
    confirmation_token: str | None = None,
) -> dict:
    """Buy a fixed-price item via eBay guest checkout. Two-phase: without
    confirm=True returns a pending quote; with confirm=True + token executes."""
    if quantity < 1:
        raise EbayError("quantity must be at least 1.")
    contact = {
        "email": email,
        "recipient": recipient,
        "address_line1": address_line1,
        "city": city,
        "state": state,
        "postal_code": postal_code,
    }

    if not confirm:
        item = client.get_item(item_id)
        _require_fixed_price(item)
        _require_available(item, quantity)
        quote = _build_quote(item, quantity)
        token = make_confirmation_token(
            secret,
            "buy_now",
            [item_id, quote["unit_price"]["value"], str(quantity),
             quote["unit_price"]["currency"]],
        )
        missing = [k for k, v in contact.items() if not v]
        return {
            "status": "pending_confirmation",
            "action": "buy_now",
            "item": {
                "item_id": item.item_id,
                "title": item.title,
                "item_web_url": item.item_web_url,
                "seller": item.seller,
            },
            "quote": quote,
            "missing_contact_fields": missing,
            "confirmation_token": token,
            "expires_in_seconds": CONFIRM_TTL_SECONDS,
            "note": (
                "No order has been placed. To execute, call buy_now again with "
                "confirm=true, this confirmation_token, and all contact/shipping "
                "fields filled."
            ),
        }

    if not confirmation_token:
        raise ConfirmationError(
            "confirm=true requires the confirmation_token from the pending quote."
        )
    fields = verify_confirmation_token(secret, "buy_now", 4, confirmation_token)
    tok_item, tok_price, tok_qty, tok_currency = fields
    if tok_item != item_id:
        raise ConfirmationError("Confirmation token was issued for a different item.")
    if str(quantity) != tok_qty:
        raise ConfirmationError(
            "quantity differs from the quoted value; order NOT placed."
        )
    missing = [k for k, v in contact.items() if not v]
    if missing:
        raise ConfirmationError(
            f"Cannot place order; missing contact/shipping fields: {missing}."
        )

    # Re-validate immediately before spending.
    item = client.get_item(item_id)
    _require_fixed_price(item)
    _require_available(item, quantity)
    quote = _build_quote(item, quantity)
    if _to_decimal(quote["unit_price"]["value"]) != _to_decimal(tok_price) or quote[
        "unit_price"
    ]["currency"] != tok_currency:
        raise PriceChangedError(
            f"Unit price moved from {tok_price} {tok_currency} to "
            f"{quote['unit_price']['value']} {quote['unit_price']['currency']} "
            "since the quote; order NOT placed. Request a fresh quote."
        )

    line_items = [{"itemId": item_id, "quantity": quantity}]
    shipping_address = {
        "recipient": recipient,
        "addressLine1": address_line1,
        "city": city,
        "stateOrProvince": state,
        "postalCode": postal_code,
        "countryCode": country,
    }
    session = client.initiate_guest_checkout(line_items, email, shipping_address)
    checkout_session_id = session.get("checkoutSessionId")
    if not checkout_session_id:
        raise EbayApiError(
            "initiateGuestCheckoutSession returned no checkoutSessionId.",
            errors=[session],
        )
    order = client.place_guest_order(checkout_session_id)
    purchase_order_id = _extract_purchase_order_id(order)
    return {
        "status": "order_placed",
        "purchase_order_id": purchase_order_id,
        "quote": quote,
        "checkout_session_id": checkout_session_id,
        "raw": order,
    }
