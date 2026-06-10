"""AIOrchestrator — runs all specialist agents in parallel and aggregates results.

Agent execution order:
  1. MarketContextAgent runs once — its output feeds all per-candidate agents
  2. Per candidate (parallel): TechnicalAnalysis, OptionsAnalysis, NewsSentiment, RiskEvaluation
  3. TradeRecommendationAgent runs after (depends on 2)

Weights for composite score:
  Technical:   35%
  Options:     30%
  Sentiment:   15%
  Risk:        20%
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import asdict

from app.application.ports.ai import IAIProvider, AIRecommendation, AIAnalysis
from app.application.ports.news import INewsProvider
from app.application.agents.market_context import MarketContextAgent
from app.application.agents.technical_analysis import TechnicalAnalysisAgent
from app.application.agents.options_analysis import OptionsAnalysisAgent
from app.application.agents.news_sentiment import NewsSentimentAgent
from app.application.agents.trade_recommendation import TradeRecommendationAgent
from app.application.agents.risk_evaluation import RiskEvaluationAgent

logger = logging.getLogger(__name__)

_AGENT_WEIGHTS = {
    "TechnicalAnalysisAgent":   0.35,
    "OptionsAnalysisAgent":     0.30,
    "RiskEvaluationAgent":      0.20,
    "NewsSentimentAgent":       0.15,
}

_REC_SCORES = {
    "STRONG_BUY":   1.0,
    "BUY":          0.75,
    "NEUTRAL":      0.5,
    "AVOID":        0.25,
    "STRONG_AVOID": 0.0,
}


class AIOrchestrator:
    def __init__(
        self,
        ai:    IAIProvider,
        news:  INewsProvider | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self._ai   = ai
        self._news = news
        self._sem  = asyncio.Semaphore(max_concurrency)

        self._market_ctx_agent = MarketContextAgent(ai)
        self._tech_agent       = TechnicalAnalysisAgent(ai)
        self._opt_agent        = OptionsAnalysisAgent(ai)
        self._sent_agent       = NewsSentimentAgent(ai)
        self._trade_agent      = TradeRecommendationAgent(ai)
        self._risk_agent       = RiskEvaluationAgent(ai)

    async def analyse_candidates(
        self,
        candidates:      list[dict],
        settings:        dict,
        portfolio_state: dict | None = None,
        market_raw:      dict | None = None,
    ) -> list[dict]:
        """Analyse all candidates. Returns enriched list with AI scores."""
        if not candidates:
            return []

        # ── 1. Market context (once, shared) ──────────────────────────────────
        ctx_analysis = await self._market_ctx_agent.analyse(market_raw or {})
        market_context = ctx_analysis.metadata
        market_context["regime"]       = market_context.get("regime", "RANGE_BOUND")
        market_context["vix"]          = (market_raw or {}).get("vix", 15)
        market_context["global_context"] = "mixed"

        portfolio_state = portfolio_state or {}
        results: list[dict] = []

        # ── 2. Per-candidate analysis (parallel, bounded) ──────────────────────
        tasks = [
            self._analyse_one(c, market_context, portfolio_state)
            for c in candidates
        ]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(enriched):
            if isinstance(result, Exception):
                logger.warning(
                    "AIOrchestrator failed for candidate %d: %s", i, result
                )
                results.append(candidates[i])
            else:
                results.append(result)

        return results

    async def _analyse_one(
        self,
        candidate:       dict,
        market_context:  dict,
        portfolio_state: dict,
    ) -> dict:
        """Run all per-candidate agents under the concurrency semaphore."""
        async with self._sem:
            instrument = candidate.get("instrument", "?")
            logger.debug("AI analysis: %s", instrument)

            # Fetch news headlines if provider available
            headlines: list[str] = []
            if self._news:
                try:
                    underlying = candidate.get("underlying", "")
                    news_items = await self._news.get_headlines(underlying, max_results=8)
                    headlines = [n.title for n in news_items]
                except Exception as exc:
                    logger.debug("News fetch failed for %s: %s", instrument, exc)

            # Run 4 agents in parallel
            tech_task  = self._tech_agent.analyse(candidate, market_context)
            opt_task   = self._opt_agent.analyse(candidate, market_context)
            sent_task  = self._sent_agent.analyse(candidate, headlines, market_context)
            risk_task  = self._risk_agent.analyse(candidate, portfolio_state, market_context)

            tech, opt, sent, risk = await asyncio.gather(
                tech_task, opt_task, sent_task, risk_task,
                return_exceptions=True,
            )

            # Replace exceptions with neutral stubs
            tech  = tech  if isinstance(tech,  AIAnalysis) else self._stub("TechnicalAnalysisAgent")
            opt   = opt   if isinstance(opt,   AIAnalysis) else self._stub("OptionsAnalysisAgent")
            sent  = sent  if isinstance(sent,  AIAnalysis) else self._stub("NewsSentimentAgent")
            risk  = risk  if isinstance(risk,  AIAnalysis) else self._stub("RiskEvaluationAgent")

            agent_analyses = {a.agent_name: a for a in [tech, opt, sent, risk]}

            # ── 3. Trade recommendation (sequential — depends on all 4) ───────
            try:
                trade_rec = await self._trade_agent.analyse(
                    candidate, agent_analyses, market_context
                )
            except Exception as exc:
                logger.warning("TradeRecommendationAgent error for %s: %s", instrument, exc)
                trade_rec = self._stub("TradeRecommendationAgent")

            # ── 4. Aggregate score ─────────────────────────────────────────────
            composite = self._composite_score(agent_analyses)
            final_rec = trade_rec.recommendation

            enriched = dict(candidate)
            enriched.update({
                "aiScore":       round(composite * 100, 1),
                "aiGrade":       trade_rec.metadata.get("grade", ""),
                "aiRec":         final_rec,
                "explanation":   trade_rec.reasoning or candidate.get("explanation", ""),
                "entryNote":     trade_rec.metadata.get("entry_note", ""),
                "exitPlan":      trade_rec.metadata.get("exit_plan", ""),
                "keyThesis":     trade_rec.metadata.get("key_thesis", ""),
                "agentAnalyses": {
                    k: {
                        "score":         round(v.score, 3),
                        "confidence":    round(v.confidence, 3),
                        "recommendation": v.recommendation,
                        "reasoning":     v.reasoning[:200] if v.reasoning else "",
                    }
                    for k, v in agent_analyses.items()
                },
                "marketRegime":  market_context.get("regime", ""),
            })
            return enriched

    def _composite_score(self, analyses: dict[str, AIAnalysis]) -> float:
        total_weight = 0.0
        weighted_sum = 0.0
        for name, weight in _AGENT_WEIGHTS.items():
            a = analyses.get(name)
            if a:
                weighted_sum += a.score * weight * a.confidence
                total_weight += weight * a.confidence
        return round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.5

    @staticmethod
    def _stub(name: str) -> AIAnalysis:
        return AIAnalysis(
            agent_name=name,
            score=0.5, confidence=0.2,
            reasoning="Agent unavailable",
            recommendation="NEUTRAL",
        )
