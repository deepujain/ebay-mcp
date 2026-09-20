"""Live integration tests against the real eBay SANDBOX API.

These are skipped unless EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are set.
Read-only tests run with any sandbox keyset. Mutating tests additionally
require EBAY_RUN_LIVE_MUTATIONS=1 AND EBAY_ENV=sandbox (never production).

Run:
    EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... pytest -m integration
"""
import os

import pytest

from ebay_mcp.auth import ApplicationTokenProvider
from ebay_mcp.client import EbayClient
from ebay_mcp.config import get_config

pytestmark = pytest.mark.integration

HAS_CREDS = bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))
ALLOW_MUTATIONS = (
    HAS_CREDS
    and os.environ.get("EBAY_RUN_LIVE_MUTATIONS") == "1"
    and os.environ.get("EBAY_ENV", "sandbox") == "sandbox"
)

needs_creds = pytest.mark.skipif(not HAS_CREDS, reason="EBAY_CLIENT_ID/EBAY_CLIENT_SECRET not set")
needs_mutations = pytest.mark.skipif(
    not ALLOW_MUTATIONS,
    reason="set EBAY_RUN_LIVE_MUTATIONS=1 with EBAY_ENV=sandbox to run live mutations",
)


def live_client() -> EbayClient:
    config = get_config()
    assert config.env == "sandbox", "integration tests only run against the sandbox"
    return EbayClient(config, token_provider=ApplicationTokenProvider(config))


@needs_creds
class TestSandboxRead:
    def test_application_token(self):
        config = get_config()
        token = ApplicationTokenProvider(config).get_token()
        assert token and len(token) > 10

    def test_search_returns_results(self):
        result = live_client().search_items("thinkpad", limit=3)
        assert result["total"] >= 0
        assert isinstance(result["results"], list)

    def test_get_item_round_trip(self):
        client = live_client()
        result = client.search_items("thinkpad", limit=1)
        if not result["results"]:
            pytest.skip("sandbox returned no listings for the probe query")
        item_id = result["results"][0]["item_id"]
        item = client.get_item(item_id)
        assert item.item_id == item_id
        assert item.title


@needs_mutations
class TestSandboxMutations:
    def test_guest_checkout_session_flow(self):
        """Initiate a guest checkout session in the sandbox (no order placed)."""
        client = live_client()
        result = client.search_items("thinkpad", limit=5)
        fixed = [r for r in result["results"] if "FIXED_PRICE" in r["buying_options"]]
        if not fixed:
            pytest.skip("no fixed-price sandbox listings for the probe query")
        session = client.initiate_guest_checkout(
            line_items=[{"itemId": fixed[0]["item_id"], "quantity": 1}],
            contact_email="sandbox-buyer@example.com",
            shipping_address={
                "recipient": "Sandbox Buyer",
                "addressLine1": "1 Test Way",
                "city": "Austin",
                "stateOrProvince": "TX",
                "postalCode": "78701",
                "countryCode": "US",
            },
        )
        assert session.get("checkoutSessionId")
