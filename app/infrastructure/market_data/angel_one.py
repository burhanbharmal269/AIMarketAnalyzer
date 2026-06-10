"""AngelOneMarketDataAdapter — wraps existing angel.py behind IMarketDataProvider.

This is the ONLY file in the codebase that may import from app.data_sources.angel.
All other code must go through IMarketDataProvider.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import date, datetime

from app.application.ports.market_data import (
    IMarketDataProvider, DataUnavailableError, RateLimitError,
)
from app.domain.market.entities import (
    Quote, OptionChainSnapshot, Candle, StrikeData, MarketSnapshot,
)
from app.domain.market.value_objects import (
    Symbol, Price, Volume, OI, IVPercent, OptionType, CandleInterval, Expiry,
)

logger = logging.getLogger(__name__)


class AngelOneMarketDataAdapter(IMarketDataProvider):
    """Translates Angel One SmartAPI responses into normalised domain objects.

    Delegates all network calls to existing angel.py — no duplication.
    run_in_executor wraps synchronous SDK calls so the event loop is never blocked.
    """

    @property
    def provider_name(self) -> str:
        return "angel_one"

    # ── Quotes ───────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: Symbol) -> Quote:
        from app.data_sources.angel import get_ltp_batch
        loop = asyncio.get_running_loop()
        result: dict = await loop.run_in_executor(None, lambda: get_ltp_batch([symbol]))
        ltp = result.get(symbol)
        if ltp is None:
            raise DataUnavailableError(f"Angel One: no LTP for {symbol}")
        return Quote(
            symbol=symbol, ltp=Price(ltp), bid=Price(0.0), ask=Price(0.0),
            volume=Volume(0), oi=OI(0.0), timestamp=datetime.utcnow(),
        )

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        from app.data_sources.angel import get_ltp_batch
        loop = asyncio.get_running_loop()
        result: dict = await loop.run_in_executor(None, lambda: get_ltp_batch(list(symbols)))
        now = datetime.utcnow()
        return {
            Symbol(sym): Quote(
                symbol=Symbol(sym), ltp=Price(ltp), bid=Price(0.0), ask=Price(0.0),
                volume=Volume(0), oi=OI(0.0), timestamp=now,
            )
            for sym, ltp in result.items()
        }

    # ── Option chains ─────────────────────────────────────────────────────────

    async def get_option_chain(
        self, symbol: Symbol, expiry: date | None = None,
    ) -> OptionChainSnapshot:
        from app.data_sources.angel import (
            get_option_chain as _angel_oc,
            get_stock_option_chain as _angel_stock_oc,
            _INDEX_SYMBOLS,
        )
        loop = asyncio.get_running_loop()

        if symbol in _INDEX_SYMBOLS:
            raw = await loop.run_in_executor(None, _angel_oc, symbol)
        else:
            raw = await loop.run_in_executor(None, _angel_stock_oc, symbol)

        if not raw or not raw.get("records", {}).get("data"):
            raise DataUnavailableError(
                f"Angel One: empty option chain for {symbol}"
            )
        return self._parse_option_chain(symbol, raw)

    def _parse_option_chain(self, symbol: Symbol, raw: dict) -> OptionChainSnapshot:
        records = raw.get("records", {})
        expiry_str = (records.get("expiryDates") or [""])[0]
        try:
            expiry = Expiry.from_nse(expiry_str)
        except Exception:
            expiry = Expiry(date.today())

        spot = 0.0
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
                spot = spot or float(d.get("underlyingValue", 0) or 0)
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
                    underlying_value=Price(spot),
                ))

        if not strikes:
            raise DataUnavailableError(f"Angel One: zero strikes parsed for {symbol}")

        return OptionChainSnapshot(
            symbol=symbol, expiry=expiry, spot_price=Price(spot),
            strikes=strikes, source="angel_one",
        )

    # ── Candles ───────────────────────────────────────────────────────────────

    async def get_intraday_candles(
        self, symbol: Symbol, interval: CandleInterval = CandleInterval.FIVE_MIN,
    ) -> list[Candle]:
        from app.data_sources.angel import get_intraday_candles as _candles
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, _candles, symbol)
        if df is None or df.empty:
            raise DataUnavailableError(f"Angel One: no intraday candles for {symbol}")
        return [
            Candle(
                symbol=symbol,
                timestamp=row.datetime if hasattr(row, "datetime") else row.Index,
                open=Price(float(row.open)), high=Price(float(row.high)),
                low=Price(float(row.low)), close=Price(float(row.close)),
                volume=Volume(int(row.volume)),
            )
            for row in df.itertuples()
        ]

    async def get_daily_ohlcv(
        self, symbol: Symbol, days: int = 400,
    ) -> list[Candle]:
        from app.data_sources.angel import get_daily_ohlcv as _daily
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, _daily, symbol, days)
        if df is None or df.empty:
            raise DataUnavailableError(f"Angel One: no daily OHLCV for {symbol}")
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
        # VIX not available from Angel One — use NSE for this
        raise DataUnavailableError("Use NSEMarketDataAdapter for market snapshot")

    # ── Universe ──────────────────────────────────────────────────────────────

    async def get_fo_universe(self) -> list[Symbol]:
        from app.data_sources.angel import get_fo_universe
        return [Symbol(s) for s in get_fo_universe()]

    # ── Health ────────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        from app.data_sources.angel import angel_session
        return angel_session.ensure_connected()
