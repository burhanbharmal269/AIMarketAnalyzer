"""Event risk gate — blocks signals near earnings, board meetings, or major events."""
from app.services.gates.base import BaseGate


class EventRiskGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if candidate.get("eventRisk"):
            return "Earnings / board meeting scheduled within 2 days — event risk."

        event_window = settings["eventWindow"]
        for event in market.get("eventCalendar", []):
            if event["severity"] == "high" and event["minutesAway"] <= event_window:
                return "Major market event risk is too close (RBI MPC, expiry, or flagged event)."

        return None
