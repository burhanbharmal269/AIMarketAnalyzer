"""Options trading strategy — NSE F&O intraday signals.

Wires together the options-specific scoring categories, hard gates,
position sizing, and sector concentration post-filter.
"""
from __future__ import annotations

from app.core.constants import SECTOR_MAP, EXEMPT_SECTOR
from app.services.scoring import (
    TrendScorer, MomentumScorer, VolumeScorer, OptionChainScorer,
    SentimentScorer, RiskRewardScorer, NewsScorer, ScoringEngine,
)
from app.services.gates import (
    LossStreakGate, DailyLossGate, WeeklyDrawdownGate, MonthlyDrawdownGate,
    SpreadGate, VolumeLiquidityGate, EventRiskGate, VixGate, IvRankGate,
    MinRRGate, TrendAlignmentGate, OpeningVolatilityGate, ClosingVolatilityGate,
    ExpiryDayGate, AiRegimeGate, MacdDirectionGate, RsiExtremeGate, GateEngine,
)
from app.services.strategies.base import BaseSignalStrategy


class OptionsTradingStrategy(BaseSignalStrategy):
    """Current production strategy: NSE F&O intraday options signals."""
    signal_type = "options"

    def __init__(self) -> None:
        self._scoring_engine = ScoringEngine([
            TrendScorer(),
            MomentumScorer(),
            VolumeScorer(),
            OptionChainScorer(),
            SentimentScorer(),
            RiskRewardScorer(),
            NewsScorer(),
        ])
        self._gate_engine = GateEngine([
            # Account-level risk gates (run first — no point scoring if account halted)
            LossStreakGate(),
            DailyLossGate(),
            WeeklyDrawdownGate(),
            MonthlyDrawdownGate(),
            # Signal quality gates
            RsiExtremeGate(),       # only true extremes: RSI >78 BUY / <22 SELL
            MinRRGate(),
            TrendAlignmentGate(),
            IvRankGate(),
            VolumeLiquidityGate(),
            SpreadGate(),
            EventRiskGate(),
            VixGate(),
            AiRegimeGate(),
            # Time-based gates
            OpeningVolatilityGate(),
            ClosingVolatilityGate(),
            ExpiryDayGate(),
        ])

    # ── ISignalStrategy ───────────────────────────────────────────────────────

    def score_candidate(self, candidate: dict, market: dict) -> dict:
        return self._scoring_engine.score(candidate, market)

    def check_gates(
        self, candidate: dict, market: dict, risk_state: dict, settings: dict
    ) -> list[str]:
        return self._gate_engine.check(candidate, market, risk_state, settings)

    # Grade multipliers: Grade A = full size, Grade B = 65% (Kelly-inspired scaling)
    _GRADE_MULTIPLIER = {"A": 1.0, "B": 0.65}

    # Premium-as-risk thresholds (professional F&O practice, Zerodha Varsity aligned).
    # For long options, the premium paid IS the maximum loss — so premium % of capital
    # is the true risk measure, independent of where you set a stop-loss.
    # > 8%: skip regardless — one losing trade destroys >8% of account
    # > 5%: caution zone — only Grade A signals allowed through
    # ≤ 5%: normal — both SL-based and premium-based risk are acceptable
    _PREMIUM_SKIP_PCT    = 8.0   # hard skip above this
    _PREMIUM_CAUTION_PCT = 5.0   # grade B skipped above this

    def compute_position_size(self, candidate: dict, settings: dict, grade: str = "A") -> dict:
        entry    = candidate["entry"]
        lot_size = candidate["lotSize"]
        capital  = settings["accountCapital"]

        # ── SL-based sizing (existing) ────────────────────────────────────────
        rupee_risk    = capital * (settings["riskPercent"] / 100)
        per_unit_risk = abs(entry - candidate["stopLoss"])
        lot_risk      = per_unit_risk * lot_size
        full_lots     = int(rupee_risk // lot_risk) if lot_risk else 0
        multiplier    = self._GRADE_MULTIPLIER.get(grade, 1.0)
        sl_lots       = max(0, int(full_lots * multiplier))

        # ── Premium-based sizing (industry standard for long options) ─────────
        # The premium paid per lot IS the max loss — stops are secondary for options.
        # Professional rule: keep premium deployed per trade ≤ 5% of capital.
        premium_1lot = round(entry * lot_size)
        premium_pct  = round(premium_1lot / capital * 100, 1) if capital > 0 else 999

        if premium_pct > self._PREMIUM_SKIP_PCT:
            # > 8%: undercapitalized — one bad trade = 8%+ loss, skip always
            capital_flag = "undercapitalized"
            premium_lots = 0
        elif premium_pct > self._PREMIUM_CAUTION_PCT and grade == "B":
            # 5–8% + Grade B: not worth the premium cost for a borderline signal
            capital_flag = "premium_too_high_for_grade"
            premium_lots = 0
        elif premium_pct > self._PREMIUM_CAUTION_PCT:
            # 5–8% + Grade A: allow 1 lot maximum — high-conviction only
            capital_flag = "caution"
            premium_lots = 1
        else:
            # ≤ 5%: healthy — scale by SL-based lots normally
            capital_flag = "ok"
            premium_lots = int(capital * (self._PREMIUM_CAUTION_PCT / 100) / premium_1lot) if premium_1lot > 0 else sl_lots

        # Final lots: more conservative of the two sizing methods
        lots = min(sl_lots, premium_lots) if sl_lots > 0 and premium_lots > 0 else 0

        # STT cost: 0.15% on the exit (sell) side only — NSE post-April 2026 rate.
        # Reduces effective profit; show it so the trader doesn't over-count gains.
        from app.core.constants import STT_RATE_SELL
        stt_per_lot  = round(entry * lot_size * STT_RATE_SELL, 2)
        stt_total    = round(stt_per_lot * lots, 2)
        # Brokerage: flat ₹20 per executed leg × 2 legs (entry + exit) = ₹40 round-trip
        brokerage_total = 40 * lots if lots > 0 else 0

        return {
            "rupeeRisk":       round(rupee_risk * multiplier),
            "perUnitRisk":     round(per_unit_risk, 2),
            "lotRisk":         round(lot_risk),
            "lots":            lots,
            "quantity":        lots * lot_size,
            "grade":           grade,
            "gradeNote":       (
                "Full size — high-conviction (score ≥ 80)." if grade == "A"
                else "Reduced to 65% — good setup, borderline score (70-79)."
            ),
            "premium1Lot":     premium_1lot,
            "premiumPct":      premium_pct,
            "capitalFlag":     capital_flag,
            "sttPerLot":       stt_per_lot,
            "sttTotal":        stt_total,
            "brokerageTotal":  brokerage_total,
            "totalTxnCost":    round(stt_total + brokerage_total, 2),
        }

    # ── Post-filter: sector concentration ────────────────────────────────────

    def _post_filter(self, approved_list: list[dict]) -> list[dict]:
        """Keep only the highest-scoring signal per sector, then apply index correlation check.

        Pass 1 — Sector concentration: max 1 signal per sector (indices exempt).
        Pass 2 — Index correlation: if NIFTY and BANKNIFTY both approved in the same
                  direction, downgrade the lower-scoring one to Grade B (65% size).
                  They are highly correlated (r ≈ 0.85+) — doubling index exposure in
                  the same direction is a hidden concentration risk.
        """
        # ── Pass 1: sector cap ────────────────────────────────────────────────
        sector_seen: set[str] = set()
        deduped:     list     = []

        for item in approved_list:
            underlying = item["candidate"].get(
                "underlying", item["candidate"]["instrument"].split()[0]
            )
            sector = SECTOR_MAP.get(underlying, underlying)

            if sector == EXEMPT_SECTOR or sector not in sector_seen:
                sector_seen.add(sector)
                deduped.append(item)
            else:
                item["approved"] = False
                item.setdefault("rejectionReasons", []).append(
                    f"Sector cap: a higher-scoring {sector.upper()} signal is already approved."
                )

        # ── Pass 2: NIFTY + BANKNIFTY same-direction correlation risk ─────────
        # If both fire in the same direction, the lower-scoring one gets Grade B
        # (65% size). They can both be traded — just not both at full size.
        index_items = {
            item["candidate"]["underlying"]: item
            for item in deduped
            if item["candidate"].get("underlying") in ("NIFTY", "BANKNIFTY")
        }
        if len(index_items) == 2:
            nifty_dir  = index_items["NIFTY"]["candidate"]["direction"]
            bnk_dir    = index_items["BANKNIFTY"]["candidate"]["direction"]
            if nifty_dir == bnk_dir:
                # Downgrade the lower-scoring index to Grade B if it was Grade A
                nifty_score = index_items["NIFTY"]["score"]["total"]
                bnk_score   = index_items["BANKNIFTY"]["score"]["total"]
                weaker_key  = "NIFTY" if nifty_score <= bnk_score else "BANKNIFTY"
                weaker_item = index_items[weaker_key]
                if weaker_item.get("grade") == "A":
                    weaker_item["grade"] = "B"
                    # Recompute sizing at 65%
                    old_lots     = weaker_item["sizing"]["lots"]
                    new_lots     = max(1, int(old_lots * 0.65))
                    lot_risk     = weaker_item["sizing"].get("lotRisk", 0)
                    weaker_item["sizing"]["lots"]      = new_lots
                    weaker_item["sizing"]["quantity"]  = new_lots * weaker_item["candidate"]["lotSize"]
                    weaker_item["sizing"]["rupeeRisk"] = round(new_lots * lot_risk)
                    weaker_item["sizing"]["grade"]     = "B"
                    weaker_item["sizing"]["gradeNote"] = (
                        f"Reduced to 65% — correlated index risk: NIFTY and BANKNIFTY "
                        f"both {nifty_dir}. Sizing down the lower-scoring index."
                    )

        return deduped
