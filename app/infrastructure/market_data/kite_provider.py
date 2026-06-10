"""KiteMarketDataAdapter — IMarketDataProvider backed by Kite Connect.

All blocking SDK calls are wrapped in loop.run_in_executor() so they
never stall the FastAPI event loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from app.application.ports.market_data import (
    IMarketDataProvider, DataUnavailableError,
)
from app.domain.market.entities import (
    Quote, OptionChainSnapshot, Candle, MarketSnapshot, StrikeData,
)
from app.domain.market.value_objects import Symbol, Price, Volume, OI, IVPercent, CandleInterval, OptionType

logger = logging.getLogger(__name__)

_INTERVAL_MAP: dict[str, str] = {
    CandleInterval.ONE_MIN:    "minute",
    CandleInterval.THREE_MIN:  "3minute",
    CandleInterval.FIVE_MIN:   "5minute",
    CandleInterval.TEN_MIN:    "10minute",
    CandleInterval.FIFTEEN_MIN:"15minute",
    CandleInterval.ONE_HOUR:   "60minute",
    CandleInterval.ONE_DAY:    "day",
}


class KiteMarketDataAdapter(IMarketDataProvider):
    """Adapts the kite data source module to the IMarketDataProvider port."""

    @property
    def provider_name(self) -> str:
        return "kite"

    # ── Quotes ────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: Symbol) -> Quote:
        loop = asyncio.get_event_loop()
        try:
            from app.data_sources.kite import get_quote
            raw = await loop.run_in_executor(None, get_quote, str(symbol))
            if raw is None:
                raise DataUnavailableError(f"No quote for {symbol}")
            return Quote(
                symbol=symbol,
                ltp=raw["ltp"],
                bid=raw.get("bid", 0.0),
                ask=raw.get("ask", 0.0),
                volume=raw.get("volume", 0),
                oi=raw.get("oi", 0),
                timestamp=datetime.utcnow(),
            )
        except DataUnavailableError:
            raise
        except Exception as exc:
            raise DataUnavailableError(f"Kite quote failed for {symbol}: {exc}") from exc

    async def get_quote_batch(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        loop = asyncio.get_event_loop()
        try:
            from app.data_sources.kite import get_ltp_batch
            sym_strs = [str(s) for s in symbols]
            ltps = await loop.run_in_executor(None, get_ltp_batch, sym_strs)
            result: dict[Symbol, Quote] = {}
            for sym in symbols:
                ltp = ltps.get(str(sym))
                if ltp is not None:
                    result[sym] = Quote(symbol=sym, ltp=ltp, bid=0, ask=0, volume=0, oi=0,
                                        timestamp=datetime.utcnow())
            return result
        except Exception as exc:
            raise DataUnavailableError(f"Kite batch quote failed: {exc}") from exc

    # ── Option chains ─────────────────────────────────────────────────────────

    async def get_option_chain(self, symbol: Symbol, expiry: date | None = None) -> OptionChainSnapshot:
        loop = asyncio.get_event_loop()
        try:
            from app.data_sources.kite import get_option_chain
            raw = await loop.run_in_executor(None, get_option_chain, str(symbol), expiry)
            return self._parse_option_chain(symbol, raw)
        except DataUnavailableError:
            raise
        except Exception as exc:
            raise DataUnavailableError(f"Kite option chain failed for {symbol}: {exc}") from exc

    def _parse_option_chain(self, symbol: Symbol, raw: dict) -> OptionChainSnapshot:
        records = raw.get("records", {})
        data    = records.get("data", [])
        udl     = records.get("underlyingValue", 0.0)
        expiry_str = raw.get("_expiry", "")
        try:
            from datetime import date as date_cls
            from app.domain.market.value_objects import Expiry
            raw_date = date_cls.fromisoformat(expiry_str) if expiry_str else date_cls.today()
            expiry = Expiry(raw_date)
        except (ValueError, Exception):
            from datetime import date as date_cls
            from app.domain.market.value_objects import Expiry
            expiry = Expiry(date_cls.today())

        strikes: list[StrikeData] = []
        for row in data:
            for side in ("CE", "PE"):
                leg = row.get(side)
                if not leg:
                    continue
                ltp = leg.get("lastPrice", 0.0)
                strikes.append(StrikeData(
                    strike=row.get("strikePrice", leg.get("strikePrice", 0.0)),
                    option_type=OptionType.CALL if side == "CE" else OptionType.PUT,
                    ltp=ltp,
                    oi=int(leg.get("openInterest", 0)),
                    oi_change=int(leg.get("changeinOpenInterest", 0)),
                    volume=int(leg.get("totalTradedVolume", 0)),
                    iv=leg.get("impliedVolatility", 0.0),
                    bid=leg.get("bidprice", 0.0),
                    ask=leg.get("askPrice", 0.0),
                    underlying_value=udl,
                ))
        return OptionChainSnapshot(
            symbol=symbol,
            expiry=expiry,
            spot_price=Price(udl),
            strikes=strikes,
            source="kite",
        )

    # ── Candles ───────────────────────────────────────────────────────────────

    async def get_intraday_candles(self, symbol: Symbol,
                                   interval: CandleInterval = CandleInterval.FIVE_MIN) -> list[Candle]:
        loop     = asyncio.get_event_loop()
        kite_int = _INTERVAL_MAP.get(interval, "5minute")
        try:
            from app.data_sources.kite import get_intraday_candles
            raw = await loop.run_in_executor(None, get_intraday_candles, str(symbol), kite_int)
            return [
                Candle(
                    symbol=symbol,
                    timestamp=c["date"],
                    open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                    volume=c["volume"],
                )
                for c in raw
            ]
        except Exception as exc:
            raise DataUnavailableError(f"Kite intraday candles failed for {symbol}: {exc}") from exc

    async def get_daily_ohlcv(self, symbol: Symbol, days: int = 400) -> list[Candle]:
        loop = asyncio.get_event_loop()
        try:
            from app.data_sources.kite import get_daily_ohlcv
            raw = await loop.run_in_executor(None, get_daily_ohlcv, str(symbol), days)
            return [
                Candle(
                    symbol=symbol,
                    timestamp=c["date"],
                    open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                    volume=c["volume"],
                )
                for c in raw
            ]
        except Exception as exc:
            raise DataUnavailableError(f"Kite daily OHLCV failed for {symbol}: {exc}") from exc

    # ── Market snapshot (delegates to NSE for VIX) ────────────────────────────

    async def get_market_snapshot(self) -> MarketSnapshot:
        raise DataUnavailableError("Market snapshot not available via Kite — use NSE")

    # ── Universe ──────────────────────────────────────────────────────────────

    async def get_fo_universe(self) -> list[Symbol]:
        from app.data_sources.kite import get_fo_universe
        return [Symbol(s) for s in get_fo_universe()]

    # ── Health ────────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        from app.data_sources.kite import KITE_AVAILABLE, kite_session
        return KITE_AVAILABLE and kite_session.ensure_connected()
