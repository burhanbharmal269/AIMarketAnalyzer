"""VIX gate — blocks directional option buying in high-volatility environments."""
from app.core.constants import VIX_HARD_GATE
from app.services.gates.base import BaseGate


class VixGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if market["indiaVix"] >= VIX_HARD_GATE:
            return "India VIX is elevated beyond directional buying threshold."
        return None
