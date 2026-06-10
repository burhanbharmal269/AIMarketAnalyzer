"""Shared test fixtures for the entire test suite.

Provides:
  - FakeMarketDataProvider  — in-memory, no network calls
  - FakeAIProvider          — deterministic, no LLM calls
  - Sample domain objects   — Candidate, OptionChainSnapshot, Portfolio
"""
from __future__ import annotations
import asyncio
from datetime import date
from typing import AsyncGenerator
import pytest

from app.application.ports.market_data import IMarketDataProvider, CandleInterval
from app.application.ports.ai import IAIProvider, AIAnalysis
from app.application.ports.cache import ICacheProvider
from app.domain.market.entities import (
    Quote, Candle, OptionChainSnapshot, StrikeData, MarketSnapshot
)
from app.domain.market.value_objects import Symbol, Price, Volume, OI, IVPercent, OptionType, Expiry
from app.domain.risk.entities import Portfolio
from app.domain.signal.entities import Candidate, ScoreBreakdown
from app.domain.signal.value_objects import Direction


# ── Fake providers ────────────────────────────────────────────────────────────

class FakeMarketDataProvider(IMarketDataProvider):
    """Deterministic in-memory market data — no network, no randomness."""

    provider_name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.call_counts: dict[str, int] = {}

    def _count(self, method: str) -> None:
        self.call_counts[method] = self.call_counts.get(method, 0) + 1

    async def get_quote(self, symbol: Symbol) -> Quote:
        self._count("get_quote")
        if self.fail:
            from app.core.exceptions import DataUnavailableError
            raise DataUnavailableError(f"Fake fail for {symbol}")
        return Quote(
            symbol=symbol,
            ltp=Price(100.0),
            bid=Price(99.9),
            ask=Price(100.1),
            volume=Volume(100_000),
            oi=OI(500_000),
            timestamp=__import__("datetime").datetime.utcnow(),
        )

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        self._count("get_quote_batch")
        return {s: await self.get_quote(s) for s in symbols}

    async def get_option_chain(
        self, symbol: Symbol, expiry: date | None = None
    ) -> OptionChainSnapshot:
        self._count("get_option_chain")
        legs = []
        for k in range(95, 106):
            k_f = float(k)
            legs.append(StrikeData(
                strike=k_f,
                option_type=OptionType.CALL,
                ltp=Price(max(0.5, 102.0 - k_f)),
                oi=OI(1_000_000 if k_f > 100 else 500_000),
                oi_change=OI(10_000),
                volume=Volume(10_000),
                iv=IVPercent(20.0),
            ))
            legs.append(StrikeData(
                strike=k_f,
                option_type=OptionType.PUT,
                ltp=Price(max(0.5, k_f - 98.0)),
                oi=OI(800_000 if k_f < 100 else 400_000),
                oi_change=OI(8_000),
                volume=Volume(8_000),
                iv=IVPercent(22.0),
            ))
        return OptionChainSnapshot(
            symbol=symbol,
            spot_price=Price(100.0),
            expiry=Expiry(date(2026, 6, 26)),
            strikes=legs,
            source="fake",
            fetched_at=__import__("datetime").datetime.utcnow(),
        )

    async def get_intraday_candles(
        self, symbol: Symbol, interval: CandleInterval
    ) -> list[Candle]:
        self._count("get_intraday_candles")
        return [
            Candle(
                symbol=symbol,
                timestamp=__import__("datetime").datetime(2026, 6, 10, 9, i * 5),
                open=Price(99.0), high=Price(101.0),
                low=Price(98.5), close=Price(100.0),
                volume=Volume(5_000),
            )
            for i in range(1, 20)
        ]

    async def get_daily_ohlcv(self, symbol: Symbol, days: int = 400) -> list[Candle]:
        self._count("get_daily_ohlcv")
        return [
            Candle(
                symbol=symbol,
                timestamp=__import__("datetime").datetime(2026, 1, i + 1),
                open=Price(95.0 + i * 0.1),
                high=Price(97.0 + i * 0.1),
                low=Price(94.0 + i * 0.1),
                close=Price(96.0 + i * 0.1),
                volume=Volume(50_000),
            )
            for i in range(min(days, 200))
        ]

    async def get_market_snapshot(self) -> MarketSnapshot:
        self._count("get_market_snapshot")
        return MarketSnapshot(
            nifty_spot=Price(24_000.0),
            banknifty_spot=Price(52_000.0),
            india_vix=15.0,
            nifty_change_pct=0.5,
        )

    async def get_fo_universe(self) -> list[Symbol]:
        self._count("get_fo_universe")
        return [Symbol(s) for s in ["RELIANCE", "INFY", "HDFCBANK", "TCS"]]

    async def health_check(self) -> bool:
        return not self.fail


