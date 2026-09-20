"""Unit tests: eBay REST client (Browse / Offer / Order)."""
import pytest

from ebay_mcp.auth import ApplicationTokenProvider
from ebay_mcp.client import EbayClient, ItemDetail
from ebay_mcp.errors import EbayApiError, EbayAuthError, EbayNotFoundError
from tests.fakes import (
    BID_OK,
    BIDDING_INFO,
    CHECKOUT_SESSION_OK,
    ERROR_BID_TOO_LOW,
    ERROR_NOT_FOUND,
    ERROR_OAUTH,
    GUEST_ORDER_OK,
    ITEM_AUCTION,
    ITEM_FIXED,
    PLACE_ORDER_OK,
    SEARCH_EMPTY,
    SEARCH_RESPONSE,
    FakeHttp,
    FakeResponse,
    make_config,
    token_response,
)


def make_client(http, **cfg_overrides):
    cfg = make_config(**cfg_overrides)
    provider = ApplicationTokenProvider(cfg, http=http)
    return EbayClient(cfg, token_provider=provider, http=http)


def api_http():
    """FakeHttp pre-wired with a working token endpoint + search/item routes."""
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "item_summary/search", FakeResponse(200, SEARCH_RESPONSE))
    return http


# ---------------------------------------------------------------------------
# Browse: search
# ---------------------------------------------------------------------------
def test_search_happy_path():
    client = make_client(api_http())
    result = client.search_items("thinkpad x1", limit=10)
    assert result["total"] == 2
    assert len(result["results"]) == 2
    first = result["results"][0]
    assert first["item_id"] == "v1|256012345678|0"
    assert "ThinkPad" in first["title"]
    assert first["price"] == {"value": "549.99", "currency": "USD"}
    assert first["buying_options"] == ["FIXED_PRICE"]
    assert first["seller"] == "techdeals_refurb"
    # marketplace header is always sent
    _, kwargs = client._http.last_call("GET", "item_summary/search")
    assert kwargs["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
    assert kwargs["headers"]["Authorization"] == "Bearer APP-TOKEN-123"


def test_search_limit_is_capped_at_200():
    client = make_client(api_http())
    client.search_items("x", limit=500)
    url, kwargs = client._http.last_call("GET", "item_summary/search")
    params = dict(kwargs["params"])
    assert params["limit"] == "200"


def test_search_empty_results():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "item_summary/search", FakeResponse(200, SEARCH_EMPTY))
    result = make_client(http).search_items("zzz-no-such-thing")
    assert result["total"] == 0
    assert result["results"] == []


def test_search_api_error_maps_error_id():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add(
        "GET",
        "item_summary/search",
        FakeResponse(
            400,
            {"errors": [{"errorId": 12000, "domain": "API_BROWSE",
                         "message": "Invalid filter."}]},
        ),
    )
    with pytest.raises(EbayApiError) as exc_info:
        make_client(http).search_items("x", filters=["filter=bogus"])
    assert exc_info.value.error_id == 12000
    assert exc_info.value.status_code == 400


def test_search_invalid_token_raises_auth_error():
    http = FakeHttp().add("POST", "oauth2/token", token_response())
    http.add("GET", "item_summary/search", ERROR_OAUTH)
    with pytest.raises(EbayAuthError, match="1001"):
        make_client(http).search_items("x")


# ---------------------------------------------------------------------------
# Browse: getItem
# ---------------------------------------------------------------------------
def test_get_item_happy_path():
    http = api_http().add("GET", "buy/browse/v1/item/", FakeResponse(200, ITEM_FIXED))
    item = make_client(http).get_item("v1|256012345678|0")
    assert isinstance(item, ItemDetail)
    assert item.title.startswith("Lenovo ThinkPad")
    assert item.price.value == "549.99"
    assert item.available_quantity == 3
    assert item.shipping_cost.value == "5.00"
    assert "FIXED_PRICE" in item.buying_options


def test_get_item_not_found():
    http = api_http().add("GET", "buy/browse/v1/item/", ERROR_NOT_FOUND)
    with pytest.raises(EbayNotFoundError):
        make_client(http).get_item("v1|000|0")


