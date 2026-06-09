"""Drawdown and loss-streak gates — account-level risk controls."""
from app.services.gates.base import BaseGate


class LossStreakGate(BaseGate):
    """Stop trading after 3 consecutive losses."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        if settings["lossStreak"] >= 3:
            return "Stop-trading rule active after 3 consecutive losses."
        return None


class DailyLossGate(BaseGate):
    """Halt when today's P&L exceeds the configured daily loss limit."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        if risk_state["dailyLossPct"] >= settings["maxDailyLossPct"]:
            return "Daily loss limit reached."
        return None


class WeeklyDrawdownGate(BaseGate):
    """Halt when this week's drawdown exceeds the weekly limit."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        if risk_state["weeklyDrawdownPct"] >= settings["maxWeeklyDrawdownPct"]:
            return "Weekly drawdown limit reached."
        return None


class MonthlyDrawdownGate(BaseGate):
    """Halt when this month's drawdown exceeds the monthly limit."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        if risk_state["monthlyDrawdownPct"] >= settings["maxMonthlyDrawdownPct"]:
            return "Monthly drawdown limit reached."
        return None
