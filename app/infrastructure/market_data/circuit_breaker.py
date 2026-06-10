"""Circuit breaker wrapping any IMarketDataProvider.

States: CLOSED (normal) → OPEN (failing, reject fast) → HALF_OPEN (testing recovery)
"""
from __future__ import annotations
import asyncio
import logging
import time
from enum import Enum
from datetime import date

from app.application.ports.market_data import (
    IMarketDataProvider, DataUnavailableError, CircuitOpenError,
)
from app.domain.market.entities import Quote, OptionChainSnapshot, Candle, MarketSnapshot
from app.domain.market.value_objects import Symbol, CandleInterval

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerProvider(IMarketDataProvider):
    """Wraps any IMarketDataProvider with circuit-breaker protection."""

    def __init__(
        self,
        provider:          IMarketDataProvider,
        failure_threshold: int   = 5,
        cooldown_secs:     int   = 60,
        half_open_max:     int   = 1,   # probes allowed in HALF_OPEN before decision
    ) -> None:
        self._provider  = provider
        self._threshold = failure_threshold
        self._cooldown  = cooldown_secs
        self._state     = CircuitState.CLOSED
        self._failures  = 0
        self._half_probes = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def provider_name(self) -> str:
        return f"cb({self._provider.provider_name})[{self._state.value}]"

    @property
    def state(self) -> CircuitState:
        return self._state

    def _check_state(self) -> None:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._cooldown:
                self._state       = CircuitState.HALF_OPEN
                self._half_probes = 0
                logger.info("[CB] %s → HALF_OPEN after %.0fs", self._provider.provider_name, elapsed)
            else:
                remaining = int(self._cooldown - elapsed)
                raise CircuitOpenError(
                    f"Circuit OPEN for {self._provider.provider_name} — "
                    f"retry in {remaining}s"
                )
        if self._state == CircuitState.HALF_OPEN:
            if self._half_probes >= 1:
                raise CircuitOpenError(
                    f"Circuit HALF_OPEN for {self._provider.provider_name} — "
                    "waiting for probe result"
                )
            self._half_probes += 1

    def _on_success(self) -> None:
        if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            logger.info(
                "[CB] %s → CLOSED (recovered after %d failures)",
                self._provider.provider_name, self._failures,
            )
        self._state    = CircuitState.CLOSED
        self._failures = 0

    def _on_failure(self, exc: Exception) -> None:
        self._failures += 1
        if self._state == CircuitState.HALF_OPEN:
            self._state     = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.error("[CB] %s → OPEN (probe failed: %s)", self._provider.provider_name, exc)
        elif self._failures >= self._threshold:
            self._state     = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.error(
                "[CB] %s → OPEN after %d failures (last: %s)",
                self._provider.provider_name, self._failures, exc,
            )

    async def _wrap(self, coro):
        # _check_state() raises CircuitOpenError if OPEN — propagates before try block
        self._check_state()
        try:
            result = await coro
            self._on_success()
            return result
        except CircuitOpenError:
            raise   # don't wrap circuit errors from nested providers
        except Exception as exc:
            self._on_failure(exc)
            raise DataUnavailableError(
                f"{self._provider.provider_name} failed: {exc}"
            ) from exc

    async def get_option_chain(self, symbol: Symbol, expiry: date | None = None) -> OptionChainSnapshot:
        return await self._wrap(self._provider.get_option_chain(symbol, expiry))

    async def get_quote(self, symbol: Symbol) -> Quote:
        return await self._wrap(self._provider.get_quote(symbol))

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        return await self._wrap(self._provider.get_quote_batch(symbols))

    async def get_intraday_candles(self, symbol: Symbol, interval: CandleInterval = CandleInterval.FIVE_MIN) -> list[Candle]:
        return await self._wrap(self._provider.get_intraday_candles(symbol, interval))

    async def get_daily_ohlcv(self, symbol: Symbol, days: int = 400) -> list[Candle]:
        return await self._wrap(self._provider.get_daily_ohlcv(symbol, days))

    async def get_market_snapshot(self) -> MarketSnapshot:
        return await self._wrap(self._provider.get_market_snapshot())

    async def get_fo_universe(self) -> list[Symbol]:
        return await self._provider.get_fo_universe()

    async def health_check(self) -> bool:
        return self._state != CircuitState.OPEN and await self._provider.health_check()
