"""Trend scoring category — EMA alignment, Supertrend, VWAP, ORB, S/R."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class TrendScorer(BaseScorer):
    category  = "trend"
    max_score = SCORE_CATEGORIES["trend"]

    def score(self, candidate: dict, market: dict) -> int:
        direction = candidate["direction"]
        aligned = (
            candidate["ema20"] > candidate["ema50"] > candidate["ema200"]
            if direction == "BUY"
            else candidate["ema20"] < candidate["ema50"] < candidate["ema200"]
        )
        score = 11 if aligned else 3

        # Daily Supertrend — macro trend direction
        st_bullish = candidate.get("supertrendBullish", True)
        st_matches = (
            (direction == "BUY"  and     st_bullish) or
            (direction == "SELL" and not st_bullish)
        )
        if st_matches:
            score += 5

        # Previous day high/low breakout — institutional momentum
        if candidate.get("pdBreakout"):
            score += 4

        # VWAP confirmation — intraday institutional bias (Angel One 5-min)
        if candidate.get("vwapConfirmed"):
            score += 6
            if candidate.get("volumeSpike"):
                score += 2   # explicit volume spike confirms the VWAP move

        # 15-min EMA9/21 — primary intraday structure
        if candidate.get("tf15Aligned"):
            score += 3

        # 30-min EMA5/10 — macro intraday gate
        if candidate.get("tf30Aligned"):
            score += 2
            if candidate.get("tf15Aligned"):
                score += 1   # 3-TF confluence bonus (15m + 30m + daily)

        # S/R breakout — multi-touch breakouts score higher (more institutional memory)
        if candidate.get("srBreakout"):
            touches = (
                candidate.get("resistanceTouches", 0) if direction == "BUY"
                else candidate.get("supportTouches", 0)
            )
            score += 4 if touches >= 2 else 2
        if direction == "BUY"  and candidate.get("nearResistance"):
            score -= 2   # approaching overhead supply — exit risk
        if direction == "SELL" and candidate.get("nearSupport"):
            score -= 2   # approaching demand zone — bounce risk

        # Opening gap in trade direction
        if direction == "BUY"  and candidate.get("gapUp"):
            score += 2
        if direction == "SELL" and candidate.get("gapDown"):
            score += 2

        # Price action
        price_action = candidate["priceAction"].lower()
        if "breakout" in price_action:
            score += 2
        elif "retest held" in price_action:
            score += 1

        # 15-min Supertrend — intraday structure confirmation (Angel One candles only)
        st15 = candidate.get("st15Bullish")
        if st15 is not None:
            st15_matches = (direction == "BUY" and st15) or (direction == "SELL" and not st15)
            score += 3 if st15_matches else -2

        # Opening Range Breakout — cleared the first 15-min auction range
        if candidate.get("orbBreakout"):
            score += 3
        elif candidate.get("orbAgainst"):
            score -= 2   # trading against the OR direction — counter-trend risk

        # Daily Pivot Points (PP/R1/S1) — floor pivot analysis.
        # Research: R1 and S1 are self-reinforcing levels (widely watched by market
        # makers). Price above PP = intraday bullish bias; near R1 = resistance
        # for calls but confirmation if broken; near S1 = support floor for puts.
        spot    = candidate.get("spotPrice", 0)
        pivot_pp = candidate.get("pivotPP")
        pivot_r1 = candidate.get("pivotR1")
        pivot_s1 = candidate.get("pivotS1")
        if spot > 0 and pivot_pp:
            tolerance = spot * 0.003   # 0.3% proximity band
            if direction == "BUY":
                if spot > pivot_r1 if pivot_r1 else False:
                    score += 3   # broken above R1 — strong bullish breakout
                elif spot > pivot_pp:
                    score += 2   # above PP — bias confirmed
                if pivot_r1 and abs(spot - pivot_r1) <= tolerance:
                    score -= 2   # approaching R1 — imminent resistance headwind
            else:   # SELL
                if pivot_s1 and spot < pivot_s1:
                    score += 3   # broken below S1 — strong bearish breakdown
                elif spot < pivot_pp:
                    score += 2   # below PP — bearish bias confirmed
                if pivot_s1 and abs(spot - pivot_s1) <= tolerance:
                    score -= 2   # approaching S1 — imminent support floor risk

        return self._clamp(score)
