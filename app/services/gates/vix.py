"""VIX gate — blocks directional option buying in extreme-fear environments."""
from app.core.constants import VIX_HARD_GATE
from app.services.gates.base import BaseGate


class VixGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        vix = market["indiaVix"]
        if vix >= VIX_HARD_GATE:
            return (
                f"India VIX {vix:.1f} ≥ {VIX_HARD_GATE} — extreme fear environment. "
                "Option premiums are severely dislocated; directional buying has "
                "unfavourable risk-reward until VIX settles below 22."
            )
        return None
