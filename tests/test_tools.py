"""Unit tests: MCP tool logic, incl. the two-phase approval contract."""
import copy

import pytest

from ebay_mcp import tools as T
from ebay_mcp.auth import ApplicationTokenProvider
from ebay_mcp.client import EbayClient
from ebay_mcp.errors import (
    ConfirmationError,
    EbayError,
    ItemNotAuctionError,
    ItemNotAvailableError,
    PriceChangedError,
)
from ebay_mcp.watchlist import WatchlistStore
from tests.fakes import (
    BID_OK,
    BIDDING_INFO,
    CHECKOUT_SESSION_OK,
    ERROR_NOT_FOUND,
    GUEST_ORDER_OK,
    ITEM_AUCTION,
    ITEM_FIXED,
    PLACE_ORDER_OK,
    SEARCH_RESPONSE,
    FakeHttp,
    FakeResponse,
    make_config,
    token_response,
)

SECRET = "test-cert-id"  # == make_config().client_secret


def make_client(http, **cfg_overrides):
    cfg = make_config(**cfg_overrides)
    provider = ApplicationTokenProvider(cfg, http=http)
    return EbayClient(cfg, token_provider=provider, http=http)


def search_http():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "item_summary/search", FakeResponse(200, SEARCH_RESPONSE))
    return http


# ---------------------------------------------------------------------------
# search_listings
# ---------------------------------------------------------------------------
def test_search_listings_builds_filters():
    http = search_http()
    client = make_client(http)
    result = T.search_listings(
        client, "thinkpad",
        max_price=600, min_price=100, condition="used",
        buying_option="fixed_price", sort="-price", limit=5,
    )
    assert result["total"] == 2
    _, kwargs = http.last_call("GET", "item_summary/search")
    params = kwargs["params"]
    filters = [v for k, v in params if k == "filter"]
    assert "price:[100..600]" in filters
    assert "priceCurrency:USD" in filters
    assert "conditions:{USED}" in filters
    assert "buyingOptions:{FIXED_PRICE}" in filters
    assert ("sort", "-price") in params
    assert ("limit", "5") in params
    assert ("q", "thinkpad") in params


def test_search_listings_no_filters():
    http = search_http()
    result = T.search_listings(make_client(http), "x")
    assert result["total"] == 2
    _, kwargs = http.last_call("GET", "item_summary/search")
    assert not [v for k, v in kwargs["params"] if k == "filter"]


# ---------------------------------------------------------------------------
# place_bid — two-phase approval
# ---------------------------------------------------------------------------
def auction_http(item_payload):
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "buy/browse/v1/item/", FakeResponse(200, item_payload))
    http.add("GET", "buy/offer/v1/bidding/", BIDDING_INFO)
    http.add("POST", "place_proxy_bid", BID_OK)
    return http


def test_place_bid_pending_places_nothing():
    http = auction_http(ITEM_AUCTION)
    result = T.place_bid(make_client(http), SECRET, "v1|256087654321|0", 1300.00)
    assert result["status"] == "pending_confirmation"
    assert result["action"] == "place_bid"
    assert result["confirmation_token"]
    assert result["expires_in_seconds"] == 900
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_bid_pending_on_fixed_price_item_fails():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "buy/browse/v1/item/", FakeResponse(200, ITEM_FIXED))
    with pytest.raises(ItemNotAuctionError):
        T.place_bid(make_client(http), SECRET, "v1|256012345678|0", 100.00)


def test_place_bid_confirm_happy_path():
    http = auction_http(ITEM_AUCTION)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    pending = T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00)
    result = T.place_bid(
        client, SECRET, "v1|256087654321|0", 1300.00,
        confirm=True, confirmation_token=pending["confirmation_token"],
    )
    assert result["status"] == "bid_placed"
    assert result["max_bid"] == {"value": "1300.0", "currency": "USD"}
    assert http.count("POST", "place_proxy_bid") == 1


