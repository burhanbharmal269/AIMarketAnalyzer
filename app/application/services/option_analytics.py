"""OptionAnalyticsService — pure analytics on top of OptionChainSnapshot.

All methods are synchronous and pure (no I/O). The caller fetches data;
this service computes derived metrics used by scorers and AI agents.

Works directly with the domain OptionChainSnapshot entity (list of StrikeData legs).
"""
from __future__ import annotations
import math
import statistics
from dataclasses import dataclass

from app.domain.market.entities import OptionChainSnapshot, StrikeData
from app.domain.market.value_objects import OptionType


@dataclass(frozen=True)
class IVSurface:
    atm_iv:       float
    call_iv_skew: float    # OTM call IV – ATM IV  (positive = right skew)
    put_iv_skew:  float    # OTM put  IV – ATM IV  (positive = vol smile)
    iv_smile:     float    # symmetric: (call_skew + put_skew) / 2


@dataclass(frozen=True)
class OIBuildUp:
    ce_oi_top3_strikes:    list[float]
    pe_oi_top3_strikes:    list[float]
    pcr_oi:                float
    pcr_volume:            float
    net_delta_bias:        str      # "bullish" | "bearish" | "neutral"
    max_pain:              float
    resistance_wall:       float    # strike with highest CE OI — call writers defending
    support_wall:          float    # strike with highest PE OI — put writers defending


@dataclass(frozen=True)
class GammaProfile:
    max_gamma_strike:  float
    gamma_flip:        float    # estimated spot where dealer gamma flips sign
    above_flip:        bool     # True → short gamma (vol amplification)


