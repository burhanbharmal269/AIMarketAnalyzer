"""CompositeMarketDataProvider — chain-of-responsibility failover.

Tries providers in order: first success wins.
DataUnavailableError / CircuitOpenError from one provider → try next.
All providers exhausted → raise DataUnavailableError.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import date

from app.application.ports.market_data import (
    IMarketDataProvider, DataUnavailableError, CircuitOpenError,
)
from app.domain.market.entities import Quote, OptionChainSnapshot, Candle, MarketSnapshot
from app.domain.market.value_objects import Symbol, CandleInterval

logger = logging.getLogger(__name__)

_FALLBACK_ERRORS = (DataUnavailableError, CircuitOpenError, NotImplementedError)


class CompositeMarketDataProvider(IMarketDataProvider):
    """Failover chain: primary → fallbacks on DataUnavailableError."""

    def __init__(self, providers: list[IMarketDataProvider]) -> None:
        if not providers:
            raise ValueError("At least one provider required")
        self._providers = providers

    @property
    def provider_name(self) -> str:
        names = ", ".join(p.provider_name for p in self._providers)
        return f"composite({names})"

    async def _try_chain(self, method: str, *args, **kwargs):
        last_exc: Exception | None = None
        for provider in self._providers:
            try:
                result = await getattr(provider, method)(*args, **kwargs)
                if provider != self._providers[0]:
                    logger.info(
                        "[Composite] Fallback to %s for %s(%s)",
                        provider.provider_name, method, args[:1],
                    )
                return result
            except _FALLBACK_ERRORS as exc:
                logger.warning(
                    "[Composite] %s failed for %s(%s): %s — trying next",
                    provider.provider_name, method, args[:1], exc,
                )
                last_exc = exc
            except Exception as exc:
                logger.error(
                    "[Composite] %s unexpected error in %s: %s",
                    provider.provider_name, method, exc,
                )
                last_exc = exc

        raise DataUnavailableError(
            f"All {len(self._providers)} providers failed for {method}({args[:1]})"
        ) from last_exc

    async def get_option_chain(self, symbol: Symbol, expiry: date | None = None) -> OptionChainSnapshot:
        return await self._try_chain("get_option_chain", symbol, expiry)

    async def get_quote(self, symbol: Symbol) -> Quote:
        return await self._try_chain("get_quote", symbol)

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        return await self._try_chain("get_quote_batch", symbols)

    async def get_intraday_candles(self, symbol: Symbol, interval: CandleInterval = CandleInterval.FIVE_MIN) -> list[Candle]:
        return await self._try_chain("get_intraday_candles", symbol, interval)

    async def get_daily_ohlcv(self, symbol: Symbol, days: int = 400) -> list[Candle]:
        return await self._try_chain("get_daily_ohlcv", symbol, days)

    async def get_market_snapshot(self) -> MarketSnapshot:
        return await self._try_chain("get_market_snapshot")

    async def get_fo_universe(self) -> list[Symbol]:
        return await self._providers[0].get_fo_universe()

    async def health_check(self) -> bool:
        checks = await asyncio.gather(
            *[p.health_check() for p in self._providers],
            return_exceptions=True,
        )
        return any(c is True for c in checks)

    def provider_statuses(self) -> list[dict]:
        return [
            {
                "name":  p.provider_name,
                "state": getattr(getattr(p, "_state", None), "value", "N/A"),
            }
            for p in self._providers
        ]
