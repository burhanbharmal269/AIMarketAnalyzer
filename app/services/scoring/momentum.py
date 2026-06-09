"""Momentum scoring category — RSI, MACD, ADX."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class MomentumScorer(BaseScorer):
    category  = "momentum"
    max_score = SCORE_CATEGORIES["momentum"]

    def score(self, candidate: dict, market: dict) -> int:
        score     = 0
        direction = candidate["direction"]
        rsi       = candidate["rsi"]

        # RSI — ideal zone for option buyers (avoid chasing)
        if direction == "BUY":
            if 55 <= rsi <= 70:   score += 7   # sweet spot
            elif 50 <= rsi < 55:  score += 4
            elif rsi > 70:        score += 1   # overbought — IV crush risk
            elif rsi < 45:        score -= 2   # counter-trend
        else:
            if 30 <= rsi <= 45:   score += 7
            elif 45 < rsi <= 50:  score += 4
            elif rsi < 30:        score += 1   # oversold — bounce risk
            elif rsi > 55:        score -= 2

        # MACD crossover — direction must match
        if direction == "BUY"  and candidate["macd"] > candidate["macdSignal"]:
            score += 6
        elif direction == "SELL" and candidate["macd"] < candidate["macdSignal"]:
            score += 6

        # MACD histogram — expanding = trend accelerating; shrinking = stalling
        hist     = candidate.get("macdHistogram", 0.0)
        macd_abs = abs(candidate.get("macd", 0.0))
        if candidate.get("macdHistExpanding") and hist != 0.0:
            if (direction == "BUY" and hist > 0) or (direction == "SELL" and hist < 0):
                score += 3
        elif hist != 0.0 and macd_abs > 0 and abs(hist) < 0.10 * macd_abs:
            score -= 1   # histogram shrinking toward zero — momentum fading

        # ADX — trend strength
        adx = candidate["adx"]
        if adx >= 28:   score += 7
        elif adx >= 22: score += 5
        elif adx >= 18: score += 3
        elif adx >= 14: score += 1

        # ADX direction — rising ADX = trend strengthening, not exhausting
        if candidate.get("adxRising") and adx >= 18:
            score += 2

        return self._clamp(score)
