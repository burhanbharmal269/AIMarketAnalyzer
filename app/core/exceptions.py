"""Domain exception hierarchy.

Every module raises one of these; callers catch the appropriate level.
Never catch the base Exception — always be specific.
"""
from __future__ import annotations


class AppError(Exception):
    """Root of the application exception tree."""
    def __init__(self, message: str, *, code: str = "APP_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ── Data / Market ─────────────────────────────────────────────────────────────

class DataUnavailableError(AppError):
    """Raised when a market data provider cannot return requested data.

    The composite provider catches this to try the next provider in chain.
    """
    def __init__(self, message: str = "Market data unavailable") -> None:
        super().__init__(message, code="DATA_UNAVAILABLE")


class RateLimitError(DataUnavailableError):
    """Provider rate limit hit — caller should back off."""
    def __init__(self, provider: str = "", retry_after: float = 1.0) -> None:
        super().__init__(f"Rate limit exceeded for {provider}")
        self.code = "RATE_LIMIT"
        self.retry_after = retry_after


class AuthenticationError(AppError):
    """Broker or data provider authentication failed."""
    def __init__(self, provider: str = "") -> None:
        super().__init__(f"Authentication failed for {provider}", code="AUTH_FAILED")


# ── Circuit Breaker ────────────────────────────────────────────────────────────

class CircuitOpenError(AppError):
    """Raised by CircuitBreakerProvider when the circuit is OPEN."""
    def __init__(self, message: str = "Circuit breaker OPEN", provider: str = "", cooldown: float = 60.0) -> None:
        super().__init__(message, code="CIRCUIT_OPEN")
        self.provider = provider
        self.cooldown = cooldown


# ── Domain / Business Logic ────────────────────────────────────────────────────

class RiskViolationError(AppError):
    """Raised when a candidate fails a hard risk gate."""
    def __init__(self, reason: str) -> None:
        super().__init__(reason, code="RISK_VIOLATION")


class InvalidSignalError(AppError):
    """Signal data is malformed or missing required fields."""
    def __init__(self, field: str = "") -> None:
        super().__init__(
            f"Invalid signal — missing or malformed field: {field}",
            code="INVALID_SIGNAL",
        )


class ScanInProgressError(AppError):
    """A scan is already running — concurrent scans are not allowed."""
    def __init__(self) -> None:
        super().__init__("Scan already in progress", code="SCAN_IN_PROGRESS")


# ── Execution / Broker ─────────────────────────────────────────────────────────

class OrderRejectedError(AppError):
    """Broker rejected the order."""
    def __init__(self, reason: str = "", broker_code: str = "") -> None:
        super().__init__(f"Order rejected: {reason} ({broker_code})", code="ORDER_REJECTED")
        self.broker_code = broker_code


class InsufficientMarginError(AppError):
    """Not enough margin to place the order."""
    def __init__(self, required: float = 0, available: float = 0) -> None:
        super().__init__(
            f"Insufficient margin: need ₹{required:,.0f}, have ₹{available:,.0f}",
            code="INSUFFICIENT_MARGIN",
        )


# ── Configuration ──────────────────────────────────────────────────────────────

class ConfigurationError(AppError):
    """Missing or invalid configuration — fail fast at startup."""
    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(
            f"Configuration error: {key}" + (f" — {detail}" if detail else ""),
            code="CONFIG_ERROR",
        )


# ── Cache ──────────────────────────────────────────────────────────────────────

class CacheError(AppError):
    """Non-fatal cache I/O error — callers should degrade gracefully."""
    def __init__(self, message: str = "Cache error") -> None:
        super().__init__(message, code="CACHE_ERROR")
