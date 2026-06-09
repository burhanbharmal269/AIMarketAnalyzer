"""Spread gate — rejects options with excessive bid-ask spread."""
from app.services.gates.base import BaseGate


class SpreadGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if candidate["spreadPct"] > settings["maxSpread"]:
            return "Bid-ask spread is excessive."
        return None
