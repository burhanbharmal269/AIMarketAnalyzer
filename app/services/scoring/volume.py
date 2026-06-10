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
        # Scale logarithmically above 2× — 3× and 4× are materially stronger signals
        if rel_vol >= 4.0:   score += 11  # extreme institutional surge
        elif rel_vol >= 3.0: score += 10  # very strong participation
        elif rel_vol >= 2.0: score += 9   # explicit volume spike
        elif rel_vol >= 1.6: score += 7
        elif rel_vol >= 1.3: score += 5
        elif rel_vol >= 1.0: score += 3

        # Volume spike bonus — 2× avg confirms institutional participation
        if candidate.get("volumeSpike"):
            score += 3

        # Option contract liquidity — fall back to OI when today's volume is 0
        # (Angel One resets totalTradedVolume at open; Wednesday post-expiry fresh
        # weekly contracts won't have volume until ~10:30 but OI is already populated)
        opt_vol = candidate["optionVolume"]
        if opt_vol == 0:
            oi_pct = candidate.get("oiChangePct", 0)
            if oi_pct > 0:
                score += 2   # OI present — contract is live, award minimum liquidity pts
        elif opt_vol >= 100_000: score += 6
        elif opt_vol >= 50_000:  score += 4
        elif opt_vol >= 20_000:  score += 2

        # Intraday Volume Profile POC — price position vs highest-volume level.
        # Research (auction theory / market profile): price moving decisively away
        # from POC in trade direction = momentum confirmation (buyers/sellers in
        # control). Price hugging POC = range-bound, unfavourable for directional trades.
        poc_pct = candidate.get("priceVsPoc")   # % above(+) or below(-) intraday POC
        if poc_pct is not None:
            direction = candidate.get("direction", "BUY")
            if direction == "BUY":
                if poc_pct > 1.5:     score += 4   # strongly above POC — bullish momentum
                elif poc_pct > 0.5:   score += 2   # above POC — buyers in control
                elif poc_pct < -0.5:  score -= 2   # below POC — headwind for calls
            else:   # SELL
                if poc_pct < -1.5:    score += 4   # strongly below POC — bearish momentum
                elif poc_pct < -0.5:  score += 2   # below POC — sellers in control
                elif poc_pct > 0.5:   score -= 2   # above POC — headwind for puts

        return self._clamp(score)
