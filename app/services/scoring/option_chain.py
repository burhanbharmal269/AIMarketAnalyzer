"""Option chain scoring — OI change, PCR, max pain, spread, IV."""
from app.core.constants import SCORE_CATEGORIES
from app.services.scoring.base import BaseScorer


class OptionChainScorer(BaseScorer):
    category  = "optionChain"
    max_score = SCORE_CATEGORIES["optionChain"]

    def score(self, candidate: dict, market: dict) -> int:
        score     = 0
        direction = candidate["direction"]

        # OI change — fresh open interest in trading direction
        oi_chg = candidate["oiChangePct"]
        if oi_chg >= 15:    score += 8
        elif oi_chg >= 8:   score += 6
        elif oi_chg >= 4:   score += 4
        elif oi_chg >= 1:   score += 2
        elif oi_chg >= 0:   score += 1
        else:               score -= 3   # unwinding — participants exiting

        # PCR — directionally aware institutional sentiment
        pcr = candidate["pcr"]
        if direction == "BUY":
            if pcr >= 1.3:    score += 6
            elif pcr >= 1.0:  score += 4
            elif pcr >= 0.8:  score += 2
            elif pcr < 0.7:   score -= 3
        else:
            if pcr <= 0.7:    score += 6
            elif pcr <= 0.9:  score += 4
            elif pcr <= 1.1:  score += 2
            elif pcr > 1.3:   score -= 3

        # Max pain distance — farther from max pain = less gravity
        mp_dist = candidate["maxPainDistancePct"]
        if mp_dist >= 2.0:   score += 3
        elif mp_dist >= 1.0: score += 2
        else:                score += 1

        # Spread — tight spread = liquid, better execution
        spread = candidate["spreadPct"]
        if spread <= 1.0:    score += 3
        elif spread <= 2.0:  score += 2
        elif spread <= 3.0:  score += 1

        # ATM IV level — raw premium cost
        atm_iv = candidate.get("atmIV", 0)
        if atm_iv > 0:
            if atm_iv < 16:    score += 3
            elif atm_iv < 22:  score += 2
            elif atm_iv < 30:  score += 1
            elif atm_iv < 40:  score -= 1
            elif atm_iv < 50:  score -= 3
            else:              score -= 5

        # IV Rank — 52-week percentile (more reliable than raw IV level)
        iv_rank = candidate.get("ivRank")
        if iv_rank is not None:
            if iv_rank < 15:    score += 3   # option buyers' ideal
            elif iv_rank < 30:  score += 2
            elif iv_rank < 50:  score += 1
            elif iv_rank > 80:  score -= 4   # avoid buying at multi-month highs
            elif iv_rank > 65:  score -= 2

        # Order-Flow Imbalance (OFI) confluence: PCR and OI change both confirm direction.
        # Research (Cont et al. 2014): when institutional sentiment (PCR) AND fresh open
        # interest (OI change) both agree, the edge is materially stronger than either alone.
        # This is the "smart money" alignment signal.
        oi_positive = oi_chg >= 4   # fresh OI build-up — participants opening, not closing
        if direction == "BUY":
            if pcr >= 1.0 and oi_positive:
                score += 5   # put writers + fresh call OI = dual institutional confirmation
        else:   # SELL
            if pcr <= 0.9 and oi_positive:
                score += 5   # call writers + fresh put OI = dual institutional confirmation

        return self._clamp(score)