def test_get_item_auction_fields():
    http = api_http().add("GET", "buy/browse/v1/item/", FakeResponse(200, ITEM_AUCTION))
    item = make_client(http).get_item("v1|256087654321|0")
    assert "AUCTION" in item.buying_options
    assert item.end_time == "2026-09-25T18:00:00.000Z"


# ---------------------------------------------------------------------------
# Offer API (user token)
# ---------------------------------------------------------------------------
def test_place_proxy_bid_happy_path():
    http = api_http().add("POST", "place_proxy_bid", BID_OK)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    result = client.place_proxy_bid("v1|256087654321|0", max_amount="1300.00", currency="USD")
    assert result["bidId"] == "bid-9f3a2c"
    _, kwargs = http.last_call("POST", "place_proxy_bid")
    assert kwargs["headers"]["Authorization"] == "Bearer USER-TOKEN-1"
    assert kwargs["json"] == {"maxAmount": {"value": "1300.00", "currency": "USD"}}


def test_place_proxy_bid_without_user_token_raises_before_http():
    http = api_http().add("POST", "place_proxy_bid", BID_OK)
    client = make_client(http)  # no user token
    with pytest.raises(EbayAuthError, match="EBAY_USER_ACCESS_TOKEN"):
        client.place_proxy_bid("v1|256087654321|0", max_amount="1300.00", currency="USD")
    assert http.count("POST", "place_proxy_bid") == 0


def test_place_proxy_bid_api_error():
    http = api_http().add("POST", "place_proxy_bid", ERROR_BID_TOO_LOW)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    with pytest.raises(EbayApiError) as exc_info:
        client.place_proxy_bid("v1|256087654321|0", max_amount="1.00", currency="USD")
    assert exc_info.value.error_id == 12009


def test_get_bidding_happy_path():
    http = api_http().add("GET", "buy/offer/v1/bidding/", BIDDING_INFO)
    client = make_client(http, user_access_token="USER-TOKEN-1")
    data = client.get_bidding("v1|256087654321|0")
    assert data["maxBidPrice"]["value"] == "1250.00"


# ---------------------------------------------------------------------------
# Order API v2 guest checkout
# ---------------------------------------------------------------------------
def _checkout_http():
    http = api_http()
    http.add("POST", "guest_checkout_session/initiate", CHECKOUT_SESSION_OK)
    http.add("POST", "place_order", PLACE_ORDER_OK)
    http.add("GET", "guest_purchase_order/", GUEST_ORDER_OK)
    return http


def test_initiate_guest_checkout_happy_path():
    client = make_client(_checkout_http())
    session = client.initiate_guest_checkout(
        line_items=[{"itemId": "v1|256012345678|0", "quantity": 1}],
        contact_email="buyer@example.com",
        shipping_address={"recipient": "Jane Doe", "countryCode": "US"},
    )
    assert session["checkoutSessionId"] == "sess-abc-123"
    _, kwargs = client._http.last_call("POST", "guest_checkout_session/initiate")
    body = kwargs["json"]
    assert body["lineItemInputs"] == [{"itemId": "v1|256012345678|0", "quantity": 1}]
    assert body["contact"] == {"email": "buyer@example.com"}
    assert body["shippingAddress"]["countryCode"] == "US"


def test_place_guest_order_happy_path():
    client = make_client(_checkout_http())
    order = client.place_guest_order("sess-abc-123")
    assert order["purchaseOrderId"] == "po-xyz-789"


def test_get_guest_purchase_order_happy_path():
    client = make_client(_checkout_http())
    data = client.get_guest_purchase_order("po-xyz-789")
    assert data["orderStatus"] == "CREATED"


def test_guest_checkout_uses_app_token_with_guest_scope():
    client = make_client(_checkout_http())
    client.initiate_guest_checkout([{"itemId": "x", "quantity": 1}], "a@b.c", {})
    _, tok_kwargs = client._http.last_call("POST", "oauth2/token")
    assert "buy.guest.order" in tok_kwargs["content"]
