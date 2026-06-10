"""OptionsAnalysisAgent — evaluates option chain metrics for a candidate."""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are a specialist in Indian F&O options analytics.
Evaluate the options chain data for this candidate and rate its quality.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Options chain analysis for {instrument} | Direction: {direction}

Option chain metrics:
- ATM IV: {atm_iv:.1f}%
- IV Rank: {iv_rank}
- PCR (OI): {pcr:.2f}
- Max Pain: ₹{max_pain:.0f} (spot: ₹{spot:.0f}, distance: {mp_dist:.1f}%)
- OI change: {oi_change:+.1f}%
- Option volume: {option_volume:,.0f}
- Bid-ask spread: {spread_pct:.2f}%
- Delta: {delta:.3f}
- Theta: {theta:.4f}/day
- Vega: {vega:.4f}
- CE resistance wall: ₹{ce_wall:.0f}
- PE support wall: ₹{pe_wall:.0f}
- Regime: {regime}

Evaluate: IV environment, OI positioning, Greeks risk, spread liquidity.
{{
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "recommendation": "STRONG_BUY|BUY|NEUTRAL|AVOID|STRONG_AVOID",
  "iv_environment": "cheap|fair|expensive",
  "oi_bias": "bullish|bearish|neutral",
  "liquidity_ok": true/false,
  "greeks_risk": "low|medium|high",
  "reasoning": "2-3 sentence options summary"
}}"""


class OptionsAnalysisAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(self, candidate: dict, market_context: dict) -> AIAnalysis:
        spot     = float(candidate.get("spotPrice", 0) or 0)
        max_pain = float(candidate.get("maxPain", 0) or 0)
        mp_dist  = abs(spot - max_pain) / spot * 100 if spot > 0 and max_pain > 0 else 0.0
        iv_rank  = candidate.get("ivRank")
        iv_rank_str = f"{iv_rank:.0f}%" if iv_rank is not None else "N/A"

        user_prompt = _USER_TEMPLATE.format(
            instrument=candidate.get("instrument", "?"),
            direction=candidate.get("direction", "BUY"),
            atm_iv=float(candidate.get("atmIv", 0) or 0),
            iv_rank=iv_rank_str,
            pcr=float(candidate.get("pcr", 1.0) or 1.0),
            max_pain=max_pain,
            spot=spot,
            mp_dist=mp_dist,
            oi_change=float(candidate.get("oiChangePct", 0) or 0),
            option_volume=float(candidate.get("optionVolume", 0) or 0),
            spread_pct=float(candidate.get("spreadPct", 0) or 0),
            delta=float(candidate.get("delta", 0) or 0),
            theta=float(candidate.get("theta", 0) or 0),
            vega=float(candidate.get("vega", 0) or 0),
            ce_wall=float(candidate.get("ceWall", 0) or 0),
            pe_wall=float(candidate.get("peWall", 0) or 0),
            regime=market_context.get("regime", "UNKNOWN"),
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.05)
        except Exception as exc:
            logger.warning("OptionsAnalysisAgent failed for %s: %s",
                           candidate.get("instrument"), exc)
            resp = {"score": 0.5, "confidence": 0.3, "recommendation": "NEUTRAL"}

        return AIAnalysis(
            agent_name="OptionsAnalysisAgent",
            score=float(resp.get("score", 0.5)),
            confidence=float(resp.get("confidence", 0.3)),
            reasoning=resp.get("reasoning", ""),
            recommendation=resp.get("recommendation", "NEUTRAL"),
            metadata={
                "iv_environment": resp.get("iv_environment", "fair"),
                "oi_bias":        resp.get("oi_bias", "neutral"),
                "liquidity_ok":   resp.get("liquidity_ok", True),
                "greeks_risk":    resp.get("greeks_risk", "medium"),
            },
        )
