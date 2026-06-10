"""NSEMarketDataAdapter — wraps existing nse.py behind IMarketDataProvider.

This is the ONLY file in the codebase that may import from app.data_sources.nse.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import date, datetime

from app.application.ports.market_data import IMarketDataProvider, DataUnavailableError
from app.domain.market.entities import (
    Quote, OptionChainSnapshot, Candle, StrikeData, MarketSnapshot,
)
from app.domain.market.value_objects import (
    Symbol, Price, Volume, OI, IVPercent, OptionType, CandleInterval, Expiry,
)

logger = logging.getLogger(__name__)


class NSEMarketDataAdapter(IMarketDataProvider):
    """Wraps the existing NseData god-class behind the IMarketDataProvider port."""

    @property
    def provider_name(self) -> str:
        return "nse"

    # ── Quotes ───────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: Symbol) -> Quote:
        raise DataUnavailableError("NSE adapter: use get_market_snapshot for index quotes")

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        raise DataUnavailableError("NSE adapter does not support batch quotes")

    # ── Option chains ─────────────────────────────────────────────────────────

    async def get_option_chain(
        self, symbol: Symbol, expiry: date | None = None,
    ) -> OptionChainSnapshot:
        from app.data_sources.nse import nse_data
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, nse_data.get_option_chain, symbol)
        if not raw or not raw.get("records", {}).get("data"):
            raise DataUnavailableError(f"NSE: empty option chain for {symbol}")
        return self._parse_option_chain(symbol, raw)

    def _parse_option_chain(self, symbol: Symbol, raw: dict) -> OptionChainSnapshot:
        records = raw.get("records", {})
        expiry_str = (records.get("expiryDates") or [""])[0]
        try:
            expiry = Expiry.from_nse(expiry_str)
        except Exception:
            expiry = Expiry(date.today())

        spot = float(raw.get("records", {}).get("underlyingValue", 0) or 0)
        strikes: list[StrikeData] = []

        for row in records.get("data", []):
            sp = float(row.get("strikePrice", 0) or 0)
            if sp <= 0:
                continue
            for ot_str, ot_enum in (("CE", OptionType.CALL), ("PE", OptionType.PUT)):
                d = row.get(ot_str) or {}
                if not d:
                    continue
                ltp = float(d.get("lastPrice", 0) or 0)
                uv  = float(d.get("underlyingValue", 0) or 0)
                spot = spot or uv
                strikes.append(StrikeData(
                    strike=sp,
                    option_type=ot_enum,
                    ltp=Price(ltp),
                    oi=OI(float(d.get("openInterest", 0) or 0)),
                    oi_change=float(d.get("changeinOpenInterest", 0) or 0),
                    volume=Volume(int(float(d.get("totalTradedVolume", 0) or 0))),
                    iv=IVPercent(float(d.get("impliedVolatility", 0) or 0)),
                    bid=Price(float(d.get("bidprice", 0) or 0)),
                    ask=Price(float(d.get("askPrice", 0) or 0)),
                    underlying_value=Price(uv),
                ))

        if not strikes:
            raise DataUnavailableError(f"NSE: zero strikes parsed for {symbol}")

        return OptionChainSnapshot(
            symbol=symbol, expiry=expiry, spot_price=Price(spot),
            strikes=strikes, source="nse",
        )

    # ── Candles ───────────────────────────────────────────────────────────────

    async def get_intraday_candles(
        self, symbol: Symbol, interval: CandleInterval = CandleInterval.FIVE_MIN,
    ) -> list[Candle]:
        raise DataUnavailableError("NSE adapter: no intraday candles — use Kite")

    async def get_daily_ohlcv(
        self, symbol: Symbol, days: int = 400,
    ) -> list[Candle]:
        from app.data_sources.nse import nse_data
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, nse_data.get_ohlcv_daily, symbol, days)
        if df is None or df.empty:
            raise DataUnavailableError(f"NSE: no daily OHLCV for {symbol}")
        return [
            Candle(
                symbol=symbol, timestamp=idx,
                open=Price(float(row.Open)), high=Price(float(row.High)),
                low=Price(float(row.Low)), close=Price(float(row.Close)),
                volume=Volume(int(row.Volume)),
            )
            for idx, row in df.iterrows()
        ]

    # ── Market snapshot ───────────────────────────────────────────────────────

    async def get_market_snapshot(self) -> MarketSnapshot:
        from app.data_sources.nse import nse_data
        loop = asyncio.get_running_loop()
        snap = await loop.run_in_executor(None, nse_data.get_market_snapshot)
        return MarketSnapshot(
            india_vix=float(snap.get("indiaVix", 0) or 0),
            nifty_ltp=float(snap.get("nifty", 0) or 0),
            nifty_direction=snap.get("niftyDirection", ""),
            banknifty_ltp=float(snap.get("banknifty", 0) or 0),
            breadth=float(snap.get("breadth", 0.5) or 0.5),
            sp500_change=snap.get("sp500Change"),
            usd_inr=float(snap.get("usdInr", 0) or 0),
            extra=snap,
        )

    async def get_fo_universe(self) -> list[Symbol]:
        from app.data_sources.kite import get_fo_universe
        return [Symbol(s) for s in get_fo_universe()]

    async def health_check(self) -> bool:
        try:
            from app.data_sources.nse import nse_data
            loop = asyncio.get_running_loop()
            vix = await loop.run_in_executor(None, nse_data.get_india_vix)
            return vix is not None and vix > 0
        except Exception:
            return False
