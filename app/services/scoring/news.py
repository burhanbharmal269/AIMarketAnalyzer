"""News sentiment scoring — AI-classified headline alignment."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class NewsScorer(BaseScorer):
    category  = "news"
    max_score = SCORE_CATEGORIES["news"]

    def score(self, candidate: dict, market: dict) -> int:
        raw = candidate.get("newsSentiment")
        if raw is None:
            return 0   # AI not configured or news unavailable — no score impact

        direction = candidate["direction"]
        effective = raw if direction == "BUY" else -raw

        if effective >= 2:  return 5     # strong tailwind
        if effective >= 1:  return 3     # mild positive alignment
        if effective == 0:  return 1     # neutral — slight positive (no headwinds)
        if effective == -1: return -1    # mild headwind
        return -3                         # strong contra-directional news
