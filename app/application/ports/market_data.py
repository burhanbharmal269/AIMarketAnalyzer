"""IMarketDataProvider — secondary port.

All market data consumers depend on THIS interface, never on broker SDKs directly.

Exception classes are re-exported from app.core.exceptions so there is exactly
one class object per exception type across the codebase — isinstance() checks
work correctly regardless of which module performed the import.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from app.domain.market.entities import Quote, OptionChainSnapshot, Candle, MarketSnapshot
from app.domain.market.value_objects import Symbol, CandleInterval

# Single-source exceptions — import from core so every import site shares
# the same class object (critical for isinstance / pytest.raises).
from app.core.exceptions import (  # noqa: F401
    DataUnavailableError,
    RateLimitError,
    CircuitOpenError,
    AuthenticationError,
)

MarketDataError = DataUnavailableError  # legacy alias


class IMarketDataProvider(ABC):
    """Secondary port — the only abstraction market consumers should import."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for logging and metrics."""

    # ── Quotes ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_quote(self, symbol: Symbol) -> Quote:
        """Return best bid/ask snapshot. Raises DataUnavailableError on failure."""

    @abstractmethod
    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """Batch quote fetch — more efficient than N individual calls."""

    # ── Option chains ─────────────────────────────────────────────────────────

    @abstractmethod
    async def get_option_chain(
        self,
        symbol: Symbol,
        expiry: date | None = None,
    ) -> OptionChainSnapshot:
        """Full option chain for one symbol. expiry=None → nearest expiry."""

    # ── Candles ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_intraday_candles(
        self,
        symbol: Symbol,
        interval: CandleInterval = CandleInterval.FIVE_MIN,
    ) -> list[Candle]:
        """Today's intraday OHLCV candles."""

    @abstractmethod
    async def get_daily_ohlcv(
        self,
        symbol: Symbol,
        days: int = 400,
    ) -> list[Candle]:
        """Daily OHLCV for technical indicator computation (EMA200 needs 400+ days)."""

    # ── Market snapshot ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_market_snapshot(self) -> MarketSnapshot:
        """VIX, Nifty LTP, breadth, global cues — aggregated market state."""

    # ── Universe ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_fo_universe(self) -> list[Symbol]:
        """Full F&O scan universe for this provider."""

    # ── Health ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def health_check(self) -> bool:
        """True if provider is reachable and authenticated."""