def test_place_bid_confirm_without_token_fails():
    http = auction_http(ITEM_AUCTION)
    with pytest.raises(ConfirmationError, match="confirmation_token"):
        T.place_bid(make_client(http), SECRET, "v1|256087654321|0", 1300.00, confirm=True)
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_bid_confirm_with_tampered_token_fails():
    http = auction_http(ITEM_AUCTION)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    pending = T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00)
    tampered = pending["confirmation_token"][:-4] + "ffff"
    with pytest.raises(ConfirmationError):
        T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00,
                    confirm=True, confirmation_token=tampered)
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_bid_confirm_with_expired_token_fails():
    http = auction_http(ITEM_AUCTION)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    expired = T.make_confirmation_token(
        SECRET, "place_bid", ["v1|256087654321|0", "1200.00", "1300.0", "USD"], ttl=-10
    )
    with pytest.raises(ConfirmationError, match="expired"):
        T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00,
                    confirm=True, confirmation_token=expired)
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_bid_confirm_aborts_on_price_change():
    moved = copy.deepcopy(ITEM_AUCTION)
    moved["price"] = {"value": "1250.00", "currency": "USD"}
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    calls = {"n": 0}

    def item_handler(method, url, kwargs):
        calls["n"] += 1
        # first GET (quote) returns original price; second GET (confirm) moved
        payload = ITEM_AUCTION if calls["n"] == 1 else moved
        return FakeResponse(200, payload)

    http.add("GET", "buy/browse/v1/item/", item_handler)
    http.add("POST", "place_proxy_bid", BID_OK)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    pending = T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00)
    with pytest.raises(PriceChangedError, match="Price moved"):
        T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00,
                    confirm=True, confirmation_token=pending["confirmation_token"])
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_bid_confirm_rejects_changed_max_bid():
    http = auction_http(ITEM_AUCTION)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    pending = T.place_bid(client, SECRET, "v1|256087654321|0", 1300.00)
    with pytest.raises(ConfirmationError, match="differ from the quoted"):
        T.place_bid(client, SECRET, "v1|256087654321|0", 1400.00,
                    confirm=True, confirmation_token=pending["confirmation_token"])
    assert http.count("POST", "place_proxy_bid") == 0


# ---------------------------------------------------------------------------
# buy_now — two-phase approval
# ---------------------------------------------------------------------------
def checkout_http(item_payload):
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "buy/browse/v1/item/", FakeResponse(200, item_payload))
    http.add("POST", "guest_checkout_session/initiate", CHECKOUT_SESSION_OK)
    http.add("POST", "place_order", PLACE_ORDER_OK)
    return http


CONTACT = dict(
    email="buyer@example.com",
    recipient="Jane Doe",
    address_line1="123 Main St",
    city="Austin",
    state="TX",
    postal_code="78701",
)


def test_buy_now_pending_quotes_totals_and_places_nothing():
    http = checkout_http(ITEM_FIXED)
    result = T.buy_now(make_client(http), SECRET, "v1|256012345678|0", quantity=2)
    assert result["status"] == "pending_confirmation"
    assert result["quote"]["unit_price"] == {"value": "549.99", "currency": "USD"}
    assert result["quote"]["shipping"] == {"value": "5.00", "currency": "USD"}
    assert result["quote"]["total"] == {"value": "1104.98", "currency": "USD"}
    assert set(result["missing_contact_fields"]) == {
        "email", "recipient", "address_line1", "city", "state", "postal_code",
    }
    assert http.count("POST", "guest_checkout_session/initiate") == 0
    assert http.count("POST", "place_order") == 0


def test_buy_now_pending_on_auction_item_fails():
    http = checkout_http(ITEM_AUCTION)
    with pytest.raises(ItemNotAvailableError, match="not Buy-It-Now"):
        T.buy_now(make_client(http), SECRET, "v1|256087654321|0")


def test_buy_now_pending_out_of_stock_fails():
    sold_out = copy.deepcopy(ITEM_FIXED)
    sold_out["estimatedAvailableQuantity"] = 0
    http = checkout_http(sold_out)
    with pytest.raises(ItemNotAvailableError, match="Only 0 available"):
        T.buy_now(make_client(http), SECRET, "v1|256012345678|0", quantity=1)


def test_buy_now_pending_insufficient_quantity_fails():
    http = checkout_http(ITEM_FIXED)  # 3 available
    with pytest.raises(ItemNotAvailableError, match="Only 3 available"):
        T.buy_now(make_client(http), SECRET, "v1|256012345678|0", quantity=5)


def test_buy_now_confirm_happy_path():
    http = checkout_http(ITEM_FIXED)
    client = make_client(http)
    pending = T.buy_now(client, SECRET, "v1|256012345678|0", quantity=1, **CONTACT)
    result = T.buy_now(
        client, SECRET, "v1|256012345678|0", quantity=1,
        confirm=True, confirmation_token=pending["confirmation_token"], **CONTACT,
    )
    assert result["status"] == "order_placed"
    assert result["purchase_order_id"] == "po-xyz-789"
    assert result["checkout_session_id"] == "sess-abc-123"
    assert http.count("POST", "guest_checkout_session/initiate") == 1
    assert http.count("POST", "place_order") == 1
    _, kwargs = http.last_call("POST", "guest_checkout_session/initiate")
    assert kwargs["json"]["contact"] == {"email": "buyer@example.com"}
    assert kwargs["json"]["shippingAddress"]["postalCode"] == "78701"


def test_buy_now_confirm_missing_contact_fails():
    http = checkout_http(ITEM_FIXED)
    client = make_client(http)
    pending = T.buy_now(client, SECRET, "v1|256012345678|0", quantity=1, **CONTACT)
    with pytest.raises(ConfirmationError, match="missing contact"):
        T.buy_now(
            client, SECRET, "v1|256012345678|0", quantity=1,
            confirm=True, confirmation_token=pending["confirmation_token"],
            email="buyer@example.com",  # everything else missing
        )
    assert http.count("POST", "guest_checkout_session/initiate") == 0


