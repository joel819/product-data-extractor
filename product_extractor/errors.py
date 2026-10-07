"""Errors the API turns into clean JSON responses: {"error": code, "message": text}."""


class ExtractError(Exception):
    code = "extract_error"
    http_status = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidUrl(ExtractError):
    code = "invalid_url"
    http_status = 422


class BlockedAddress(ExtractError):
    """The URL points at a private, loopback, link-local or otherwise non-public address (SSRF guard)."""
    code = "blocked_address"
    http_status = 400


class BlockedByRobots(ExtractError):
    code = "blocked_by_robots"
    http_status = 403


class FetchFailed(ExtractError):
    code = "fetch_failed"
    http_status = 502


class FetchTimeout(ExtractError):
    code = "fetch_timeout"
    http_status = 504


class PageTooLarge(ExtractError):
    code = "page_too_large"
    http_status = 422


class NotHtml(ExtractError):
    code = "not_html"
    http_status = 422


class Unauthorized(ExtractError):
    code = "unauthorized"
    http_status = 401
