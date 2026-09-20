"""MCP server entrypoint — streamable HTTP transport.

Run:
    python -m ebay_mcp.server
(or: ebay-mcp, once installed)

The server listens on EBAY_HOST:EBAY_PORT (defaults 127.0.0.1:8000).
Any MCP client connects to http(s)://<host>:<port>/mcp as a streamable-HTTP
MCP server.
"""
from __future__ import annotations

from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import tools as T
from .auth import ApplicationTokenProvider
from .client import EbayClient
from .config import EbayConfig, get_config
from .watchlist import WatchlistStore

mcp = FastMCP(
    "ebay",
    instructions=(
        "eBay shopping connector. Read-only tools (search_listings, "
        "get_item_details, watch_item, list_watches, remove_watch, "
        "get_order_status) execute immediately. place_bid and buy_now are "
        "two-phase: the first call returns a pending_confirmation quote and "
        "places nothing; only a second call with confirm=true and the "
        "confirmation_token executes. Never invent a confirmation token."
    ),
)


@lru_cache(maxsize=1)
def _deps() -> tuple[EbayConfig, EbayClient, WatchlistStore]:
    config = get_config()
    provider = ApplicationTokenProvider(config)
    client = EbayClient(config, token_provider=provider)
    store = WatchlistStore(config.watchlist_path)
    return config, client, store


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    """Unauthenticated liveness probe for load balancers / health checks."""
    return JSONResponse({"status": "ok", "service": "ebay-connector"})


@mcp.tool()
def search_listings(
    query: str,
    max_price: float | None = None,
    min_price: float | None = None,
    condition: str | None = None,
    buying_option: str | None = None,
    sort: str = "price",
    limit: int = 10,
    currency: str = "USD",
) -> dict:
    """Search active eBay listings.

    condition: NEW, USED, REFURBISHED... buying_option: FIXED_PRICE or AUCTION.
    sort: price, -price, newlyListed, endingSoonest.
    """
    _, client, _ = _deps()
    return T.search_listings(
        client,
        query,
        max_price=max_price,
        min_price=min_price,
        condition=condition,
        buying_option=buying_option,
        sort=sort,
        limit=limit,
        currency=currency,
    )


@mcp.tool()
def get_item_details(item_id: str) -> dict:
    """Full details for one listing. item_id is the RESTful id, e.g. 'v1|123|0'."""
    _, client, _ = _deps()
    return T.get_item_details(client, item_id)


@mcp.tool()
def watch_item(item_id: str, target_price: float | None = None) -> dict:
    """Watch a listing (server-side watchlist); optional target_price alert."""
    _, client, store = _deps()
    return T.watch_item(client, store, item_id, target_price=target_price)


@mcp.tool()
def list_watches() -> dict:
    """Re-check all watched listings: price drops, target hits, stock-outs."""
    _, client, store = _deps()
    return T.list_watches(client, store)


@mcp.tool()
def remove_watch(item_id: str) -> dict:
    """Stop watching a listing."""
    _, _, store = _deps()
    return T.remove_watch(store, item_id)


@mcp.tool()
def place_bid(
    item_id: str,
    max_bid: float,
    currency: str = "USD",
    confirm: bool = False,
    confirmation_token: str | None = None,
) -> dict:
    """Bid on an auction item (requires user OAuth token on the server).

    First call (confirm=false) returns a pending_confirmation quote and places
    NOTHING. Second call with confirm=true + confirmation_token executes.
    """
    config, client, _ = _deps()
    return T.place_bid(
        client,
        config.client_secret,
        item_id,
        max_bid,
        currency=currency,
        confirm=confirm,
        confirmation_token=confirmation_token,
    )


@mcp.tool()
def buy_now(
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
    """Buy a fixed-price item via eBay guest checkout.

    First call (confirm=false) returns a pending_confirmation quote with totals
    and places NOTHING. Second call with confirm=true + confirmation_token and
    all contact/shipping fields executes the guest checkout.
    """
    config, client, _ = _deps()
    return T.buy_now(
        client,
        config.client_secret,
        item_id,
        quantity=quantity,
        email=email,
        recipient=recipient,
        address_line1=address_line1,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
        confirm=confirm,
        confirmation_token=confirmation_token,
    )


@mcp.tool()
def get_order_status(purchase_order_id: str) -> dict:
    """Look up a guest purchase order placed through this connector."""
    _, client, _ = _deps()
    return T.get_order_status(client, purchase_order_id)


def main() -> None:
    config, _, _ = _deps()  # validates env early; fails fast with a clear error
    mcp.settings.host = config.host
    mcp.settings.port = config.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
