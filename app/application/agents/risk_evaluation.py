"""RiskEvaluationAgent — independent AI risk review of a candidate.

Separate from RiskEngine (which enforces hard rules). This agent provides
qualitative risk assessment that feeds into TradeRecommendationAgent.
"""
from __future__ import annotations
import logging
from app.application.ports.ai import IAIProvider, AIAnalysis

logger = logging.getLogger(__name__)

_SYSTEM = """You are a risk manager for an Indian proprietary trading desk.
Independently assess the risk profile of this options trade.
Be conservative — capital preservation is the primary mandate.
Respond ONLY in valid JSON with no markdown fences."""

_USER_TEMPLATE = """Risk assessment for {instrument}:

Trade parameters:
- Direction: {direction}
- Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | T1: ₹{t1:.2f}
- R:R ratio: {rr:.2f}
- Lots: {lots} | Risk/trade: ₹{trade_risk:,.0f}
- DTE: {dte} days | Theta decay rate: ₹{theta_daily:.2f}/day/lot

Portfolio context:
- Daily P&L: {daily_pnl:.1f}%
- Weekly P&L: {weekly_pnl:.1f}%
- Open positions: {open_positions}
- Loss streak: {loss_streak}
- VIX: {vix} | Regime: {regime}

Known gate failures: {gate_failures}

Identify risks unique to this setup (not just rule checks).
{{
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "recommendation": "STRONG_BUY|BUY|NEUTRAL|AVOID|STRONG_AVOID",
  "risk_level": "low|medium|high|extreme",
  "theta_risk": "negligible|low|medium|high",
  "event_risk": "none|low|medium|high",
  "concentration_risk": true/false,
  "hidden_risks": ["list of non-obvious risks"],
  "reasoning": "2-3 sentence risk summary"
}}"""


class RiskEvaluationAgent:
    def __init__(self, ai: IAIProvider) -> None:
        self._ai = ai

    async def analyse(
        self,
        candidate:       dict,
        portfolio_state: dict,
        market_context:  dict,
    ) -> AIAnalysis:
        targets = candidate.get("targets", [0, 0, 0])
        t1      = float(targets[0]) if targets else 0.0
        entry   = float(candidate.get("entry", 0) or 0)
        sl      = float(candidate.get("stopLoss", 0) or 0)
        lots    = int(candidate.get("lots", 1) or 1)
        theta   = float(candidate.get("theta", 0) or 0)
        lot_size = int(candidate.get("lotSize", 1) or 1)

        rr = abs(t1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        trade_risk   = abs(entry - sl) * lots * lot_size
        theta_daily  = abs(theta) * lot_size

        user_prompt = _USER_TEMPLATE.format(
            instrument=candidate.get("instrument", "?"),
            direction=candidate.get("direction", "BUY"),
            entry=entry, sl=sl, t1=t1,
            rr=round(rr, 2),
            lots=lots,
            trade_risk=trade_risk,
            dte=int(candidate.get("dte", 0) or 0),
            theta_daily=theta_daily,
            daily_pnl=float(portfolio_state.get("dailyPnlPct", 0) or 0),
            weekly_pnl=float(portfolio_state.get("weeklyPnlPct", 0) or 0),
            open_positions=int(portfolio_state.get("openPositions", 0) or 0),
            loss_streak=int(portfolio_state.get("lossStreak", 0) or 0),
            vix=float(market_context.get("vix", 15) or 15),
            regime=market_context.get("regime", "UNKNOWN"),
            gate_failures=", ".join(candidate.get("gateFailures", [])[:3]) or "None",
        )

        try:
            resp = await self._ai.complete(_SYSTEM, user_prompt, temperature=0.05)
        except Exception as exc:
            logger.warning("RiskEvaluationAgent failed for %s: %s",
                           candidate.get("instrument"), exc)
            resp = {"score": 0.5, "confidence": 0.3, "recommendation": "NEUTRAL"}

        return AIAnalysis(
            agent_name="RiskEvaluationAgent",
            score=float(resp.get("score", 0.5)),
            confidence=float(resp.get("confidence", 0.3)),
            reasoning=resp.get("reasoning", ""),
            recommendation=resp.get("recommendation", "NEUTRAL"),
            metadata={
                "risk_level":         resp.get("risk_level", "medium"),
                "theta_risk":         resp.get("theta_risk", "medium"),
                "event_risk":         resp.get("event_risk", "low"),
                "concentration_risk": resp.get("concentration_risk", False),
                "hidden_risks":       resp.get("hidden_risks", []),
            },
        )
