"""Trend alignment gate — rejects counter-trend trades (EMA20/50/200 stack)."""
from app.core.utils import trend_aligned
from app.services.gates.base import BaseGate


class TrendAlignmentGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if not trend_aligned(candidate):
            return "Trend is not aligned with trade direction."
        return None
