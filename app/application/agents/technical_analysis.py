"""TechnicalAnalysisAgent — validates the technical setup for a single candidate."""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert in Indian equity technical analysis specialising in options setups.
Evaluate the technical strength of the provided candidate. Be concise and precise.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Evaluate this options candidate:

Instrument: {instrument} | Direction: {direction}
Spot: ₹{spot:.2f} | Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | T1: ₹{t1:.2f}

Technicals:
- EMA 20/50/200: {ema20:.2f} / {ema50:.2f} / {ema200:.2f}
- RSI(14): {rsi:.1f}
- ADX: {adx:.1f}
- MACD hist: {macd_hist:.4f}
- ATR: {atr:.2f}
- VWAP: {vwap:.2f} | Spot above VWAP: {above_vwap}
- Supertrend bullish: {supertrend}
- 15-min aligned: {tf15}
- ORB breakout: {orb} | S/R breakout: {sr}

Score each criterion 0-10. Respond with:
{{
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "recommendation": "STRONG_BUY|BUY|NEUTRAL|AVOID|STRONG_AVOID",
  "trend_alignment": true/false,
  "momentum_quality": "strong|moderate|weak|adverse",
  "key_risk": "one-line risk",
  "reasoning": "2-3 sentence technical summary"
}}"""


class TechnicalAnalysisAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(self, candidate: dict, market_context: dict) -> AIAnalysis:
        targets = candidate.get("targets", [0, 0, 0])
        t1      = targets[0] if targets else 0

        user_prompt = _USER_TEMPLATE.format(
            instrument=candidate.get("instrument", "?"),
            direction=candidate.get("direction", "BUY"),
            spot=float(candidate.get("spotPrice", 0) or 0),
            entry=float(candidate.get("entry", 0) or 0),
            sl=float(candidate.get("stopLoss", 0) or 0),
            t1=float(t1),
            ema20=float(candidate.get("ema20", 0) or 0),
            ema50=float(candidate.get("ema50", 0) or 0),
            ema200=float(candidate.get("ema200", 0) or 0),
            rsi=float(candidate.get("rsi", 50) or 50),
            adx=float(candidate.get("adx", 0) or 0),
            macd_hist=float(candidate.get("macdHist", 0) or 0),
            atr=float(candidate.get("atr", 0) or 0),
            vwap=float(candidate.get("vwap", 0) or 0),
            above_vwap=candidate.get("vwapConfirmed", False),
            supertrend=candidate.get("supertrendBull", False),
            tf15=candidate.get("tf15Aligned", False),
            orb=candidate.get("orbBreakout", False),
            sr=candidate.get("srBreakout", False),
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.05)
        except Exception as exc:
            logger.warning("TechnicalAnalysisAgent failed for %s: %s",
                           candidate.get("instrument"), exc)
            resp = {"score": 0.5, "confidence": 0.3, "recommendation": "NEUTRAL"}

        return AIAnalysis(
            agent_name="TechnicalAnalysisAgent",
            score=float(resp.get("score", 0.5)),
            confidence=float(resp.get("confidence", 0.3)),
            reasoning=resp.get("reasoning", ""),
            recommendation=resp.get("recommendation", "NEUTRAL"),
            metadata={
                "trend_alignment":  resp.get("trend_alignment", False),
                "momentum_quality": resp.get("momentum_quality", "weak"),
                "key_risk":         resp.get("key_risk", ""),
            },
        )
