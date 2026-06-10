"""Risk domain entities."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Portfolio:
    """Current portfolio state — passed to RiskEngine for evaluation."""
    daily_pnl_inr:      float       = 0.0
    daily_pnl_pct:      float       = 0.0   # negative = loss
    weekly_pnl_pct:     float       = 0.0
    monthly_pnl_pct:    float       = 0.0
    open_position_count: int        = 0
    open_underlyings:   set[str]    = field(default_factory=set)
    sector_exposure:    dict[str, float] = field(default_factory=dict)  # sector → ₹ notional
    loss_streak:        int         = 0
    total_capital:      float       = 0.0


@dataclass
class RiskState:
    """Computed risk metrics from journal — matches existing storage.compute_risk_state output."""
    daily_pnl:       float = 0.0
    daily_pnl_pct:   float = 0.0
    weekly_pnl_pct:  float = 0.0
    monthly_pnl_pct: float = 0.0
    loss_streak:     int   = 0
    open_trades:     list[dict] = field(default_factory=list)

    @classmethod
    def from_storage_dict(cls, d: dict) -> "RiskState":
        return cls(
            daily_pnl=float(d.get("dailyPnl", 0) or 0),
            daily_pnl_pct=float(d.get("dailyPnlPct", 0) or 0),
            weekly_pnl_pct=float(d.get("weeklyPnlPct", 0) or 0),
            monthly_pnl_pct=float(d.get("monthlyPnlPct", 0) or 0),
            loss_streak=int(d.get("lossStreak", 0) or 0),
            open_trades=d.get("openTrades", []),
        )

    def to_portfolio(self, capital: float) -> Portfolio:
        open_underlyings = {t.get("underlying", t.get("instrument", "").split()[0])
                            for t in self.open_trades}
        return Portfolio(
            daily_pnl_inr=self.daily_pnl,
            daily_pnl_pct=self.daily_pnl_pct,
            weekly_pnl_pct=self.weekly_pnl_pct,
            monthly_pnl_pct=self.monthly_pnl_pct,
            open_position_count=len(self.open_trades),
            open_underlyings=open_underlyings,
            loss_streak=self.loss_streak,
            total_capital=capital,
        )
