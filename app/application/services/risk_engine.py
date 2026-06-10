"""RiskEngine — single-responsibility position sizing and portfolio risk validation.

Replaces the scattered risk logic spread across:
  - services/strategies/base.py (sizing)
  - services/gates/drawdown.py (drawdown limits)
  - services/gates/risk_reward.py (R:R)
  - services/scanner.py (position validation)

Every risk check is a named method returning (passed: bool, reason: str).
All checks run even on failure — the trader sees the complete picture.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

from app.domain.risk.entities import Portfolio
from app.domain.signal.entities import Candidate

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved:    bool
    lots:        int
    quantity:    int
    failures:    list[str] = field(default_factory=list)
    lot_risk:    float = 0.0
    risk_budget: float = 0.0
    sizing_notes: str = ""


@dataclass
class RiskConfig:
    capital:              float
    risk_pct:             float = 2.0
    max_daily_loss_pct:   float = 3.0
    max_weekly_loss_pct:  float = 8.0
    max_monthly_loss_pct: float = 15.0
    max_sector_exposure:  float = 30.0
    max_open_positions:   int   = 5
    min_rr:               float = 1.5
    max_lots_per_trade:   int   = 10
    loss_streak_pause_at: int   = 3    # consecutive losses → reduce size
    loss_streak_halt_at:  int   = 5    # consecutive losses → halt new trades


class RiskEngine:
    """Portfolio-aware position sizing and risk validation."""

    def __init__(self, config: RiskConfig) -> None:
        self._cfg = config

    def evaluate(
        self,
        candidate:  Candidate,
        portfolio:  Portfolio,
    ) -> RiskDecision:
        """Full risk evaluation. Returns RiskDecision with approved flag and all failure reasons."""
        failures: list[str] = []

        risk_budget = self._cfg.capital * (self._cfg.risk_pct / 100)

        # ── 1. Position sizing ────────────────────────────────────────────────
        lots, lot_risk, sizing_note = self._compute_lots(candidate, risk_budget)

        if lots < 1:
            capital_needed = int(lot_risk / (self._cfg.risk_pct / 100)) if lot_risk > 0 else 0
            risk_pct_needed = round(lot_risk / self._cfg.capital * 100, 1) if lot_risk > 0 else 0
            failures.append(
                f"Risk per lot ₹{lot_risk:,.0f} exceeds {self._cfg.risk_pct}% budget "
                f"(₹{risk_budget:,.0f}). "
                f"Fix: raise capital to ₹{capital_needed:,} OR set risk% ≥ {risk_pct_needed}%."
            )

        # ── 2. Loss streak ────────────────────────────────────────────────────
        streak_ok, streak_msg = self._check_loss_streak(portfolio)
        if not streak_ok:
            failures.append(streak_msg)

        # ── 3. Daily loss limit ───────────────────────────────────────────────
        daily_ok, daily_msg = self._check_drawdown(
            portfolio.daily_pnl_pct, -self._cfg.max_daily_loss_pct, "Daily"
        )
        if not daily_ok:
            failures.append(daily_msg)

        # ── 4. Weekly loss limit ──────────────────────────────────────────────
        weekly_ok, weekly_msg = self._check_drawdown(
            portfolio.weekly_pnl_pct, -self._cfg.max_weekly_loss_pct, "Weekly"
        )
        if not weekly_ok:
            failures.append(weekly_msg)

        # ── 5. Monthly loss limit ─────────────────────────────────────────────
        monthly_ok, monthly_msg = self._check_drawdown(
            portfolio.monthly_pnl_pct, -self._cfg.max_monthly_loss_pct, "Monthly"
        )
        if not monthly_ok:
            failures.append(monthly_msg)

        # ── 6. R:R ratio ──────────────────────────────────────────────────────
        rr_ok, rr_msg = self._check_rr(candidate)
        if not rr_ok:
            failures.append(rr_msg)

        # ── 7. Max open positions ─────────────────────────────────────────────
        if portfolio.open_position_count >= self._cfg.max_open_positions:
            failures.append(
                f"Max {self._cfg.max_open_positions} concurrent positions reached. "
                "Close an existing trade before opening a new one."
            )

        # ── 8. Duplicate underlying ───────────────────────────────────────────
        if candidate.underlying and candidate.underlying in portfolio.open_underlyings:
            failures.append(
                f"Open position already exists in {candidate.underlying}. "
                "One direction per underlying at a time — concentration risk."
            )

        # ── 9. Sector exposure ────────────────────────────────────────────────
        sector_ok, sector_msg = self._check_sector_exposure(candidate, portfolio)
        if not sector_ok:
            failures.append(sector_msg)

        approved = not failures and lots >= 1
        quantity = lots * candidate.lot_size if lots >= 1 else 0

        return RiskDecision(
            approved=approved,
            lots=min(lots, self._cfg.max_lots_per_trade),
            quantity=quantity,
            failures=failures,
            lot_risk=lot_risk,
            risk_budget=risk_budget,
            sizing_notes=sizing_note,
        )

    # ── Private checks ────────────────────────────────────────────────────────

    def _compute_lots(
        self, candidate: Candidate, risk_budget: float
    ) -> tuple[int, float, str]:
        """Returns (lots, lot_risk_inr, note)."""
        # ATR-based risk per lot (preferred)
        atr_risk = candidate.atr * candidate.lot_size if candidate.atr > 0 else 0
        # Entry-to-SL based risk per lot
        if candidate.entry > 0 and candidate.stop_loss > 0:
            price_risk = abs(candidate.entry - candidate.stop_loss) * candidate.lot_size
        else:
            price_risk = 0.0
        # Premium-based risk (fallback — 30% of premium per lot)
        premium_risk = candidate.lot_premium * 0.30 * candidate.lot_size if candidate.lot_premium > 0 else 0

        # Choose the best available measure (ATR → price → premium)
        if atr_risk > 0:
            lot_risk = atr_risk
            note = f"ATR-based: ₹{lot_risk:,.0f}/lot"
        elif price_risk > 0:
            lot_risk = price_risk
            note = f"SL-based: ₹{lot_risk:,.0f}/lot"
        elif premium_risk > 0:
            lot_risk = premium_risk
            note = f"Premium-based (30%): ₹{lot_risk:,.0f}/lot"
        else:
            return 0, 0.0, "Cannot compute lot risk — missing ATR/SL/premium"

        # Loss-streak size reduction
        streak = getattr(self._cfg, "_active_streak", 0)
        if streak >= self._cfg.loss_streak_pause_at:
            risk_budget = risk_budget * 0.5
            note += f" (½ size: {streak} consecutive losses)"

        lots = int(risk_budget / lot_risk) if lot_risk > 0 else 0
        return max(0, lots), round(lot_risk, 2), note

    def _check_loss_streak(self, portfolio: Portfolio) -> tuple[bool, str]:
        streak = portfolio.loss_streak
        if streak >= self._cfg.loss_streak_halt_at:
            return False, (
                f"{streak} consecutive losses — trading halted until review. "
                f"Max allowed streak: {self._cfg.loss_streak_halt_at}."
            )
        return True, ""

    def _check_drawdown(
        self, current_pct: float, limit_pct: float, label: str
    ) -> tuple[bool, str]:
        if current_pct <= limit_pct:
            return False, (
                f"{label} loss {abs(current_pct):.1f}% exceeded limit "
                f"{abs(limit_pct):.1f}% — no new trades until next {label.lower()} session."
            )
        return True, ""

    def _check_rr(self, candidate: Candidate) -> tuple[bool, str]:
        if not candidate.targets:
            return True, ""
        t1 = float(candidate.targets[0])
        risk   = abs(candidate.entry - candidate.stop_loss) if candidate.entry and candidate.stop_loss else 0
        reward = abs(t1 - candidate.entry) if t1 and candidate.entry else 0
        if risk <= 0 or reward <= 0:
            return True, ""   # can't validate
        rr = round(reward / risk, 2)
        if rr < self._cfg.min_rr:
            return False, (
                f"R:R {rr:.2f} below minimum {self._cfg.min_rr} "
                f"(risk ₹{risk:.2f}/unit, reward ₹{reward:.2f}/unit). "
                "Widen target or tighten stop loss."
            )
        return True, ""

    def _check_sector_exposure(
        self, candidate: Candidate, portfolio: Portfolio
    ) -> tuple[bool, str]:
        from app.core.constants import SECTOR_MAP
        sector = SECTOR_MAP.get(candidate.underlying, "unknown")
        if sector == "index":
            return True, ""   # indices are exempt
        exposure = portfolio.sector_exposure.get(sector, 0.0)
        max_exposure = self._cfg.capital * (self._cfg.max_sector_exposure / 100)
        if exposure >= max_exposure:
            return False, (
                f"Sector '{sector}' exposure ₹{exposure:,.0f} already at "
                f"{self._cfg.max_sector_exposure}% limit (₹{max_exposure:,.0f})."
            )
        return True, ""

    @classmethod
    def from_settings(cls, settings: dict) -> "RiskEngine":
        """Build from scan settings dict — backward compatible with existing API."""
        return cls(RiskConfig(
            capital=float(settings.get("accountCapital", 100_000)),
            risk_pct=float(settings.get("riskPercent", 2.0)),
            max_daily_loss_pct=float(settings.get("maxDailyLossPct", 3.0)),
            max_weekly_loss_pct=float(settings.get("maxWeeklyDrawdownPct", 8.0)),
            max_monthly_loss_pct=float(settings.get("maxMonthlyDrawdownPct", 15.0)),
            loss_streak_halt_at=int(settings.get("lossStreak", 0)) + 5,
        ))
