"""TradeRecommendationAgent — final trade decision synthesiser.

Receives outputs from all other agents and the candidate data.
Produces the final recommendation with entry-exit-sizing guidance.
"""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior proprietary options trader with 15 years of NSE F&O experience.
Synthesise the analysis from multiple specialist agents to produce a final trading recommendation.
Be decisive. If the setup is not excellent, say AVOID.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Final trade recommendation synthesis:

Candidate: {instrument}
Direction: {direction} | Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | T1: ₹{t1:.2f} | T2: ₹{t2:.2f}
Score: {score}/100 | Grade: {grade}
DTE: {dte}d | Lots: {lots} | Risk/lot: ₹{lot_risk:,.0f}

Agent verdicts:
- Market Context: {regime} | Strategy: {recommended_strategy}
- Technical: {tech_rec} (score: {tech_score:.2f})
- Options:   {opt_rec}  (score: {opt_score:.2f}, IV: {iv_env}, OI bias: {oi_bias})
- Sentiment: {sent_rec} (score: {sent_score:.2f}, risk: {event_risk})
- Risk:      {risk_rec} (score: {risk_score:.2f})

Gate failures: {gate_failures}

Produce the final recommendation. Only recommend BUY/STRONG_BUY if all agents broadly agree.
{{
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "recommendation": "STRONG_BUY|BUY|NEUTRAL|AVOID|STRONG_AVOID",
  "grade": "A+|A|B|C|D",
  "entry_note": "entry timing/confirmation note",
  "exit_plan": "SL/target management note",
  "sizing_note": "position sizing advice",
  "key_thesis": "one sentence trade thesis",
  "reasoning": "3-4 sentence comprehensive summary"
}}"""


class TradeRecommendationAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(
        self,
        candidate:       dict,
        agent_analyses:  dict,   # agent_name → AIAnalysis
        market_context:  dict,
    ) -> AIAnalysis:
        targets = candidate.get("targets", [0, 0, 0])
        t1 = float(targets[0]) if len(targets) > 0 else 0.0
        t2 = float(targets[1]) if len(targets) > 1 else 0.0

        tech  = agent_analyses.get("TechnicalAnalysisAgent")
        opt   = agent_analyses.get("OptionsAnalysisAgent")
        sent  = agent_analyses.get("NewsSentimentAgent")
        risk  = agent_analyses.get("RiskEvaluationAgent")

        score_dict = candidate.get("score", {})
        total_score = score_dict.get("total", 0) if isinstance(score_dict, dict) else 0
        grade = "A" if total_score >= 85 else "B" if total_score >= 70 else "C"

        user_prompt = _USER_TEMPLATE.format(
            instrument=candidate.get("instrument", "?"),
            direction=candidate.get("direction", "BUY"),
            entry=float(candidate.get("entry", 0) or 0),
            sl=float(candidate.get("stopLoss", 0) or 0),
            t1=t1, t2=t2,
            score=total_score,
            grade=grade,
            dte=int(candidate.get("dte", 0) or 0),
            lots=int(candidate.get("lots", 1) or 1),
            lot_risk=float(candidate.get("lotRisk", 0) or 0),
            regime=market_context.get("regime", "UNKNOWN"),
            recommended_strategy=market_context.get("recommended_strategy", "MOMENTUM"),
            tech_rec=tech.recommendation  if tech  else "N/A",
            tech_score=tech.score         if tech  else 0.5,
            opt_rec=opt.recommendation    if opt   else "N/A",
            opt_score=opt.score           if opt   else 0.5,
            iv_env=opt.metadata.get("iv_environment", "fair") if opt else "N/A",
            oi_bias=opt.metadata.get("oi_bias", "neutral")    if opt else "N/A",
            sent_rec=sent.recommendation  if sent  else "N/A",
            sent_score=sent.score         if sent  else 0.5,
            event_risk=sent.metadata.get("event_risk", "low") if sent else "N/A",
            risk_rec=risk.recommendation  if risk  else "N/A",
            risk_score=risk.score         if risk  else 0.5,
            gate_failures=", ".join(candidate.get("gateFailures", [])[:3]) or "None",
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.05)
        except Exception as exc:
            logger.warning("TradeRecommendationAgent failed for %s: %s",
                           candidate.get("instrument"), exc)
            resp = {
                "score": 0.5, "confidence": 0.3,
                "recommendation": "NEUTRAL", "grade": grade,
            }

        return AIAnalysis(
            agent_name="TradeRecommendationAgent",
            score=float(resp.get("score", 0.5)),
            confidence=float(resp.get("confidence", 0.3)),
            reasoning=resp.get("reasoning", ""),
            recommendation=resp.get("recommendation", "NEUTRAL"),
            metadata={
                "grade":        resp.get("grade", grade),
                "entry_note":   resp.get("entry_note", ""),
                "exit_plan":    resp.get("exit_plan", ""),
                "sizing_note":  resp.get("sizing_note", ""),
                "key_thesis":   resp.get("key_thesis", ""),
            },
        )
