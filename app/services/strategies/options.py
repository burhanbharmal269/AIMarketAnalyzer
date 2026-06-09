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
    ExpiryDayGate, AiRegimeGate, GateEngine,
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
            LossStreakGate(),
            DailyLossGate(),
            WeeklyDrawdownGate(),
            MonthlyDrawdownGate(),
            MinRRGate(),
            TrendAlignmentGate(),
            IvRankGate(),
            VolumeLiquidityGate(),
            SpreadGate(),
            EventRiskGate(),
            VixGate(),
            AiRegimeGate(),
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

    def compute_position_size(self, candidate: dict, settings: dict, grade: str = "A") -> dict:
        rupee_risk    = settings["accountCapital"] * (settings["riskPercent"] / 100)
        per_unit_risk = abs(candidate["entry"] - candidate["stopLoss"])
        lot_risk      = per_unit_risk * candidate["lotSize"]
        full_lots     = int(rupee_risk // lot_risk) if lot_risk else 0

        # Scale lots by grade — Grade B gets 65% to reflect lower conviction
        multiplier    = self._GRADE_MULTIPLIER.get(grade, 1.0)
        lots          = max(0, int(full_lots * multiplier))

        return {
            "rupeeRisk":    round(rupee_risk * multiplier),
            "perUnitRisk":  round(per_unit_risk, 2),
            "lotRisk":      round(lot_risk),
            "lots":         lots,
            "quantity":     lots * candidate["lotSize"],
            "grade":        grade,
            "gradeNote":    (
                "Full size — high-conviction (score ≥ 80)." if grade == "A"
                else "Reduced to 65% — good setup, borderline score (70-79)."
            ),
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
                    # Recompute lots at 65%
                    old_lots = weaker_item["sizing"]["lots"]
                    new_lots = max(1, int(old_lots * 0.65))
                    weaker_item["sizing"]["lots"]      = new_lots
                    weaker_item["sizing"]["quantity"]  = new_lots * weaker_item["candidate"]["lotSize"]
                    weaker_item["sizing"]["grade"]     = "B"
                    weaker_item["sizing"]["gradeNote"] = (
                        f"Reduced to 65% — correlated index risk: NIFTY and BANKNIFTY "
                        f"both {nifty_dir}. Sizing down the lower-scoring index."
                    )

        return deduped
