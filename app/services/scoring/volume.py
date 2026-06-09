"""Volume scoring category — relative equity volume and option contract liquidity."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class VolumeScorer(BaseScorer):
    category  = "volume"
    max_score = SCORE_CATEGORIES["volume"]

    def score(self, candidate: dict, market: dict) -> int:
        score   = 0
        rel_vol = candidate["relativeVolume"]

        # Relative equity volume vs 20-day average
        if rel_vol >= 2.0:   score += 9   # explicit volume spike
        elif rel_vol >= 1.6: score += 7
        elif rel_vol >= 1.3: score += 5
        elif rel_vol >= 1.0: score += 3

        # Volume spike bonus — 2× avg confirms institutional participation
        if candidate.get("volumeSpike"):
            score += 3

        # Option contract liquidity
        opt_vol = candidate["optionVolume"]
        if opt_vol >= 100_000:   score += 6
        elif opt_vol >= 50_000:  score += 4
        elif opt_vol >= 20_000:  score += 2

        return self._clamp(score)
