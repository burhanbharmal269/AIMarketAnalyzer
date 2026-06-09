"""Risk/reward scoring — S/R-anchored RR ratio."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class RiskRewardScorer(BaseScorer):
    category  = "riskReward"
    max_score = SCORE_CATEGORIES["riskReward"]

    def score(self, candidate: dict, market: dict) -> int:
        rr = candidate["rr"]
        if rr >= 2.5: return 10
        if rr >= 2.0: return 8
        if rr >= 1.5: return 5
        if rr >= 1.0: return 2
        return 0   # below 1:1 — hard gate will reject anyway