class FakeAIProvider(IAIProvider):
    """Deterministic AI responses — no API calls."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {
            "score": 0.75,
            "confidence": 0.8,
            "recommendation": "BUY",
            "reasoning": "Strong technical setup with bullish OI bias.",
            "regime": "TRENDING_BULL",
            "note": "Bullish momentum",
        }

    async def complete(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.1, max_tokens: int = 500
    ) -> dict:
        return dict(self._response)

    async def batch_complete(
        self, requests: list[dict], max_concurrency: int = 5
    ) -> list[dict]:
        return [dict(self._response) for _ in requests]

    async def health_check(self) -> bool:
        return True


class FakeCache(ICacheProvider):
    """In-memory cache for tests — no Redis."""

    def __init__(self) -> None:
        self._store: dict = {}

    async def get_option_chain(self, symbol: str, expiry_str: str):
        return self._store.get(f"oc:{symbol}:{expiry_str}")

    async def set_option_chain(self, symbol: str, expiry_str: str, data) -> None:
        self._store[f"oc:{symbol}:{expiry_str}"] = data

    async def get_scan_result(self):
        return self._store.get("scan:latest")

    async def set_scan_result(self, data: dict, ttl_secs: int = 900) -> None:
        self._store["scan:latest"] = data

    async def get_vix(self):
        return self._store.get("vix")

    async def set_vix(self, vix: float, ttl_secs: int = 30) -> None:
        self._store["vix"] = vix

    async def get_iv_rank(self, symbol: str):
        return self._store.get(f"ivrank:{symbol}")

    async def set_iv_rank(self, symbol: str, rank: float, ttl_secs: int = 3600) -> None:
        self._store[f"ivrank:{symbol}"] = rank

    async def invalidate(self, pattern: str) -> int:
        keys = [k for k in self._store if k.startswith(pattern.rstrip("*"))]
        for k in keys:
            del self._store[k]
        return len(keys)

    async def health_check(self) -> bool:
        return True


# ── Sample domain objects ──────────────────────────────────────────────────────

@pytest.fixture
def sample_candidate() -> Candidate:
    """A fully-populated Candidate for gate and scoring tests."""
    return Candidate.from_raw({
        "symbol":      "RELIANCE",
        "underlying":  "RELIANCE",
        "instrument":  "RELIANCE30JUN261310CE",
        "direction":   "BUY",
        "spotPrice":   1310.0,
        "entry":       30.0,
        "stopLoss":    20.0,
        "targets":     [45.0, 60.0, 75.0],
        "lotSize":     250,
        "lotPremium":  30.0,
        "ema20":       1300.0,
        "ema50":       1280.0,
        "ema200":      1250.0,
        "rsi":         62.0,
        "adx":         28.0,
        "atr":         0.5,
        "vwap":        1305.0,
        "macd":        2.5,
        "macdSignal":  1.8,
        "macdHist":    0.7,
        "supertrendBull": True,
        "atmIv":       22.0,
        "ivRank":      65.0,
        "optionVolume": 50_000.0,
        "oiChangePct":  15.0,
        "spreadPct":    0.8,
        "pcr":          0.9,
        "maxPain":      1300.0,
        "delta":        0.45,
        "theta":        -0.12,
        "vega":         0.08,
        "vwapConfirmed": True,
        "tf15Aligned":   True,
        "orbBreakout":   False,
        "srBreakout":    True,
        "pdBreakout":    False,
        "indiaVix":      15.5,
        "relVolume":     1.8,
        "resistance":    1350.0,
        "support":       1280.0,
        "poc":           1295.0,
        "setupType":     "Trend",
        "expiryType":    "Monthly",
        "dte":           20,
    })


@pytest.fixture
def clean_portfolio() -> Portfolio:
    """Portfolio with no open positions and no drawdown."""
    return Portfolio(
        daily_pnl_inr=0.0,
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        monthly_pnl_pct=0.0,
        open_position_count=0,
        open_underlyings=set(),
        sector_exposure={},
        loss_streak=0,
        total_capital=500_000,
    )


@pytest.fixture
def stressed_portfolio() -> Portfolio:
    """Portfolio approaching risk limits."""
    return Portfolio(
        daily_pnl_inr=-15_000,
        daily_pnl_pct=-3.5,
        weekly_pnl_pct=-9.0,
        monthly_pnl_pct=-5.0,
        open_position_count=5,
        open_underlyings={"RELIANCE", "INFY", "HDFCBANK", "TCS", "NIFTY"},
        sector_exposure={"banking": 150_000, "it": 100_000},
        loss_streak=4,
        total_capital=500_000,
    )


@pytest.fixture
def fake_market_data() -> FakeMarketDataProvider:
    return FakeMarketDataProvider()


@pytest.fixture
def fake_ai() -> FakeAIProvider:
    return FakeAIProvider()


@pytest.fixture
def fake_cache() -> FakeCache:
    return FakeCache()
