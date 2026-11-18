"""Domain errors, mapped to HTTP responses in app.main."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class NotFound(AppError):
    status_code = 404


class Conflict(AppError):
    status_code = 409


class TooManyRequests(AppError):
    status_code = 429


class UpstreamError(AppError):
    """Riot API failed (bad key, network, 5xx)."""

    status_code = 502


class RiotNotFound(NotFound):
    pass


class RiotRateLimited(TooManyRequests):
    pass
