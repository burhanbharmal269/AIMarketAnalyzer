"""Minimum risk/reward gate — rejects setups where T1 is too close to entry."""
from app.services.gates.base import BaseGate

_MIN_RR = 1.5


class MinRRGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if candidate["rr"] < _MIN_RR:
            return "Risk reward is below 1:1.5 (S/R target too close to entry)."
        return None
