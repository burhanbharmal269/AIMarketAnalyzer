"""Signal domain entities."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from app.domain.market.value_objects import Symbol, Price, OptionType
from app.domain.signal.value_objects import Direction, SetupType, SignalGrade


@dataclass
class ScoreBreakdown:
    trend:       float = 0.0
    momentum:    float = 0.0
    volume:      float = 0.0
    option:      float = 0.0
    sentiment:   float = 0.0
    risk_reward: float = 0.0
    news:        float = 0.0

    @property
    def total(self) -> float:
        return round(sum([
            self.trend, self.momentum, self.volume,
            self.option, self.sentiment, self.risk_reward, self.news
        ]), 2)

    @property
    def grade(self) -> SignalGrade:
        return SignalGrade.from_score(self.total)

    def to_dict(self) -> dict:
        return {
            "trend": self.trend, "momentum": self.momentum,
            "volume": self.volume, "optionChain": self.option,
            "sentiment": self.sentiment, "riskReward": self.risk_reward,
            "news": self.news, "total": self.total,
        }


@dataclass
class Candidate:
    """Enriched candidate instrument ready for gate + scoring evaluation."""
    id:            UUID      = field(default_factory=uuid4)
    symbol:        Symbol    = Symbol("")
    underlying:    Symbol    = Symbol("")
    instrument:    str       = ""
    direction:     Direction = Direction.BUY
    option_type:   OptionType = OptionType.CALL
    strike:        float     = 0.0
    spot_price:    Price     = Price(0.0)
    entry:         Price     = Price(0.0)
    stop_loss:     Price     = Price(0.0)
    targets:       list[Price] = field(default_factory=list)
    lot_size:      int       = 1
    lot_premium:   Price     = Price(0.0)

    # Technical indicators
    ema20:         float = 0.0
    ema50:         float = 0.0
    ema200:        float = 0.0
    rsi:           float = 0.0
    adx:           float = 0.0
    atr:           float = 0.0
    vwap:          float = 0.0
    macd:          float = 0.0
    macd_signal:   float = 0.0
    macd_hist:     float = 0.0
    supertrend_bull: bool = False

    # Option-specific
    atm_iv:        float      = 0.0
    iv_rank:       float | None = None
    option_volume: float      = 0.0
    oi_change_pct: float      = 0.0
    spread_pct:    float      = 0.0
    pcr:           float      = 0.0
    max_pain:      float      = 0.0
    delta:         float      = 0.0
    theta:         float      = 0.0
    vega:          float      = 0.0

    # Pattern flags
    vwap_confirmed:  bool = False
    tf15_aligned:    bool = False
    orb_breakout:    bool = False
    sr_breakout:     bool = False
    pd_breakout:     bool = False
    gap_up:          bool = False
    gap_down:        bool = False

    # Market context
    india_vix:       float = 0.0
    rel_volume:      float = 0.0
    resistance:      float = 0.0
    support:         float = 0.0
    poc:             float = 0.0
    news_sentiment:  float = 0.0
    setup_type:      str  = "Trend"
    expiry_type:     str  = "Weekly"
    dte:             int  = 0

    # Backward-compat raw dict (existing scorers/gates consume this)
    _raw: dict = field(default_factory=dict, repr=False)

    def to_raw(self) -> dict:
        """Return a merged raw dict — new fields shadow old ones."""
        base = dict(self._raw)
        base.update({
            "symbol": self.symbol, "underlying": self.underlying,
            "instrument": self.instrument, "direction": self.direction.value,
            "spotPrice": self.spot_price, "entry": self.entry,
            "stopLoss": self.stop_loss, "targets": list(self.targets),
            "lotSize": self.lot_size, "lotPremium": self.lot_premium,
            "ema20": self.ema20, "ema50": self.ema50, "ema200": self.ema200,
            "rsi": self.rsi, "adx": self.adx, "atr": self.atr,
            "vwap": self.vwap, "macd": self.macd, "macdSignal": self.macd_signal,
            "macdHist": self.macd_hist, "supertrendBull": self.supertrend_bull,
            "atmIv": self.atm_iv, "ivRank": self.iv_rank,
            "optionVolume": self.option_volume, "oiChangePct": self.oi_change_pct,
            "spreadPct": self.spread_pct, "pcr": self.pcr, "maxPain": self.max_pain,
            "delta": self.delta, "theta": self.theta, "vega": self.vega,
            "vwapConfirmed": self.vwap_confirmed, "tf15Aligned": self.tf15_aligned,
            "orbBreakout": self.orb_breakout, "srBreakout": self.sr_breakout,
            "pdBreakout": self.pd_breakout, "gapUp": self.gap_up, "gapDown": self.gap_down,
            "indiaVix": self.india_vix, "relVolume": self.rel_volume,
            "resistance": self.resistance, "support": self.support,
            "poc": self.poc, "newsSentiment": self.news_sentiment,
            "setupType": self.setup_type, "expiryType": self.expiry_type, "dte": self.dte,
        })
        return base

    @classmethod
    def from_raw(cls, raw: dict) -> "Candidate":
        """Build Candidate from legacy raw dict (used during incremental migration)."""
        c = cls()
        c._raw = raw
        c.symbol       = Symbol(raw.get("symbol", raw.get("underlying", "")))
        c.underlying   = Symbol(raw.get("underlying", ""))
        c.instrument   = raw.get("instrument", "")
        c.direction    = Direction(raw.get("direction", "BUY"))
        c.spot_price   = Price(float(raw.get("spotPrice", 0) or 0))
        c.entry        = Price(float(raw.get("entry", 0) or 0))
        c.stop_loss    = Price(float(raw.get("stopLoss", 0) or 0))
        c.targets      = [Price(float(t)) for t in raw.get("targets", [])]
        c.lot_size     = int(raw.get("lotSize", 1))
        c.lot_premium  = Price(float(raw.get("lotPremium", raw.get("entry", 0)) or 0))
        c.ema20        = float(raw.get("ema20", 0) or 0)
        c.ema50        = float(raw.get("ema50", 0) or 0)
        c.ema200       = float(raw.get("ema200", 0) or 0)
        c.rsi          = float(raw.get("rsi", 50) or 50)
        c.adx          = float(raw.get("adx", 0) or 0)
        c.atr          = float(raw.get("atr", 0) or 0)
        c.vwap         = float(raw.get("vwap", 0) or 0)
        c.macd         = float(raw.get("macd", 0) or 0)
        c.macd_signal  = float(raw.get("macdSignal", 0) or 0)
        c.macd_hist    = float(raw.get("macdHist", 0) or 0)
        c.supertrend_bull = bool(raw.get("supertrendBull", False))
        c.atm_iv       = float(raw.get("atmIv", 0) or 0)
        c.iv_rank      = raw.get("ivRank")
        c.option_volume = float(raw.get("optionVolume", 0) or 0)
        c.oi_change_pct = float(raw.get("oiChangePct", 0) or 0)
        c.spread_pct   = float(raw.get("spreadPct", 0) or 0)
        c.pcr          = float(raw.get("pcr", 0) or 0)
        c.max_pain     = float(raw.get("maxPain", 0) or 0)
        c.delta        = float(raw.get("delta", 0) or 0)
        c.theta        = float(raw.get("theta", 0) or 0)
        c.vega         = float(raw.get("vega", 0) or 0)
        c.vwap_confirmed = bool(raw.get("vwapConfirmed", False))
        c.tf15_aligned   = bool(raw.get("tf15Aligned", False))
        c.orb_breakout   = bool(raw.get("orbBreakout", False))
        c.sr_breakout    = bool(raw.get("srBreakout", False))
        c.pd_breakout    = bool(raw.get("pdBreakout", False))
        c.gap_up         = bool(raw.get("gapUp", False))
        c.gap_down       = bool(raw.get("gapDown", False))
        c.india_vix      = float(raw.get("indiaVix", 0) or 0)
        c.rel_volume     = float(raw.get("relVolume", 0) or 0)
        c.resistance     = float(raw.get("resistance", 0) or 0)
        c.support        = float(raw.get("support", 0) or 0)
        c.poc            = float(raw.get("poc", 0) or 0)
        c.news_sentiment = float(raw.get("newsSentiment", 0) or 0)
        c.setup_type     = raw.get("setupType", "Trend")
        c.expiry_type    = raw.get("expiryType", "Weekly")
        c.dte            = int(raw.get("dte", 0) or 0)
        return c


@dataclass
class Signal:
    """Fully evaluated, gate-checked, scored trading signal."""
    id:             UUID           = field(default_factory=uuid4)
    candidate:      Candidate      = field(default_factory=Candidate)
    score:          ScoreBreakdown = field(default_factory=ScoreBreakdown)
    gate_failures:  list[str]      = field(default_factory=list)
    approved:       bool           = False
    lots:           int            = 0
    quantity:       int            = 0
    lot_risk:       float          = 0.0
    explanation:    str            = ""
    ai_score:       float          = 0.0
    ai_grade:       str            = ""
    setup_type:     str            = "Trend"
    created_at:     datetime       = field(default_factory=datetime.utcnow)