def test_buy_now_confirm_aborts_on_price_change():
    moved = copy.deepcopy(ITEM_FIXED)
    moved["price"] = {"value": "599.99", "currency": "USD"}
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    calls = {"n": 0}

    def item_handler(method, url, kwargs):
        calls["n"] += 1
        return FakeResponse(200, ITEM_FIXED if calls["n"] == 1 else moved)

    http.add("GET", "buy/browse/v1/item/", item_handler)
    http.add("POST", "guest_checkout_session/initiate", CHECKOUT_SESSION_OK)
    http.add("POST", "place_order", PLACE_ORDER_OK)
    client = make_client(http)
    pending = T.buy_now(client, SECRET, "v1|256012345678|0", quantity=1, **CONTACT)
    with pytest.raises(PriceChangedError, match="Unit price moved"):
        T.buy_now(client, SECRET, "v1|256012345678|0", quantity=1,
                  confirm=True, confirmation_token=pending["confirmation_token"], **CONTACT)
    assert http.count("POST", "guest_checkout_session/initiate") == 0


def test_buy_now_confirm_with_wrong_item_token_fails():
    http = checkout_http(ITEM_FIXED)
    client = make_client(http)
    pending = T.buy_now(client, SECRET, "v1|256012345678|0", quantity=1, **CONTACT)
    with pytest.raises(ConfirmationError, match="different item"):
        T.buy_now(client, SECRET, "v1|999999999999|0", quantity=1,
                  confirm=True, confirmation_token=pending["confirmation_token"], **CONTACT)


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------
def watch_http(price="549.99", not_found=False):
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    item = copy.deepcopy(ITEM_FIXED)
    item["price"] = {"value": price, "currency": "USD"}
    route = ERROR_NOT_FOUND if not_found else FakeResponse(200, item)
    http.add("GET", "buy/browse/v1/item/", route)
    return http


def test_watch_item_adds_entry():
    http = watch_http()
    cfg = make_config()
    store = WatchlistStore(cfg.watchlist_path)
    result = T.watch_item(make_client(http), store, "v1|256012345678|0", target_price=500.00)
    assert result["status"] == "watching"
    assert result["watch"]["target_price"] == "500.0"
    assert result["watch"]["last_price"] == "549.99"
    assert len(store.all()) == 1


def test_list_watches_detects_target_hit():
    http = watch_http(price="499.99")
    cfg = make_config()
    store = WatchlistStore(cfg.watchlist_path)
    store.add("v1|256012345678|0", "ThinkPad", target_price="500.00",
              currency="USD", last_price="549.99")
    report = T.list_watches(make_client(http), store)
    assert report["watches"][0]["status"] == "target_hit"
    assert "500.00" in report["watches"][0]["note"]


def test_list_watches_detects_price_drop():
    http = watch_http(price="529.99")
    cfg = make_config()
    store = WatchlistStore(cfg.watchlist_path)
    store.add("v1|256012345678|0", "ThinkPad", target_price=None,
              currency="USD", last_price="549.99")
    report = T.list_watches(make_client(http), store)
    assert report["watches"][0]["status"] == "price_drop"


def test_list_watches_marks_ended_on_404():
    http = watch_http(not_found=True)
    cfg = make_config()
    store = WatchlistStore(cfg.watchlist_path)
    store.add("v1|256012345678|0", "ThinkPad")
    report = T.list_watches(make_client(http), store)
    assert report["watches"][0]["status"] == "ended"


def test_remove_watch():
    cfg = make_config()
    store = WatchlistStore(cfg.watchlist_path)
    store.add("v1|1|0", "Thing")
    assert T.remove_watch(store, "v1|1|0")["status"] == "removed"
    assert T.remove_watch(store, "v1|1|0")["status"] == "not_found"


# ---------------------------------------------------------------------------
# get_order_status + token helpers
# ---------------------------------------------------------------------------
def test_get_order_status():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "guest_purchase_order/", GUEST_ORDER_OK)
    result = T.get_order_status(make_client(http), "po-xyz-789")
    assert result["status"] == "CREATED"


def test_confirmation_token_round_trip():
    token = T.make_confirmation_token(SECRET, "buy_now", ["v1|1|0", "10.00", "1", "USD"])
    fields = T.verify_confirmation_token(SECRET, "buy_now", 4, token)
    assert fields == ["v1|1|0", "10.00", "1", "USD"]


def test_confirmation_token_wrong_secret_fails():
    token = T.make_confirmation_token(SECRET, "buy_now", ["v1|1|0", "10.00", "1", "USD"])
    with pytest.raises(ConfirmationError):
        T.verify_confirmation_token("other-secret", "buy_now", 4, token)


def test_confirmation_token_wrong_action_fails():
    token = T.make_confirmation_token(SECRET, "buy_now", ["v1|1|0", "10.00", "1", "USD"])
    with pytest.raises(ConfirmationError):
        T.verify_confirmation_token(SECRET, "place_bid", 4, token)
