"""Market domain entities — normalised data structures used across all layers."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from app.domain.market.value_objects import (
    Symbol, Price, Volume, OI, IVPercent, OptionType, Expiry,
)


@dataclass
class Candle:
    symbol:    Symbol
    timestamp: datetime
    open:      Price
    high:      Price
    low:       Price
    close:     Price
    volume:    Volume

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class Quote:
    """Best bid/ask snapshot for a single instrument."""
    symbol:    Symbol
    ltp:       Price
    bid:       Price
    ask:       Price
    volume:    Volume
    oi:        OI
    timestamp: datetime
    change:    float = 0.0
    change_pct: float = 0.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        return round(self.spread / self.ltp * 100, 2) if self.ltp > 0 else 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if (self.bid > 0 and self.ask > 0) else self.ltp


@dataclass
class StrikeData:
    """One CE or PE leg at a given strike."""
    strike:      float
    option_type: OptionType
    ltp:         Price
    oi:          OI
    oi_change:   float
    volume:      Volume
    iv:          IVPercent
    delta:       float = 0.0
    gamma:       float = 0.0
    theta:       float = 0.0
    vega:        float = 0.0
    bid:         Price = Price(0.0)
    ask:         Price = Price(0.0)
    underlying_value: Price = Price(0.0)

    @property
    def spread_pct(self) -> float:
        return round((self.ask - self.bid) / self.ltp * 100, 2) if self.ltp > 0 else 0.0

    @property
    def oi_change_pct(self) -> float:
        prev_oi = self.oi - self.oi_change
        return round(self.oi_change / prev_oi * 100, 2) if prev_oi > 0 else 0.0


@dataclass
class OptionChainSnapshot:
    """Complete option chain for one symbol/expiry — normalised from any broker."""
    symbol:     Symbol
    expiry:     Expiry
    spot_price: Price
    strikes:    list[StrikeData] = field(default_factory=list)
    source:     str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def call_strikes(self) -> list[StrikeData]:
        return [s for s in self.strikes if s.option_type == OptionType.CALL]

    @property
    def put_strikes(self) -> list[StrikeData]:
        return [s for s in self.strikes if s.option_type == OptionType.PUT]

    @property
    def total_ce_oi(self) -> float:
        return sum(s.oi for s in self.call_strikes)

    @property
    def total_pe_oi(self) -> float:
        return sum(s.oi for s in self.put_strikes)

    @property
    def pcr(self) -> float:
        return round(self.total_pe_oi / self.total_ce_oi, 3) if self.total_ce_oi > 0 else 0.0

    @property
    def strike_prices(self) -> list[float]:
        return sorted({s.strike for s in self.strikes})

    def atm_strike(self) -> float:
        prices = self.strike_prices
        if not prices:
            return self.spot_price
        return min(prices, key=lambda sp: abs(sp - self.spot_price))

    def atm_iv(self) -> float:
        atm = self.atm_strike()
        atm_legs = [s for s in self.strikes if s.strike == atm and s.iv > 0]
        if not atm_legs:
            return 0.0
        return round(sum(s.iv for s in atm_legs) / len(atm_legs), 2)

    def to_nse_dict(self) -> dict:
        """Convert back to NSE-compatible dict for backward-compat with existing parsers."""
        rows = []
        strike_set: dict[float, dict] = {}
        for s in self.strikes:
            if s.strike not in strike_set:
                strike_set[s.strike] = {"strikePrice": s.strike, "expiryDate": self.expiry.to_nse_fmt()}
            leg = {
                "lastPrice": s.ltp,
                "openInterest": s.oi,
                "changeinOpenInterest": s.oi_change,
                "pchangeinOpenInterest": s.oi_change_pct,
                "totalTradedVolume": s.volume,
                "impliedVolatility": s.iv,
                "bidprice": s.bid,
                "askPrice": s.ask,
                "underlyingValue": s.underlying_value or self.spot_price,
            }
            strike_set[s.strike][s.option_type.value] = leg
        rows = list(strike_set.values())
        return {
            "records": {
                "expiryDates": [self.expiry.to_nse_fmt()],
                "data": rows,
            },
            "filtered": {
                "CE": {"totOI": self.total_ce_oi},
                "PE": {"totOI": self.total_pe_oi},
            },
            "_source": self.source,
        }


@dataclass
class MarketSnapshot:
    """Aggregated market state — VIX, breadth, indices, global cues."""
    india_vix:       float = 0.0
    nifty_ltp:       float = 0.0
    nifty_direction: str = ""
    banknifty_ltp:   float = 0.0
    breadth:         float = 0.0          # advances / (advances + declines)
    sp500_change:    float | None = None  # previous session change %
    usd_inr:         float = 0.0
    timestamp:       datetime = field(default_factory=datetime.utcnow)
    extra:           dict = field(default_factory=dict)
