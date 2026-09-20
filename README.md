# eBay MCP Connector

An MCP server (streamable HTTP) exposing eBay's **Buy APIs** as tools:
search listings, inspect items, watch for price drops, bid on auctions, and
buy fixed-price items via guest checkout.

## Works with

This is a standard [Model Context Protocol](https://modelcontextprotocol.io/)
server — nothing in it is tied to any single assistant. Run it yourself
(below) and point any MCP-compatible client at `http://127.0.0.1:8000/mcp`:

- **Meta Muse**, **Claude / Claude Code** (Anthropic), **ChatGPT** (OpenAI)
- **Cursor**, **Windsurf**, **Cline**, and other MCP-capable coding assistants
- Any custom agent built on an MCP SDK (Python, TypeScript, …)

## Tools

| Tool | Effect | Approval |
|---|---|---|
| `search_listings` | Search active listings (keyword, price range, condition, format, sort) | immediate (read-only) |
| `get_item_details` | Full details for one listing | immediate (read-only) |
| `watch_item` | Add listing to the server-side watchlist, optional target price | immediate (read-only) |
| `list_watches` | Re-check all watches: price drops, target hits, stock-outs, ended | immediate (read-only) |
| `remove_watch` | Stop watching a listing | immediate (read-only) |
| `get_order_status` | Look up a guest order placed through this connector | immediate (read-only) |
| `place_bid` | Proxy (max) bid on an **auction** item | **two-phase** |
| `buy_now` | Buy a fixed-price item via **guest checkout** | **two-phase** |

### Two-phase approval contract

`place_bid` and `buy_now` never spend on the first call. The first call
(`confirm=false`, the default) fetches a fresh quote and returns
`{"status": "pending_confirmation", ...}` with a `confirmation_token` that is
HMAC-SHA256-signed (server's client secret), binds item id + price +
quantity/max-bid, and expires after 15 minutes.

The second call (`confirm=true` + token) re-fetches the listing and aborts —
placing nothing — if the price moved (`PriceChangedError`), the item sold out
or ended (`ItemNotAvailableError`), or the token is expired/tampered
(`ConfirmationError`). Only then does it call eBay.

## Required OAuth scopes

| Scope | Used for | Token type |
|---|---|---|
| `https://api.ebay.com/oauth/api_scope` | Browse API: search, item details | application (client credentials) |
| `https://api.ebay.com/oauth/api_scope/buy.guest.order` | Order API v2: guest checkout | application (client credentials) |
| `https://api.ebay.com/oauth/api_scope/buy.offer.auction` | Offer API: `place_proxy_bid` | **user** (authorization code grant) |

Everything except auction bidding works with the application token alone
(no user login). Bidding needs one browser consent per user.

## Setup

1. **eBay developer account** (free, ~5 min, not scriptable — requires
   accepting the developer agreement as yourself):
   - Sign up at https://developer.ebay.com and verify your email.
   - New accounts go through a review before keys work — allow at least one
     business day.
   - Go to **My Keys** → create a keyset → note the **App ID** (client_id)
     and **Cert ID** (client_secret). Create both **Sandbox** and
     **Production** keysets.
   - Under the keyset, add a **RuName** (redirect URI name) — needed only for
     the optional bidding consent flow.
2. **Configure:**
   ```bash
   cp .env.example .env   # then fill in EBAY_CLIENT_ID / EBAY_CLIENT_SECRET
   ```
   Start with `EBAY_ENV=sandbox` (test data, free, no real money moves).
3. **Install & run** (always start with `EBAY_ENV=sandbox` — test data, no
   real money moves):

   Local Python:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   .venv/bin/python -m ebay_mcp.server
   # listening on 127.0.0.1:8000 by default (EBAY_HOST / EBAY_PORT)
   ```

   Or Docker (the image bakes in a `/healthz` liveness probe; the watchlist
   lives in `/data`, so mount a volume to keep watches across restarts):
   ```bash
   cp .env.example .env   # then fill in your keys
   docker build -t ebay-mcp .
   docker run --env-file .env -p 8000:8000 -v ebay-data:/data ebay-mcp
   ```
4. **User consent for bidding** (only needed for `place_bid`): print the
   consent URL, open it in a browser, approve, then exchange the returned
   `?code=...` for tokens:
   ```bash
   .venv/bin/python - <<'EOF'
   from ebay_mcp.config import get_config
   from ebay_mcp.auth import build_authorize_url, exchange_code_for_tokens
   config = get_config()
   print(build_authorize_url(config))   # open this in a browser and approve
   # then:
   # tokens = exchange_code_for_tokens(config, code="<code from redirect>")
   # print(tokens["access_token"], tokens["refresh_token"])
   EOF
   ```
   Set `EBAY_USER_ACCESS_TOKEN` / `EBAY_USER_REFRESH_TOKEN` in `.env` from the
   exchange output. Everything except auction bidding works with the
   application token alone (no browser step).
5. **Connect a client:** point any MCP-compatible client at
   `http://<host>:<port>/mcp`. eBay credentials stay in this server's
   environment — the client never sees them.

## Running tests

```bash
.venv/bin/python -m pytest -q            # unit tests (mocked HTTP)
.venv/bin/python -m pytest -q -m integration   # live sandbox tests (needs keys)
```

Integration tests are skipped without `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`.
The one live mutation test additionally requires `EBAY_RUN_LIVE_MUTATIONS=1`
**and** `EBAY_ENV=sandbox`; it only opens a guest checkout session, never
places an order.

## Example prompts

1. "Find me a used ThinkPad X1 Carbon under $600 with free shipping."
2. "Watch this auction for the vintage Rolex and alert me if it drops below $2,000."
3. "What's the cheapest Buy-It-Now listing for AirPods Pro 2 in new condition?"
4. "Bid up to $450 on that auction ending today — but confirm the price with me first."
5. "Buy two of those fixed-price phone cases and tell me the order total before you check out."

## Layout

```
src/ebay_mcp/
  config.py     env-var config (no secrets in code)
  errors.py     exception hierarchy
  auth.py       OAuth: app token (cached), user-token helpers, scope constants
  client.py     REST client: Browse / Offer / Order v2 + response normalization
  watchlist.py  server-side watchlist (JSON file)
  tools.py      MCP tool logic incl. the two-phase approval contract
  server.py     FastMCP entrypoint (streamable HTTP)
tests/
  test_config.py / test_auth.py / test_client.py / test_tools.py
  test_integration.py   (skipped without sandbox credentials)
```

## Unverified details

Verified against official eBay docs at build time: Browse search/getItem
paths and params, Offer `GET /bidding/{id}` and `POST
/bidding/{id}/place_proxy_bid`, OAuth token/authorize URLs, and all scope
strings. **Not verifiable from public docs** (flagged `UNVERIFIED` in code):
the Order API v2 guest-checkout REST paths
(`/buy/order/v2/guest_checkout_session/...`), the `place_proxy_bid` request
body shape (`{"maxAmount": {"value", "currency"}}`), the guest-checkout
initiate body shape, and the `estimatedAvailableQuantity` field name. The
sandbox integration tests are the verification vehicle — run them with real
sandbox keys before production use.

Notes:
- Pins `mcp>=1.9,<2` (v1 FastMCP API). v2 renamed FastMCP → MCPServer;
  migrate deliberately, not accidentally.
- eBay's Buy APIs expose no watchlist endpoint, so `watch_item` is
  server-side by design. There is also no REST Buy endpoint for Best Offer
  negotiation — `place_bid` covers auctions only.
- Browse API default quota is 5,000 calls/day (free tier).

## Troubleshooting

- **Keys return auth errors right after signup:** new eBay developer accounts
  are reviewed before keys activate (at least one business day). Sandbox and
  production keysets are separate — approval for one does not imply the other.
- **"Item not available" in sandbox:** sandbox listings are test data with
  short lifespans. Search fresh (`search_listings`) rather than reusing item
  IDs from an earlier session.
- **`place_bid` fails but search works:** bidding needs the *user* token
  (Authorization Code grant) with the `buy.offer.auction` scope — the
  application token alone is not enough. Re-run Setup step 4.
- **Watches disappear after a container restart:** the watchlist is a plain
  JSON file at `EBAY_WATCHLIST_PATH`. In Docker it lives in `/data` — mount
  a volume (see Setup step 3) or the file dies with the container.
- **Guest checkout vs eBay account:** `buy_now` uses eBay's guest checkout —
  no eBay buyer account is needed. The order confirmation email goes to the
  address you supply at confirm time.

## License

MIT — see [LICENSE](LICENSE).
