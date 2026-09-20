"""Exception hierarchy for the eBay MCP connector."""


class EbayError(Exception):
    """Base class for all connector errors."""


class EbayConfigError(EbayError):
    """Raised when required configuration (env vars) is missing or invalid."""


class EbayAuthError(EbayError):
    """Raised for OAuth/token problems (invalid client credentials, expired or
    missing user token, insufficient scopes)."""


class EbayApiError(EbayError):
    """Raised when the eBay API returns an error payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_id: int | str | None = None,
        errors: list | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_id = error_id
        self.errors = errors or []


class EbayNotFoundError(EbayApiError):
    """Raised when an item/order does not exist (HTTP 404)."""


class ItemNotAuctionError(EbayError):
    """Raised when place_bid is called on a non-auction (fixed-price) listing."""


class ItemNotAvailableError(EbayError):
    """Raised when trying to buy an item that is out of stock or ended."""


class PriceChangedError(EbayError):
    """Raised at confirm time when the listing price changed since the quote.
    The mutating action is NOT executed."""


class ConfirmationError(EbayError):
    """Raised when a confirmation token is missing, expired, or tampered with."""
