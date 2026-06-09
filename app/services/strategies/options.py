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

    def compute_position_size(self, candidate: dict, settings: dict) -> dict:
        rupee_risk    = settings["accountCapital"] * (settings["riskPercent"] / 100)
        per_unit_risk = abs(candidate["entry"] - candidate["stopLoss"])
        lot_risk      = per_unit_risk * candidate["lotSize"]
        lots          = int(rupee_risk // lot_risk) if lot_risk else 0
        return {
            "rupeeRisk":    round(rupee_risk),
            "perUnitRisk":  round(per_unit_risk, 2),
            "lotRisk":      round(lot_risk),
            "lots":         max(0, lots),
            "quantity":     max(0, lots) * candidate["lotSize"],
        }

    # ── Post-filter: sector concentration ────────────────────────────────────

    def _post_filter(self, approved_list: list[dict]) -> list[dict]:
        """Keep only the highest-scoring signal per sector.

        Indices (NIFTY, BANKNIFTY) are exempt — they are independent products
        that don't add sector correlation. List is already score-sorted so the
        first occurrence per sector is always the strongest.
        """
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

        return deduped