class OptionAnalyticsService:
    """Stateless analytics — instantiate once and call repeatedly."""

    # ── Public API ────────────────────────────────────────────────────────────

    def compute_iv_surface(
        self,
        chain:     OptionChainSnapshot,
        otm_width: int = 3,    # number of strikes away from ATM for skew
    ) -> IVSurface:
        atm  = chain.atm_strike()
        atm_iv = chain.atm_iv()

        call_legs = sorted(
            [s for s in chain.strikes if s.option_type == OptionType.CALL and s.strike > atm and s.iv > 0],
            key=lambda s: s.strike
        )
        put_legs = sorted(
            [s for s in chain.strikes if s.option_type == OptionType.PUT  and s.strike < atm and s.iv > 0],
            key=lambda s: s.strike, reverse=True
        )

        otm_call_iv = (
            statistics.mean(s.iv for s in call_legs[:otm_width])
            if call_legs else atm_iv
        )
        otm_put_iv = (
            statistics.mean(s.iv for s in put_legs[:otm_width])
            if put_legs else atm_iv
        )

        call_skew = round(otm_call_iv - atm_iv, 2)
        put_skew  = round(otm_put_iv  - atm_iv, 2)

        return IVSurface(
            atm_iv=round(atm_iv, 2),
            call_iv_skew=call_skew,
            put_iv_skew=put_skew,
            iv_smile=round((call_skew + put_skew) / 2, 2),
        )

    def compute_oi_build_up(self, chain: OptionChainSnapshot) -> OIBuildUp:
        atm = chain.atm_strike()
        spot = chain.spot_price

        # Group CE/PE legs by strike
        ce_by_strike: dict[float, StrikeData] = {
            s.strike: s for s in chain.strikes if s.option_type == OptionType.CALL
        }
        pe_by_strike: dict[float, StrikeData] = {
            s.strike: s for s in chain.strikes if s.option_type == OptionType.PUT
        }

        # Sort by OI for wall detection
        ce_by_oi = sorted(ce_by_strike.items(), key=lambda x: x[1].oi, reverse=True)
        pe_by_oi = sorted(pe_by_strike.items(), key=lambda x: x[1].oi, reverse=True)

        ce_top3 = [k for k, _ in ce_by_oi[:3]]
        pe_top3 = [k for k, _ in pe_by_oi[:3]]

        resistance_wall = ce_top3[0] if ce_top3 else atm
        support_wall    = pe_top3[0] if pe_top3 else atm

        total_ce_oi  = chain.total_ce_oi
        total_pe_oi  = chain.total_pe_oi
        pcr_oi       = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else 1.0

        total_ce_vol = sum(s.volume for s in ce_by_strike.values())
        total_pe_vol = sum(s.volume for s in pe_by_strike.values())
        pcr_vol      = round(total_pe_vol / total_ce_vol, 3) if total_ce_vol > 0 else 1.0

        if pcr_oi > 1.15:
            bias = "bullish"
        elif pcr_oi < 0.85:
            bias = "bearish"
        else:
            bias = "neutral"

        max_pain = self._compute_max_pain(chain)

        return OIBuildUp(
            ce_oi_top3_strikes=ce_top3,
            pe_oi_top3_strikes=pe_top3,
            pcr_oi=pcr_oi,
            pcr_volume=pcr_vol,
            net_delta_bias=bias,
            max_pain=max_pain,
            resistance_wall=resistance_wall,
            support_wall=support_wall,
        )

    def compute_gamma_profile(self, chain: OptionChainSnapshot, spot: float) -> GammaProfile:
        if not chain.strikes:
            return GammaProfile(max_gamma_strike=spot, gamma_flip=spot, above_flip=False)

        # Proxy gamma from OI weight and moneyness decay
        weighted = []
        for s in chain.strikes:
            moneyness = abs(s.strike - spot) / spot
            decay     = math.exp(-0.5 * (moneyness / 0.01) ** 2)
            weighted.append((s.strike, s.oi * decay))

        max_gamma_strike = max(weighted, key=lambda x: x[1])[0] if weighted else spot

        # Estimate gamma flip: midpoint between top CE and PE OI strikes
        ce_legs = [s for s in chain.strikes if s.option_type == OptionType.CALL and s.oi > 0]
        pe_legs = [s for s in chain.strikes if s.option_type == OptionType.PUT  and s.oi > 0]

        ce_top = max(ce_legs, key=lambda s: s.oi).strike if ce_legs else spot
        pe_top = max(pe_legs, key=lambda s: s.oi).strike if pe_legs else spot
        gamma_flip = round((ce_top + pe_top) / 2, 2)

        return GammaProfile(
            max_gamma_strike=max_gamma_strike,
            gamma_flip=gamma_flip,
            above_flip=spot > gamma_flip,
        )

    def iv_rank(self, current_iv: float, iv_history: list[float]) -> float:
        """IV percentile rank over supplied history (0–100)."""
        if not iv_history or len(iv_history) < 5:
            return 50.0
        iv_min = min(iv_history)
        iv_max = max(iv_history)
        if iv_max == iv_min:
            return 50.0
        return round((current_iv - iv_min) / (iv_max - iv_min) * 100, 1)

    def expected_move(self, spot: float, iv: float, dte: int) -> float:
        """1-sigma expected move: spot × IV × sqrt(dte/365)."""
        if iv <= 0 or dte <= 0 or spot <= 0:
            return 0.0
        return round(spot * (iv / 100) * math.sqrt(dte / 365), 2)

    def pop_estimate(self, spot: float, strike: float, iv: float, dte: int) -> float:
        """Probability of Profit for OTM option (Black-Scholes N(d2) approximation)."""
        if iv <= 0 or dte <= 0:
            return 0.0
        sigma = (iv / 100) * math.sqrt(dte / 365)
        if sigma == 0:
            return 0.0
        d2 = math.log(spot / strike) / sigma
        return round(0.5 * (1 + math.erf(d2 / math.sqrt(2))), 4)

    # ── Private ───────────────────────────────────────────────────────────────

    def _compute_max_pain(self, chain: OptionChainSnapshot) -> float:
        """Max pain = strike that minimises total option seller payout."""
        strikes = sorted({s.strike for s in chain.strikes})
        if not strikes:
            return chain.spot_price

        min_pain  = float("inf")
        max_pain_strike = strikes[0]

        ce_by_strike = {
            s.strike: s for s in chain.strikes if s.option_type == OptionType.CALL
        }
        pe_by_strike = {
            s.strike: s for s in chain.strikes if s.option_type == OptionType.PUT
        }

        for test_price in strikes:
            ce_pain = sum(
                max(0, test_price - k) * s.oi
                for k, s in ce_by_strike.items()
            )
            pe_pain = sum(
                max(0, k - test_price) * s.oi
                for k, s in pe_by_strike.items()
            )
            total = ce_pain + pe_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = test_price

        return max_pain_strike
