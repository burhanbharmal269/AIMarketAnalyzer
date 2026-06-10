"""NewsSentimentAgent — evaluates news sentiment for a candidate."""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are a news sentiment analyst for Indian equity markets.
Rate news sentiment and its expected impact on the stock/index price.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Sentiment analysis for {underlying}:

Recent headlines:
{headlines}

Events:
- Earnings in next {earnings_days} days: {has_earnings}
- Pending news events: {news_events}
- Sector sentiment: {sector_sentiment}
- Global cue context: {global_context}

Rate the overall sentiment and its directional bias for a {direction} trade.
{{
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "recommendation": "STRONG_BUY|BUY|NEUTRAL|AVOID|STRONG_AVOID",
  "sentiment": "positive|neutral|negative|mixed",
  "event_risk": "none|low|medium|high|extreme",
  "headline_bias": "bullish|bearish|neutral",
  "reasoning": "1-2 sentence news summary"
}}"""


class NewsSentimentAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(
        self,
        candidate: dict,
        news_headlines: list[str],
        market_context: dict,
    ) -> AIAnalysis:
        underlying = candidate.get("underlying", candidate.get("symbol", "?"))
        headlines_text = (
            "\n".join(f"- {h}" for h in news_headlines[:8])
            if news_headlines else "No headlines available."
        )

        user_prompt = _USER_TEMPLATE.format(
            underlying=underlying,
            headlines=headlines_text,
            earnings_days=candidate.get("earningsDays", "N/A"),
            has_earnings=bool(candidate.get("earningsDays", 999) <= 3),
            news_events=candidate.get("newsEvents", "None"),
            sector_sentiment=market_context.get("sector_sentiment", {}).get(
                candidate.get("sector", ""), "neutral"
            ),
            global_context=market_context.get("global_context", "mixed"),
            direction=candidate.get("direction", "BUY"),
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.1)
        except Exception as exc:
            logger.warning("NewsSentimentAgent failed for %s: %s", underlying, exc)
            resp = {"score": 0.5, "confidence": 0.2, "recommendation": "NEUTRAL"}

        return AIAnalysis(
            agent_name="NewsSentimentAgent",
            score=float(resp.get("score", 0.5)),
            confidence=float(resp.get("confidence", 0.2)),
            reasoning=resp.get("reasoning", ""),
            recommendation=resp.get("recommendation", "NEUTRAL"),
            metadata={
                "sentiment":    resp.get("sentiment", "neutral"),
                "event_risk":   resp.get("event_risk", "low"),
                "headline_bias": resp.get("headline_bias", "neutral"),
            },
        )
