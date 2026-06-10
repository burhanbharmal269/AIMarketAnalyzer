"""Market sentiment scoring — VIX, breadth, AI regime."""
from app.core.constants import SCORE_CATEGORIES, VIX_VERY_CALM, VIX_CALM, VIX_ELEVATED, VIX_CAUTION
from app.services.scoring.base import BaseScorer


class SentimentScorer(BaseScorer):
    category  = "sentiment"
    max_score = SCORE_CATEGORIES["sentiment"]

    def score(self, candidate: dict, market: dict) -> int:
        score = candidate.get("marketSentiment", 0)
        vix   = market["indiaVix"]

        # VIX — calm markets favour trending option setups
        if vix <= VIX_VERY_CALM: score += 3
        elif vix <= VIX_CALM:    score += 1
        elif vix <= VIX_ELEVATED: pass       # neutral
        elif vix <= VIX_CAUTION:  score -= 2
        else:                     score -= 4  # above 20 (22+ = hard gate)

        # Market breadth — advance/decline ratio
        breadth = market.get("breadth", 1.0)
        if breadth >= 1.5:    score += 3
        elif breadth >= 1.2:  score += 2
        elif breadth >= 1.0:  score += 1
        elif breadth < 0.8:   score -= 1

        # Market PCR (NIFTY total PE OI ÷ CE OI) — broad institutional sentiment.
        # High PCR = institutions are buying puts (hedging longs) = contrarian bullish.
        # Low PCR = institutions are buying calls aggressively = contrarian bearish signal.
        market_pcr = market.get("marketPcr")
        if market_pcr is not None:
            direction = candidate.get("direction", "BUY")
            if direction == "BUY":
                if market_pcr >= 1.3:    score += 2   # heavy put hedging = smart money long
                elif market_pcr >= 1.1:  score += 1
                elif market_pcr < 0.7:   score -= 2   # call euphoria = caution for longs
            else:   # SELL
                if market_pcr <= 0.7:    score += 2   # call euphoria confirms bearish setup
                elif market_pcr <= 0.9:  score += 1
                elif market_pcr > 1.3:   score -= 2   # defensive hedging ≠ genuine distribution

        # AI regime — overrides rule-based bias when AI is configured
        ai_action = market.get("aiAction")
        if ai_action == "trade_full":
            score += 3
        elif ai_action == "trade_reduced":
            score -= 1
        elif ai_action == "selective":
            score -= 2

        return self._clamp(score)
