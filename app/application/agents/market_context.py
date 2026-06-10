"""MarketContextAgent — classifies the current market regime.

Runs once per scan, not per candidate. Output is shared with all other agents.
"""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior Indian equity derivatives market analyst.
Classify the current market regime based on the provided data.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Current market data:
- India VIX: {vix}
- NIFTY spot: {nifty_spot}
- NIFTY 1-day change: {nifty_change_pct:.2f}%
- BANKNIFTY spot: {banknifty_spot}
- BANKNIFTY 1-day change: {banknifty_change_pct:.2f}%
- Global cue (S&P 500 prev session): {sp500_change:.2f}%
- FII DII net flow today (₹ Cr): FII {fii_flow:+.0f}, DII {dii_flow:+.0f}
- Days to next NIFTY expiry: {days_to_expiry}
- Top sector movers: {sector_summary}

Respond with JSON:
{{
  "regime": "TRENDING_BULL|TRENDING_BEAR|RANGE_BOUND|HIGH_VOL|EXPIRY_SQUEEZE",
  "confidence": 0.0-1.0,
  "note": "one sentence rationale",
  "allow_ce_buying": true/false,
  "allow_pe_buying": true/false,
  "recommended_strategy": "MOMENTUM|REVERSAL|STRADDLE|SKIP",
  "vix_regime": "CALM|ELEVATED|HIGH|EXTREME"
}}"""


class MarketContextAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(self, market_data: dict) -> AIAnalysis:
        user_prompt = _USER_TEMPLATE.format(
            vix=market_data.get("vix", 15),
            nifty_spot=market_data.get("nifty_spot", 0),
            nifty_change_pct=market_data.get("nifty_change_pct", 0),
            banknifty_spot=market_data.get("banknifty_spot", 0),
            banknifty_change_pct=market_data.get("banknifty_change_pct", 0),
            sp500_change=market_data.get("sp500_change", 0),
            fii_flow=market_data.get("fii_flow", 0),
            dii_flow=market_data.get("dii_flow", 0),
            days_to_expiry=market_data.get("days_to_expiry", 7),
            sector_summary=market_data.get("sector_summary", "N/A"),
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.05)
        except Exception as exc:
            logger.warning("MarketContextAgent failed: %s", exc)
            resp = {"regime": "RANGE_BOUND", "confidence": 0.3, "note": "AI unavailable"}

        regime     = resp.get("regime", "RANGE_BOUND")
        confidence = float(resp.get("confidence", 0.5))

        return AIAnalysis(
            agent_name="MarketContextAgent",
            score=confidence,
            confidence=confidence,
            reasoning=resp.get("note", ""),
            recommendation="NEUTRAL",
            metadata={
                "regime":               regime,
                "allow_ce_buying":      resp.get("allow_ce_buying", True),
                "allow_pe_buying":      resp.get("allow_pe_buying", True),
                "recommended_strategy": resp.get("recommended_strategy", "MOMENTUM"),
                "vix_regime":           resp.get("vix_regime", "CALM"),
            },
        )
